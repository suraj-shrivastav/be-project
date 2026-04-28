"""SQLAlchemy ORM models — maps to the Supabase/PostgreSQL schema.

Users are managed by Supabase Auth (auth.users). The user_id columns below
store the Supabase auth UUID but have no FK constraint to allow the
auth.users table to live in a different schema.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class SavedQuery(Base):
    __tablename__ = "saved_queries"

    id:         Mapped[str]           = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id:    Mapped[str]           = mapped_column(String(36), nullable=False, index=True)
    name:       Mapped[str]           = mapped_column(Text, nullable=False)
    prompt:     Mapped[str]           = mapped_column(Text, nullable=False)
    sql:        Mapped[str | None]    = mapped_column(Text, nullable=True)
    filters:    Mapped[list | None]   = mapped_column(JSONB, nullable=True)
    query_type: Mapped[str]           = mapped_column(String(16), nullable=False, default="prompt")
    created_at: Mapped[datetime]      = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime]      = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class QueryHistory(Base):
    __tablename__ = "query_history"

    id:         Mapped[str]        = mapped_column(String(36),  primary_key=True, default=_uuid)
    user_id:    Mapped[str]        = mapped_column(String(36),  nullable=False, index=True)
    prompt:     Mapped[str]        = mapped_column(Text,        nullable=False)
    sql:        Mapped[str | None] = mapped_column(Text,        nullable=True)
    row_count:  Mapped[int | None] = mapped_column(Integer,     nullable=True)
    created_at: Mapped[datetime]   = mapped_column(DateTime(timezone=True), default=_now)


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id:         Mapped[str]           = mapped_column(String(128), primary_key=True)
    user_id:    Mapped[str | None]    = mapped_column(String(36),  nullable=True, index=True)
    messages:   Mapped[list]          = mapped_column(JSONB,       nullable=False, default=list)
    updated_at: Mapped[datetime]      = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class UserEvent(Base):
    __tablename__ = "user_events"

    id:         Mapped[int]           = mapped_column(BigInteger,  primary_key=True, autoincrement=True)
    user_id:    Mapped[str | None]    = mapped_column(String(36),  nullable=True, index=True)
    session_id: Mapped[str | None]    = mapped_column(String(128), nullable=True)
    event_type: Mapped[str]           = mapped_column(String(64),  nullable=False, index=True)
    event_metadata: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)
    created_at: Mapped[datetime]      = mapped_column(DateTime(timezone=True), default=_now, index=True)
