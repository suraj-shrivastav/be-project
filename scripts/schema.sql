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

-- Migration for existing tables:
-- ALTER TABLE saved_queries ADD COLUMN IF NOT EXISTS filters JSONB;
-- ALTER TABLE saved_queries ADD COLUMN IF NOT EXISTS query_type VARCHAR(16) NOT NULL DEFAULT 'prompt';
-- ALTER TABLE saved_queries ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now();

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
