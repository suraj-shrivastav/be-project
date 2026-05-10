"""SQLAlchemy ORM models — maps to the Supabase/PostgreSQL schema.

Users are managed by Supabase Auth (auth.users). The user_id columns below
store the Supabase auth UUID but have no FK constraint to allow the
auth.users table to live in a different schema.
"""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import BigInteger, Date, DateTime, Integer, Numeric, String, Text
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


# ── Stock data tables (replaces local Parquet + DuckDB) ──────


class Fundamental(Base):
    __tablename__ = "fundamentals"

    ticker:           Mapped[str]               = mapped_column(Text, primary_key=True)
    company_name:     Mapped[str]               = mapped_column(Text, nullable=False)
    country:          Mapped[str]               = mapped_column(Text, nullable=False, index=True)
    exchange:         Mapped[str]               = mapped_column(Text, nullable=False, index=True, default="OTHER")
    currency:         Mapped[str]               = mapped_column(Text, nullable=False, default="USD")
    sector:           Mapped[str | None]        = mapped_column(Text, nullable=True, index=True)
    industry:         Mapped[str | None]        = mapped_column(Text, nullable=True)
    description:      Mapped[str | None]        = mapped_column(Text, nullable=True)
    market_cap:       Mapped[int | None]        = mapped_column(BigInteger, nullable=True, index=True)
    pe_ratio:         Mapped[Decimal | None]    = mapped_column(Numeric(10, 2), nullable=True)
    pb_ratio:         Mapped[Decimal | None]    = mapped_column(Numeric(10, 2), nullable=True)
    dividend_yield:   Mapped[Decimal | None]    = mapped_column(Numeric(10, 4), nullable=True)
    beta:             Mapped[Decimal | None]    = mapped_column(Numeric(6, 3),  nullable=True)
    eps:              Mapped[Decimal | None]    = mapped_column(Numeric(10, 2), nullable=True)
    revenue_growth:   Mapped[Decimal | None]    = mapped_column(Numeric(10, 4), nullable=True)
    profit_margin:    Mapped[Decimal | None]    = mapped_column(Numeric(10, 4), nullable=True)
    debt_to_equity:   Mapped[Decimal | None]    = mapped_column(Numeric(10, 2), nullable=True)
    return_on_equity: Mapped[Decimal | None]    = mapped_column(Numeric(10, 4), nullable=True)
    week52_high:      Mapped[Decimal | None]    = mapped_column(Numeric(12, 2), nullable=True)
    week52_low:       Mapped[Decimal | None]    = mapped_column(Numeric(12, 2), nullable=True)
    last_price:       Mapped[Decimal | None]    = mapped_column(Numeric(12, 2), nullable=True)
    month_change:     Mapped[Decimal | None]    = mapped_column(Numeric(10, 4), nullable=True)
    year_change:      Mapped[Decimal | None]    = mapped_column(Numeric(10, 4), nullable=True)
    updated_at:       Mapped[datetime]          = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class DailyPrice(Base):
    __tablename__ = "daily_prices"

    ticker: Mapped[str]            = mapped_column(Text, primary_key=True)
    date:   Mapped[date]           = mapped_column(Date, primary_key=True)
    open:   Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    high:   Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    low:    Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    close:  Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    volume: Mapped[int | None]     = mapped_column(BigInteger,     nullable=True)


class QuarterlyFinancial(Base):
    """One row per (ticker, quarter_end) — backs the quarterly trend chart on
    the stock detail page. Sourced from yfinance's quarterly_income_stmt."""

    __tablename__ = "quarterly_financials"

    ticker:        Mapped[str]            = mapped_column(Text, primary_key=True)
    quarter_end:   Mapped[date]           = mapped_column(Date, primary_key=True)
    revenue:       Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    net_income:    Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    operating_inc: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    gross_profit:  Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    updated_at:    Mapped[datetime]       = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
