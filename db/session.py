"""Async SQLAlchemy engine + session factory for Supabase/PostgreSQL."""

import os
from typing import AsyncGenerator

from dotenv import load_dotenv

load_dotenv()  # must run before os.getenv — session.py is imported before api.py calls load_dotenv

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# Supabase gives a postgresql:// URL; SQLAlchemy needs postgresql+asyncpg://
_raw_url = os.getenv("DATABASE_URL", "")
if _raw_url.startswith("postgres://"):
    _raw_url = _raw_url.replace("postgres://", "postgresql+asyncpg://", 1)
elif _raw_url.startswith("postgresql://"):
    _raw_url = _raw_url.replace("postgresql://", "postgresql+asyncpg://", 1)

engine = create_async_engine(
    _raw_url,
    echo=False,          # set True temporarily to debug queries
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,  # drop stale connections after server restart
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields an async DB session."""
    async with AsyncSessionLocal() as session:
        yield session
