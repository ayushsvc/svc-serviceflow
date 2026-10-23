from datetime import UTC, datetime, timedelta
from uuid import uuid4

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import get_settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def create_token(subject: str, role: str, token_type: str, expires_delta: timedelta, family: str | None = None) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {"sub": subject, "role": role, "type": token_type, "iat": now, "exp": now + expires_delta, "jti": str(uuid4()), "family": family or str(uuid4())},
        get_settings().jwt_secret,
        algorithm="HS256",
    )


def decode_token(token: str) -> dict:
    return jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
