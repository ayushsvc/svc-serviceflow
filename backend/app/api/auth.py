from datetime import timedelta
from ipaddress import ip_address
from redis.exceptions import RedisError
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, status
from jose import JWTError
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.security import create_token, decode_token, verify_password
from app.deps import CurrentUser, DbSession
from app.models import Entity, User
from app.schemas import LoginRequest, MeRead, OwnEntity, RefreshRequest, TokenPair

router = APIRouter(prefix="/auth", tags=["auth"])

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
