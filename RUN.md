# Run Guide

Step-by-step from a fresh checkout to a working server with real stock data.

Working directory throughout: `be-project/`. Shell: PowerShell on Windows.

---

## 1. Install dependencies

```powershell
uv sync
```

This pulls everything in `pyproject.toml` (FastAPI, SQLAlchemy + asyncpg, yfinance, transformers, openai, etc.) and removes `duckdb`. Takes ~2–4 min on first run.

---

## 2. Set up `.env`

Create `be-project/.env` with these keys:

```ini
# Supabase Postgres — use the POOLER endpoint (port 6543)
DATABASE_URL=postgresql://postgres.<project-ref>:<db-password>@aws-0-<region>.pooler.supabase.com:6543/postgres

# Supabase Auth
SUPABASE_URL=https://<project-ref>.supabase.co
SUPABASE_PUBLISHABLE_KEY=<anon-key>

# LLM provider — pick ONE of these (NIM preferred)
NVIDIA_NIM_API_KEY=<nvcf-token>
# OPENROUTER_API=<openrouter-key>

# External data
INDIAN_API_KEY=<indianapi.in-key>
TAVILY_API_KEY=<tavily-key>

# CORS for the frontend (optional, defaults to localhost:3000)
FRONTEND_URL=http://localhost:3000
```

Where to grab each:

- `DATABASE_URL` → Supabase Dashboard → Project Settings → Database → **Connection Pooling** → copy URI (mode: Transaction).
- `SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY` → Project Settings → API.
- `NVIDIA_NIM_API_KEY` → build.nvidia.com → API Keys.
- `INDIAN_API_KEY` → from your existing indianapi.in account.
- `TAVILY_API_KEY` → tavily.com → API Keys.

---

## 3. Run the schema on Supabase

Go to Supabase Dashboard → **SQL Editor** → **New query**. Paste the entire contents of [scripts/schema.sql](scripts/schema.sql) and click Run.

Idempotent — `IF NOT EXISTS` everywhere, so re-running is safe. This creates:

- `users`, `saved_queries`, `query_history`, `chat_sessions`, `user_events`, `auth_events` (existing)
- `fundamentals`, `daily_prices` (new — stock data)
- All required indexes
- Adds `exchange` + `currency` columns if they're missing on an old `fundamentals` table

---

## 4. Populate the database with real data

```powershell
uv run python -m scripts.ingest
```

Pulls fundamentals + 2 years of daily prices for ~140 tickers (Nifty 100 NSE + 12 BSE picks + 50 Nasdaq leaders). Takes ~10–15 min — yfinance is rate-limited so the ingester batches in chunks of 25 with 2s sleeps.

**Quick mode** (fundamentals only, ~3 min — useful for first-time testing):

```powershell
uv run python -m scripts.ingest --quick
```

You should see Rich progress bars and a final `✓ Ingestion complete` line. Some tickers may fail (yfinance returns empty `.info` for delisted/renamed names) — those are logged in yellow and skipped.

---

## 5. Start the API server

```powershell
uv run uvicorn api:app --reload --port 8000
```

Watch for these startup logs:
```
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000
```

The first request will be slow (~5–15s) because the local Qwen3Guard model has to load to GPU/CPU on first invocation.

---

## 6. Smoke test the endpoints

### a. Health check
```powershell
curl.exe http://localhost:8000/api/health
```
→ `{"ok": true}`

### b. Available markets
```powershell
curl.exe http://localhost:8000/api/markets
```
→ list of 3 markets (global, india, nasdaq).

### c. Presets per market
```powershell
curl.exe "http://localhost:8000/api/presets?market=india"
curl.exe "http://localhost:8000/api/presets?market=nasdaq"
curl.exe "http://localhost:8000/api/presets?market=global"
```
→ 6 market-specific presets each.

### d. Novice intent (deterministic — no LLM call)

```powershell
curl.exe -X POST http://localhost:8000/api/query `
  -H "Content-Type: application/json" `
  -d '{\"prompt\": \"safe dividend stocks\", \"market\": \"india\"}'
```

→ ~20 NSE/BSE stocks ranked by safety score. Response includes `"intent": "safe"`, `"market": "india"`, `"intent_explanation"`, and a SQL string showing the `exchange IN ('NSE','BSE')` filter.

```powershell
curl.exe -X POST http://localhost:8000/api/query `
  -H "Content-Type: application/json" `
  -d '{\"prompt\": \"high growth tech\", \"market\": \"nasdaq\"}'
```

→ Nasdaq tech stocks ranked by growth.

### e. LLM-driven query (falls through to Llama 3.3 70B)

```powershell
curl.exe -X POST http://localhost:8000/api/query `
  -H "Content-Type: application/json" `
  -d '{\"prompt\": \"PE under 25 and revenue growth above 15%\", \"market\": \"global\"}'
```

→ Returns `sql` field showing the Postgres query the LLM wrote.

### f. Stock detail

```powershell
curl.exe http://localhost:8000/api/stock/AAPL
curl.exe http://localhost:8000/api/stock/RELIANCE
```

→ fundamentals + 1y daily prices + computed technicals (SMA-20/50/200, RSI-14, volatility).

### g. SSE stream (cmd-line — easier with a frontend)

```powershell
curl.exe -N -X POST http://localhost:8000/api/query/stream `
  -H "Content-Type: application/json" `
  -d '{\"prompt\": \"safe stocks\", \"market\": \"india\"}'
```

You'll see events in order: `safety` → `executing` → `done` (with rows) → `explanations` (per-row narratives from the explainer).

---

## 7. (Optional) Run the frontend

In a second terminal, working dir `frontend/`:

```powershell
npm install        # if first time
npm run dev
```

Open http://localhost:3000. The frontend doesn't yet wire the market dropdown — see [MARKETS_UX.md](MARKETS_UX.md) for the spec.

---

## Common failures and fixes

| Symptom | Cause | Fix |
|---|---|---|
| `connection refused: 6543` on ingest/query | DATABASE_URL wrong port | Use the **pooler** endpoint (6543), not direct (5432) |
| `relation "fundamentals" does not exist` | Schema not run | Re-run [scripts/schema.sql](scripts/schema.sql) on Supabase |
| Ingester hangs / `429 Too Many Requests` | yfinance rate limit | Wait 5 min, re-run — partial state is fine, `daily_prices` uses ON CONFLICT DO NOTHING |
| `/api/query` returns `llm_error: ...` | NIM API key missing/invalid | Check `NVIDIA_NIM_API_KEY` in `.env`, or set `OPENROUTER_API` instead |
| Guard model takes 30s+ on first request | HuggingFace downloading the model | One-time — subsequent requests use the cached model |
| `column "exchange" does not exist` after ingest | Old schema on existing DB | Re-run [scripts/schema.sql](scripts/schema.sql) — the `ADD COLUMN IF NOT EXISTS` lines patch it |
| All ingester rows fail with empty info | Yahoo blocked your IP | Wait 30 min, or use a VPN |

---

## Refresh data

To re-pull fresh data later (manually or via cron):

```powershell
uv run python -m scripts.ingest
```

Fundamentals are wiped and replaced atomically (`TRUNCATE` + bulk insert in one transaction). Daily prices use `ON CONFLICT (ticker, date) DO NOTHING` so existing rows are kept and only new days are added.
