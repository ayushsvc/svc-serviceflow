from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

db_url = get_settings().database_url
connect_args = {"check_same_thread": False} if "sqlite" in db_url else {}

engine = create_async_engine(db_url, pool_pre_ping=True, connect_args=connect_args)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session
