import secrets
import smtplib
from datetime import timedelta
from email.message import EmailMessage
from ipaddress import ip_address
from urllib.parse import quote
from redis.exceptions import RedisError
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, status
from jose import JWTError
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.security import create_token, decode_token, hash_password, verify_password
from app.deps import CurrentUser, DbSession
from app.models import AuditLog, Entity, Group, GroupMember, Profile, User
from app.schemas import (
    InvitationAccept,
    InvitationAccepted,
    InvitationCreate,
    InvitationCreated,
    InvitationOptions,
    InvitationProfile,
    InvitationTeam,
    LoginRequest,
    ManagedUserRead,
    MeRead,
    OwnEntity,
    RefreshRequest,
    TokenPair,
    UserActivationUpdate,
)
from app.services.email import send_email

router = APIRouter(prefix="/auth", tags=["auth"])
_consume_invitation_script = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""


def _require_invitation_admin(user: User) -> None:
    if (user.profile.rights or {}).get("*") is not True:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Administrator rights required")


def _invitation_email(user: User, token: str) -> EmailMessage:
    settings = get_settings()
    message = EmailMessage()
    message["From"] = settings.mail_from
    message["To"] = user.email
    message["Subject"] = "Your SVC ServiceFlow invitation"
    link = f"{settings.app_base_url}/?invite={quote(token, safe='')}"
    message.set_content(
        f"Hello {user.name},\n\n"
        "You have been invited to SVC ServiceFlow. Use this link to set your password:\n\n"
        f"{link}\n\n"
        f"This link expires in {settings.invitation_ttl_hours} hours and can be used once.\n"
        "If you were not expecting this invitation, you can ignore this email.\n"
    )
    return message


async def _issue_invitation(user: User) -> None:
    settings = get_settings()
    token = create_token(
        str(user.id),
        user.profile.name if user.profile else "",
        "invitation",
        timedelta(hours=settings.invitation_ttl_hours),
    )
    token_id = decode_token(token)["jti"]
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        await redis.setex(
            f"invitation:user:{user.id}",
            settings.invitation_ttl_hours * 3600,
            token_id,
        )
    except RedisError as exc:
        raise HTTPException(status_code=503, detail="Invitation service temporarily unavailable") from exc
    finally:
        await redis.aclose()
    try:
        send_email(_invitation_email(user, token))
    except (OSError, smtplib.SMTPException) as exc:
        raise HTTPException(status_code=503, detail="Invitation email could not be sent; resend it after email service is available") from exc

def _client_ip(request: Request) -> str:
    settings = get_settings()
    direct = request.client.host if request.client else "unknown"
    if settings.trusted_proxy_enabled and direct in settings.trusted_proxy_ip_list:
        forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        if forwarded:
            try:
                ip_address(forwarded)
                return forwarded
            except ValueError:
                pass
    return direct


@router.post("/login", response_model=TokenPair)
async def login(request: Request, payload: LoginRequest, db: DbSession) -> TokenPair:
    settings = get_settings()
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    key = f"auth:login:{_client_ip(request)}:{payload.email.lower()}"
    try:
        try:
            attempts = await redis.incr(key)
            if attempts == 1:
                await redis.expire(key, settings.auth_rate_window_seconds)
            if attempts > settings.auth_rate_limit:
                raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Unable to authenticate")
        except RedisError as exc:
            raise HTTPException(status_code=503, detail="Authentication temporarily unavailable") from exc
    finally:
        await redis.aclose()
    user = await db.scalar(select(User).options(selectinload(User.profile)).where(User.email == payload.email.lower()))
    if user is None or not user.is_active or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    family = str(uuid4())
    return TokenPair(
        access_token=create_token(str(user.id), user.profile.name, "access", timedelta(minutes=settings.access_token_minutes), family),
        refresh_token=create_token(str(user.id), user.profile.name, "refresh", timedelta(days=settings.refresh_token_days), family),
    )


@router.post("/refresh", response_model=TokenPair)
async def refresh(payload: RefreshRequest) -> TokenPair:
    settings = get_settings()
    try:
        claims = decode_token(payload.refresh_token)
    except JWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication credentials") from exc
    if claims.get("type") != "refresh" or not claims.get("sub") or not claims.get("jti"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication credentials")
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        if await redis.get(f"revoked:{claims['jti']}"):
            if claims.get("family"):
                await redis.setex(f"revoked-family:{claims['family']}", settings.refresh_token_days * 86400, "1")
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication credentials")
        await redis.setex(f"revoked:{claims['jti']}", settings.refresh_token_days * 86400, "1")
        if claims.get("family") and await redis.get(f"revoked-family:{claims['family']}"):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication credentials")
        family = str(claims.get("family") or uuid4())
    finally:
        await redis.aclose()
    return TokenPair(
        access_token=create_token(str(claims["sub"]), str(claims.get("role", "")), "access", timedelta(minutes=settings.access_token_minutes)),
        refresh_token=create_token(str(claims["sub"]), str(claims.get("role", "")), "refresh", timedelta(days=settings.refresh_token_days), family),
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(payload: RefreshRequest) -> None:
    settings = get_settings()
    try:
        claims = decode_token(payload.refresh_token)
    except JWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication credentials") from exc
    if claims.get("type") != "refresh" or not claims.get("jti"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication credentials")
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        await redis.setex(f"revoked:{claims['jti']}", settings.refresh_token_days * 86400, "1")
    finally:
        await redis.aclose()


@router.get("/me", response_model=MeRead)
async def me(user: CurrentUser, db: DbSession) -> MeRead:
    entity = await db.get(Entity, user.entity_id)
    result = MeRead.model_validate(user)
    result.entity = OwnEntity(id=entity.id, name=entity.name) if entity else None
    return result


@router.get("/invitations/options", response_model=InvitationOptions)
async def invitation_options(user: CurrentUser, db: DbSession) -> InvitationOptions:
    _require_invitation_admin(user)
    profiles = await db.scalars(select(Profile).order_by(Profile.name))
    groups = await db.scalars(
        select(Group).where(Group.entity_id == user.entity_id).order_by(Group.name)
    )
    return InvitationOptions(
        profiles=[InvitationProfile(id=item.id, name=item.name, interface=item.interface) for item in profiles],
        teams=[InvitationTeam(id=item.id, name=item.name) for item in groups],
    )


@router.get("/users", response_model=list[ManagedUserRead])
async def list_users(user: CurrentUser, db: DbSession) -> list[User]:
    _require_invitation_admin(user)
    result = await db.scalars(
        select(User)
        .options(selectinload(User.profile))
        .where(User.entity_id == user.entity_id)
        .order_by(User.name, User.id)
    )
    return list(result)


@router.post("/invitations", response_model=InvitationCreated, status_code=status.HTTP_201_CREATED)
async def invite_user(payload: InvitationCreate, user: CurrentUser, db: DbSession) -> InvitationCreated:
    _require_invitation_admin(user)
    email = str(payload.email).lower()
    if await db.scalar(select(User.id).where(User.email == email)) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An account with this email already exists")

    profile = await db.get(Profile, payload.profile_id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Profile not found")
    if not payload.name.strip():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Name cannot be blank")
    team_ids = set(payload.team_ids)
    if len(team_ids) != len(payload.team_ids):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Duplicate teams are not allowed")
    groups = list(
        await db.scalars(
            select(Group).where(Group.id.in_(team_ids), Group.entity_id == user.entity_id)
        )
    ) if team_ids else []
    if len(groups) != len(team_ids):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Team not found in your entity")
    if team_ids and profile.interface != "helpdesk":
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Only helpdesk users can be assigned to teams")

    invited = User(
        email=email,
        name=payload.name.strip(),
        profile_id=profile.id,
        entity_id=user.entity_id,
        password_hash=hash_password(secrets.token_urlsafe(32)),
        is_active=False,
    )
    db.add(invited)
    await db.flush()
    db.add_all([GroupMember(group_id=group.id, user_id=invited.id) for group in groups])
    db.add(
        AuditLog(
            entity="user",
            entity_id=invited.id,
            action="invited",
            actor_id=user.id,
            diff={"email": email, "profile_id": profile.id},
        )
    )
    await db.commit()
    await db.refresh(invited)
    invited.profile = profile
    await _issue_invitation(invited)
    return InvitationCreated(id=invited.id, email=invited.email, is_active=invited.is_active)


@router.post("/invitations/{user_id}/resend", response_model=InvitationCreated)
async def resend_invitation(user_id: int, user: CurrentUser, db: DbSession) -> InvitationCreated:
    _require_invitation_admin(user)
    invited = await db.scalar(
        select(User)
        .options(selectinload(User.profile))
        .where(User.id == user_id, User.entity_id == user.entity_id)
    )
    if invited is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if invited.is_active:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Active accounts cannot be re-invited")
    accepted = await db.scalar(
        select(AuditLog.id).where(
            AuditLog.entity == "user",
            AuditLog.entity_id == invited.id,
            AuditLog.action == "invitation_accepted",
        )
    )
    if accepted is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only pending invitations can be resent")
    await _issue_invitation(invited)
    return InvitationCreated(id=invited.id, email=invited.email, is_active=invited.is_active)


@router.post("/invitations/accept", response_model=InvitationAccepted)
async def accept_invitation(payload: InvitationAccept, db: DbSession) -> InvitationAccepted:
    try:
        claims = decode_token(payload.token)
    except JWTError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invitation is invalid or expired") from exc
    if claims.get("type") != "invitation" or not claims.get("sub") or not claims.get("jti"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invitation is invalid or expired")
    try:
        user_id = int(claims["sub"])
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invitation is invalid or expired") from exc

    redis = Redis.from_url(get_settings().redis_url, decode_responses=True)
    try:
        consumed = await redis.eval(
            _consume_invitation_script,
            1,
            f"invitation:user:{user_id}",
            str(claims["jti"]),
        )
    except RedisError as exc:
        raise HTTPException(status_code=503, detail="Invitation service temporarily unavailable") from exc
    finally:
        await redis.aclose()
    if consumed != 1:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invitation is invalid, expired, or already used")

    invited = await db.get(User, user_id)
    if invited is None or invited.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invitation is invalid, expired, or already used")
    invited.password_hash = hash_password(payload.password)
    invited.is_active = True
    db.add(
        AuditLog(
            entity="user",
            entity_id=invited.id,
            action="invitation_accepted",
            actor_id=invited.id,
            diff={},
        )
    )
    await db.commit()
    return InvitationAccepted(email=invited.email)


@router.patch("/users/{user_id}", response_model=ManagedUserRead)
async def update_user_activation(
    user_id: int,
    payload: UserActivationUpdate,
    user: CurrentUser,
    db: DbSession,
) -> User:
    _require_invitation_admin(user)
    if user_id == user.id and not payload.is_active:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="You cannot deactivate your own account")
    target = await db.scalar(
        select(User)
        .options(selectinload(User.profile))
        .where(User.id == user_id, User.entity_id == user.entity_id)
    )
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if payload.is_active and not target.is_active:
        accepted = await db.scalar(
            select(AuditLog.id).where(
                AuditLog.entity == "user",
                AuditLog.entity_id == target.id,
                AuditLog.action == "invitation_accepted",
            )
        )
        if accepted is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Pending accounts must be activated through their invitation link",
            )
    target.is_active = payload.is_active
    db.add(
        AuditLog(
            entity="user",
            entity_id=target.id,
            action="activated" if payload.is_active else "deactivated",
            actor_id=user.id,
            diff={"is_active": payload.is_active},
        )
    )
    await db.commit()
    await db.refresh(target)
    return target
