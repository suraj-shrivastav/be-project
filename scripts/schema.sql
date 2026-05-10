-- ============================================================
-- Stock Screener — Supabase Schema
-- Run this in: Supabase Dashboard → SQL Editor → New query
-- ============================================================

-- ── Users ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id            TEXT        PRIMARY KEY,
    email         TEXT        UNIQUE NOT NULL,
    password_hash TEXT        NOT NULL,
    created_at    TIMESTAMPTZ DEFAULT now()
);

-- ── Saved queries ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS saved_queries (
    id         TEXT        PRIMARY KEY,
    user_id    TEXT        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name       TEXT        NOT NULL,
    prompt     TEXT        NOT NULL,
    sql        TEXT,
    filters    JSONB,
    query_type VARCHAR(16) NOT NULL DEFAULT 'prompt',
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_saved_queries_user ON saved_queries(user_id, created_at DESC);

-- Idempotent migrations for existing tables created before these columns
-- existed. Safe to re-run on every schema apply.
ALTER TABLE saved_queries ADD COLUMN IF NOT EXISTS filters    JSONB;
ALTER TABLE saved_queries ADD COLUMN IF NOT EXISTS query_type VARCHAR(16) NOT NULL DEFAULT 'prompt';
ALTER TABLE saved_queries ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now();

-- ── Query history ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS query_history (
    id         TEXT        PRIMARY KEY,
    user_id    TEXT        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    prompt     TEXT        NOT NULL,
    sql        TEXT,
    row_count  INTEGER,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_query_history_user ON query_history(user_id, created_at DESC);

-- ── Chat sessions ─────────────────────────────────────────────
-- Stores the full message history as JSONB for each chat session
CREATE TABLE IF NOT EXISTS chat_sessions (
    id         TEXT        PRIMARY KEY,   -- session_id from frontend
    user_id    TEXT        REFERENCES users(id) ON DELETE SET NULL,
    messages   JSONB       NOT NULL DEFAULT '[]',
    updated_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_user ON chat_sessions(user_id, updated_at DESC);

-- ── User events (every action) ────────────────────────────────
-- event_type examples:
--   query_run, stock_viewed, chat_message,
--   query_saved, query_deleted, sector_viewed, export
CREATE TABLE IF NOT EXISTS user_events (
    id         BIGSERIAL   PRIMARY KEY,
    user_id    TEXT        REFERENCES users(id) ON DELETE SET NULL,
    session_id TEXT,
    event_type TEXT        NOT NULL,
    metadata   JSONB,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_user_events_user       ON user_events(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_user_events_event_type ON user_events(event_type, created_at DESC);

-- ── Auth events ────────────────────────────────────────────────
-- event_type: register | login | login_failed
CREATE TABLE IF NOT EXISTS auth_events (
    id         BIGSERIAL   PRIMARY KEY,
    user_id    TEXT        REFERENCES users(id) ON DELETE SET NULL,
    email      TEXT,
    event_type TEXT        NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_auth_events_user ON auth_events(user_id, created_at DESC);

-- ============================================================
-- Stock data tables (replaces local Parquet + DuckDB)
-- ============================================================

-- ── Fundamentals (one row per ticker, refreshed nightly) ─────
CREATE TABLE IF NOT EXISTS fundamentals (
    ticker            TEXT          PRIMARY KEY,
    company_name      TEXT          NOT NULL,
    country           TEXT          NOT NULL,        -- 'US' | 'IN'
    exchange          TEXT          NOT NULL DEFAULT 'OTHER',  -- 'NSE' | 'BSE' | 'NASDAQ' | 'NYSE' | 'OTHER'
    currency          TEXT          NOT NULL DEFAULT 'USD',    -- 'INR' | 'USD' | …
    sector            TEXT,
    industry          TEXT,
    description       TEXT,
    market_cap        BIGINT,
    pe_ratio          NUMERIC(10,2),
    pb_ratio          NUMERIC(10,2),
    dividend_yield    NUMERIC(10,4),
    beta              NUMERIC(6,3),
    eps               NUMERIC(10,2),
    revenue_growth    NUMERIC(10,4),
    profit_margin     NUMERIC(10,4),
    debt_to_equity    NUMERIC(10,2),
    return_on_equity  NUMERIC(10,4),
    week52_high       NUMERIC(12,2),
    week52_low        NUMERIC(12,2),
    last_price        NUMERIC(12,2),
    month_change      NUMERIC(10,4),
    year_change       NUMERIC(10,4),
    updated_at        TIMESTAMPTZ   DEFAULT now()
);

-- Migration FIRST so existing fundamentals tables get the new columns
-- before we try to index them. Idempotent — no-op when columns already exist.
ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS exchange TEXT NOT NULL DEFAULT 'OTHER';
ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS currency TEXT NOT NULL DEFAULT 'USD';

-- Indexes — safe to create now that the columns are guaranteed to exist
CREATE INDEX IF NOT EXISTS idx_fundamentals_sector     ON fundamentals(sector);
CREATE INDEX IF NOT EXISTS idx_fundamentals_country    ON fundamentals(country);
CREATE INDEX IF NOT EXISTS idx_fundamentals_exchange   ON fundamentals(exchange);
CREATE INDEX IF NOT EXISTS idx_fundamentals_market_cap ON fundamentals(market_cap DESC);

-- ── Daily OHLCV (replaces minute-level Parquet) ──────────────
CREATE TABLE IF NOT EXISTS daily_prices (
    ticker  TEXT          NOT NULL,
    date    DATE          NOT NULL,
    open    NUMERIC(12,2),
    high    NUMERIC(12,2),
    low     NUMERIC(12,2),
    close   NUMERIC(12,2),
    volume  BIGINT,
    PRIMARY KEY (ticker, date)
);
CREATE INDEX IF NOT EXISTS idx_daily_prices_ticker_date ON daily_prices(ticker, date DESC);

-- ── Quarterly financials (one row per ticker per quarter end) ─
-- Used for the trend charts on the stock detail page.
CREATE TABLE IF NOT EXISTS quarterly_financials (
    ticker         TEXT          NOT NULL,
    quarter_end    DATE          NOT NULL,
    revenue        NUMERIC(20,2),
    net_income     NUMERIC(20,2),
    operating_inc  NUMERIC(20,2),
    gross_profit   NUMERIC(20,2),
    updated_at     TIMESTAMPTZ   DEFAULT now(),
    PRIMARY KEY (ticker, quarter_end)
);
CREATE INDEX IF NOT EXISTS idx_quarterly_ticker_date ON quarterly_financials(ticker, quarter_end DESC);
