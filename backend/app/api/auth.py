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
from app.core.kv import Redis, get_kv
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
_claim_invitation_script = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then
    return 0
end
if redis.call('SET', KEYS[2], ARGV[1], 'NX', 'EX', ARGV[2]) then
    return 1
end
return 0
"""
_replace_invitation_script = """
redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
redis.call('DEL', KEYS[2])
return 1
"""
_consume_invitation_script = """
if redis.call('GET', KEYS[1]) == ARGV[1] and redis.call('GET', KEYS[2]) == ARGV[1] then
    local ttl = redis.call('TTL', KEYS[1])
    if ttl > 0 then
        redis.call('DEL', KEYS[1])
        return ttl
    end
end
return 0
"""
_restore_invitation_script = """
if redis.call('GET', KEYS[2]) == ARGV[1] then
    if redis.call('EXISTS', KEYS[1]) == 0 then
        redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
    end
    redis.call('DEL', KEYS[2])
    return 1
end
return 0
"""
_release_invitation_claim_script = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""
_deactivate_user_script = """
redis.call('DEL', KEYS[1], KEYS[2])
redis.call('SET', KEYS[3], ARGV[1], 'EX', ARGV[2])
return 1
"""
_verify_invitation_claim_script = """
if redis.call('GET', KEYS[1]) == ARGV[1] and redis.call('GET', KEYS[2]) == ARGV[1] then
    return 1
end
return 0
"""
_USER_LIFECYCLE_ACTIONS = (
    "invited",
    "invitation_accepted",
    "invitation_cancelled",
    "activated",
    "deactivated",
)
_INVALID_INVITATION_DETAIL = "Invitation is invalid, expired, or cancelled"


def _require_invitation_admin(user: User) -> None:
    rights = user.profile.rights or {}
    if rights.get("*") is not True and rights.get("users:invite") is not True:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Administrator rights required")


def can_grant_profile(inviter: User, profile: Profile) -> bool:
    inviter_rights = inviter.profile.rights or {}
    profile_rights = profile.rights or {}
    if profile_rights.get("*") is True:
        return inviter_rights.get("*") is True
    if inviter_rights.get("*") is True:
        return True
    return all(
        enabled is not True or inviter_rights.get(right) is True
        for right, enabled in profile_rights.items()
        if right != "*"
    )


async def _increment_limiter(
    redis: Redis,
    key: str,
    limit: int,
    window_seconds: int,
    detail: str = "Too many attempts; try again later",
) -> None:
    attempts = await redis.incr(key)
    if attempts == 1:
        await redis.expire(key, window_seconds)
    if attempts > limit:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=detail)


async def _enforce_rate_limit(key: str, limit: int, window_seconds: int, message: str) -> None:
    redis = Redis.from_url(get_settings().redis_url, decode_responses=True)
    try:
        try:
            await _increment_limiter(redis, key, limit, window_seconds)
        except RedisError as exc:
            raise HTTPException(status_code=503, detail=message) from exc
    finally:
        await redis.aclose()


async def _latest_lifecycle_action(db: DbSession, user_id: int) -> str | None:
    return await db.scalar(
        select(AuditLog.action)
        .where(
            AuditLog.entity == "user",
            AuditLog.entity_id == user_id,
            AuditLog.action.in_(_USER_LIFECYCLE_ACTIONS),
        )
        .order_by(AuditLog.ts.desc(), AuditLog.id.desc())
        .limit(1)
    )


def _managed_user_status(user: User, latest_action: str | None) -> str:
    if user.is_active:
        return "Active"
    return "Pending" if latest_action == "invited" else "Deactivated"


def _managed_user_read(user: User, latest_action: str | None) -> ManagedUserRead:
    return ManagedUserRead.model_validate(user).model_copy(
        update={"status": _managed_user_status(user, latest_action)}
    )


def _invitation_email(user: User, token: str) -> EmailMessage:
    settings = get_settings()
    message = EmailMessage()
    message["From"] = settings.mail_from
    message["To"] = user.email
    message["Subject"] = "Your SVC ServiceFlow invitation"
    link = f"{settings.app_base_url}/#invite={quote(token, safe='')}"
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
        await redis.eval(
            _replace_invitation_script,
            2,
            f"invitation:user:{user.id}",
            f"invitation:claim:user:{user.id}",
            token_id,
            settings.invitation_ttl_hours * 3600,
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
            await _increment_limiter(
                redis,
                key,
                settings.auth_rate_limit,
                settings.auth_rate_window_seconds,
                "Unable to authenticate",
            )
        except RedisError as exc:
            raise HTTPException(status_code=503, detail="Authentication temporarily unavailable") from exc
    finally:
        await redis.aclose()
    user = await db.scalar(select(User).options(selectinload(User.profile)).where(User.email == payload.email.lower()))
    if user is None or not user.is_active or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        session_epoch = await redis.get(f"auth:user-epoch:{user.id}")
    except RedisError as exc:
        raise HTTPException(status_code=503, detail="Authentication temporarily unavailable") from exc
    finally:
        await redis.aclose()
    db.add(
        AuditLog(
            entity="user",
            entity_id=user.id,
            action="login",
            actor_id=user.id,
            diff={},
        )
    )
    await db.commit()
    family = str(uuid4())
    return TokenPair(
        access_token=create_token(
            str(user.id),
            user.profile.name,
            "access",
            timedelta(minutes=settings.access_token_minutes),
            family,
            session_epoch,
        ),
        refresh_token=create_token(
            str(user.id),
            user.profile.name,
            "refresh",
            timedelta(days=settings.refresh_token_days),
            family,
            session_epoch,
        ),
    )


@router.post("/refresh", response_model=TokenPair)
async def refresh(payload: RefreshRequest, db: DbSession) -> TokenPair:
    settings = get_settings()
    try:
        claims = decode_token(payload.refresh_token)
    except JWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication credentials") from exc
    if claims.get("type") != "refresh" or not claims.get("sub") or not claims.get("jti"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication credentials")
    try:
        user_id = int(claims["sub"])
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication credentials") from exc
    user = await db.scalar(
        select(User)
        .options(selectinload(User.profile))
        .where(User.id == user_id, User.is_active.is_(True))
    )
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication credentials")
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        epoch = await redis.get(f"auth:user-epoch:{user_id}")
        if epoch is not None and claims.get("session_epoch") != epoch:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication credentials")
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
        access_token=create_token(
            str(user.id),
            user.profile.name,
            "access",
            timedelta(minutes=settings.access_token_minutes),
            family,
            epoch,
        ),
        refresh_token=create_token(
            str(user.id),
            user.profile.name,
            "refresh",
            timedelta(days=settings.refresh_token_days),
            family,
            epoch,
        ),
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
        profiles=[
            InvitationProfile(id=item.id, name=item.name, interface=item.interface)
            for item in profiles
            if can_grant_profile(user, item)
        ],
        teams=[InvitationTeam(id=item.id, name=item.name) for item in groups],
    )


@router.get("/users", response_model=list[ManagedUserRead])
async def list_users(user: CurrentUser, db: DbSession) -> list[ManagedUserRead]:
    _require_invitation_admin(user)
    users = list(await db.scalars(
        select(User)
        .options(selectinload(User.profile))
        .where(User.entity_id == user.entity_id)
        .order_by(User.name, User.id)
    ))
    if not users:
        return []
    audit_rows = await db.execute(
        select(AuditLog.entity_id, AuditLog.action)
        .where(
            AuditLog.entity == "user",
            AuditLog.entity_id.in_([item.id for item in users]),
            AuditLog.action.in_(_USER_LIFECYCLE_ACTIONS),
        )
        .order_by(AuditLog.ts.desc(), AuditLog.id.desc())
    )
    latest_actions: dict[int, str] = {}
    for user_id, action in audit_rows:
        latest_actions.setdefault(user_id, action)
    return [_managed_user_read(item, latest_actions.get(item.id)) for item in users]


@router.post("/invitations", response_model=InvitationCreated, status_code=status.HTTP_201_CREATED)
async def invite_user(payload: InvitationCreate, user: CurrentUser, db: DbSession) -> InvitationCreated:
    _require_invitation_admin(user)
    settings = get_settings()
    await _enforce_rate_limit(
        f"auth:invite:admin:{user.id}",
        settings.invite_rate_limit,
        settings.invite_rate_window_seconds,
        "Invitation service temporarily unavailable",
    )
    email = str(payload.email).lower()
    existing = await db.execute(
        select(User.id, User.entity_id).where(User.email == email)
    )
    existing_user = existing.first()
    if existing_user is not None:
        detail = (
            "This address is already a member"
            if existing_user.entity_id == user.entity_id
            else "This address cannot be invited"
        )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)
    allowed_domains = settings.invite_allowed_email_domain_list
    email_domain = email.rpartition("@")[2].casefold()
    if allowed_domains and email_domain not in allowed_domains:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Email domain is not allowed for invitations",
        )

    profile = await db.get(Profile, payload.profile_id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Profile not found")
    if not can_grant_profile(user, profile):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="You cannot grant this profile's permissions",
        )
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
    settings = get_settings()
    await _enforce_rate_limit(
        f"auth:invite:admin:{user.id}",
        settings.invite_rate_limit,
        settings.invite_rate_window_seconds,
        "Invitation service temporarily unavailable",
    )
    invited = await db.scalar(
        select(User)
        .options(selectinload(User.profile))
        .where(User.id == user_id, User.entity_id == user.entity_id)
        .with_for_update()
    )
    if invited is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if _managed_user_status(invited, await _latest_lifecycle_action(db, invited.id)) != "Pending":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only pending invitations can be resent")
    await _issue_invitation(invited)
    await db.commit()
    return InvitationCreated(id=invited.id, email=invited.email, is_active=invited.is_active)


@router.post("/invitations/accept", response_model=InvitationAccepted)
async def accept_invitation(
    payload: InvitationAccept,
    db: DbSession,
    request: Request,
) -> InvitationAccepted:
    settings = get_settings()
    await _enforce_rate_limit(
        f"auth:invite:accept:ip:{_client_ip(request)}",
        settings.invite_accept_rate_limit,
        settings.invite_accept_rate_window_seconds,
        "Invitation service temporarily unavailable",
    )
    try:
        claims = decode_token(payload.token)
    except JWTError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_INVALID_INVITATION_DETAIL) from exc
    if claims.get("type") != "invitation" or not claims.get("sub") or not claims.get("jti"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_INVALID_INVITATION_DETAIL)
    try:
        user_id = int(claims["sub"])
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_INVALID_INVITATION_DETAIL) from exc

    token_id = str(claims["jti"])
    await _enforce_rate_limit(
        f"auth:invite:accept:jti:{token_id}",
        settings.invite_accept_rate_limit,
        settings.invite_accept_rate_window_seconds,
        "Invitation service temporarily unavailable",
    )

    invitation_key = f"invitation:user:{user_id}"
    claim_key = f"invitation:claim:user:{user_id}"
    invited = await db.scalar(
        select(User)
        .options(selectinload(User.profile))
        .where(User.id == user_id)
        .with_for_update()
    )
    if invited is None or invited.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_INVALID_INVITATION_DETAIL)
    prior_login = await db.scalar(
        select(AuditLog.id).where(
            AuditLog.entity == "user",
            AuditLog.entity_id == invited.id,
            AuditLog.action == "login",
        )
    )
    if prior_login is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_INVALID_INVITATION_DETAIL)
    email_local_part = invited.email.rpartition("@")[0].casefold()
    if payload.password.casefold() == email_local_part:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Password must not match the email username",
        )

    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        claimed = await redis.eval(
            _claim_invitation_script,
            2,
            invitation_key,
            claim_key,
            token_id,
            300,
        )
    except RedisError as exc:
        await redis.aclose()
        raise HTTPException(status_code=503, detail="Invitation service temporarily unavailable") from exc
    if claimed != 1:
        await redis.aclose()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_INVALID_INVITATION_DETAIL)

    consumed_ttl = 0
    invitation_ttl = 0
    committed = False
    try:
        invitation_ttl = await redis.ttl(invitation_key)
        if invitation_ttl < 1:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_INVALID_INVITATION_DETAIL)
        owns_claim = await redis.eval(
            _verify_invitation_claim_script,
            2,
            invitation_key,
            claim_key,
            token_id,
        )
        if owns_claim != 1:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_INVALID_INVITATION_DETAIL)

        consumed_ttl = await redis.eval(
            _consume_invitation_script,
            2,
            invitation_key,
            claim_key,
            token_id,
        )
        if consumed_ttl < 1:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_INVALID_INVITATION_DETAIL)

        # Acceptance requires the current Redis JTI, an inactive account, and no prior login.
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
        committed = True
        await redis.eval(
            _release_invitation_claim_script,
            1,
            claim_key,
            token_id,
        )
        return InvitationAccepted(email=invited.email)
    except RedisError as exc:
        raise HTTPException(status_code=503, detail="Invitation service temporarily unavailable") from exc
    finally:
        if not committed:
            await db.rollback()
            try:
                await redis.eval(
                    _restore_invitation_script,
                    2,
                    invitation_key,
                    claim_key,
                    token_id,
                    max(consumed_ttl, invitation_ttl, 1),
                )
            except RedisError as exc:
                raise HTTPException(status_code=503, detail="Invitation service temporarily unavailable") from exc
        await redis.aclose()


@router.patch("/users/{user_id}", response_model=ManagedUserRead)
async def update_user_activation(
    user_id: int,
    payload: UserActivationUpdate,
    user: CurrentUser,
    db: DbSession,
) -> ManagedUserRead:
    _require_invitation_admin(user)
    if user_id == user.id and not payload.is_active:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="You cannot deactivate your own account")
    target = await db.scalar(
        select(User)
        .options(selectinload(User.profile))
        .where(User.id == user_id, User.entity_id == user.entity_id)
        .with_for_update()
    )
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    latest_action = await _latest_lifecycle_action(db, target.id)
    was_pending = not target.is_active and _managed_user_status(target, latest_action) == "Pending"
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
    if not payload.is_active and was_pending:
        db.add(
            AuditLog(
                entity="user",
                entity_id=target.id,
                action="invitation_cancelled",
                actor_id=user.id,
                diff={"reason": "account_deactivated"},
            )
        )
    if not payload.is_active:
        settings = get_settings()
        redis = Redis.from_url(settings.redis_url, decode_responses=True)
        try:
            await redis.eval(
                _deactivate_user_script,
                3,
                f"invitation:user:{target.id}",
                f"invitation:claim:user:{target.id}",
                f"auth:user-epoch:{target.id}",
                str(uuid4()),
                settings.refresh_token_days * 86400,
            )
        except RedisError as exc:
            await db.rollback()
            raise HTTPException(status_code=503, detail="User deactivation temporarily unavailable") from exc
        finally:
            await redis.aclose()
    await db.commit()
    await db.refresh(target)
    latest_action = "activated" if payload.is_active else "deactivated"
    return _managed_user_read(target, latest_action)
