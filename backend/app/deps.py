from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from redis.asyncio import Redis
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.db import get_db
from app.core.security import decode_token
from app.core.config import get_settings
from app.models import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")
DbSession = Annotated[AsyncSession, Depends(get_db)]


async def current_user(token: Annotated[str, Depends(oauth2_scheme)], db: DbSession) -> User:
    credentials_error = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication credentials")
    try:
        payload = decode_token(token)
        if payload.get("type") != "access" or not payload.get("sub"):
            raise credentials_error
        redis = Redis.from_url(get_settings().redis_url, decode_responses=True)
        try:
            if await redis.get(f"revoked:{payload.get('jti')}"):
                raise credentials_error
        finally:
            await redis.aclose()
        user_id = int(payload["sub"])
    except (JWTError, ValueError) as exc:
        raise credentials_error from exc
    user = await db.scalar(select(User).options(selectinload(User.profile)).where(User.id == user_id, User.is_active.is_(True)))
    if user is None:
        raise credentials_error
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def require_helpdesk(user: CurrentUser) -> User:
    if user.profile.interface != "helpdesk":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Helpdesk rights required")
    return user


def require_admin(user: CurrentUser) -> User:
    if user.profile.name != "Administrator":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Administrator rights required")
    return user
