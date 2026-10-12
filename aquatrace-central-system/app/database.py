from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    pass


# Pool size is env-configurable (DB_POOL_SIZE/DB_MAX_OVERFLOW): local concurrent-registration
# testing wants it generous (registration can hold a connection open for a few seconds while
# retrying a not-yet-registered parent, app/mqtt_ingestion.py _wait_for_parent, during a
# fully-concurrent simulation start against up to 25 nodes); a managed Postgres free tier (e.g.
# Supabase) needs it much smaller since the connection cap is shared with everything else on the
# account. Defaults favor the deployed case; bump both via env for local load testing.
_settings = get_settings()
engine = create_async_engine(
    _settings.database_url, echo=False, future=True, pool_size=_settings.db_pool_size, max_overflow=_settings.db_max_overflow
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncSession:
    async with SessionLocal() as session:
        yield session
