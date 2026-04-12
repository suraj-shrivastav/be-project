# Full-Stack Implementation Plan
## AI-Powered Stock Screener — NLP → SQL → Visualization

> Based on backend analysis of `main.py`, `models/`, and research in `RESEARCH.md`.  
> Inspired by: Finviz, TradingView Screener, Simply Wall St NLP, Stox.AI.

---

## 1. What We Are Building

A multi-page, dark/light mode web app that lets users type natural language stock queries
(e.g. *"tech stocks with P/E under 20 and market cap over 5B"*), view structured results in
tables or charts, explore sector relationships visually via Graphviz, and save/revisit their
screens — all backed by the existing LLM → DuckDB pipeline.

---

## 2. Tech Stack

### Frontend
| Layer | Choice | Why |
|---|---|---|
| Framework | **Next.js 15** (App Router) | File-based routing, SSR/CSR hybrid, API routes |
| Language | **JavaScript (ES2022+)** | No TS compilation overhead, plain JS throughout |
| Styling | **Tailwind CSS** + `globals.css` (user-provided) | Utility-first, theme tokens in CSS vars |
| Components | **shadcn/ui** + **lucide-react** | Accessible, unstyled base + consistent icons |
| State | **Zustand** | Lightweight global store for query state, auth, theme |
| Charts | **Recharts** | Financial line/bar/area charts, composable |
| Graph Viz | **@hpcc-js/wasm** (Graphviz WASM) | Render DOT language graphs in browser — sector trees, correlation maps |
| Data Fetching | **TanStack Query (React Query)** | Caching, loading states, refetch on focus |
| Forms | **React Hook Form** + **Zod** | Validation for auth and filter forms |
| Auth Client | **Supabase JS SDK** | Session management, OAuth, JWT tokens |

### Backend
| Layer | Choice | Why |
|---|---|---|
| API Framework | **FastAPI** | Async, automatic OpenAPI docs, Python-native |
| Auth Middleware | **python-jose** + **passlib** | JWT issue/verify, bcrypt hashing |
| CORS | **FastAPI CORSMiddleware** | Allow frontend origin |
| Existing Pipeline | `models/filter.py`, `models/guard.py` | Unchanged — just wrapped |
| DB (Auth/User data) | **Supabase (PostgreSQL)** | Managed Postgres + built-in Auth + Row Level Security |
| DB Client (BE) | **asyncpg** + **SQLAlchemy async** | Non-blocking DB queries from FastAPI |

### Why Supabase for Auth DB
- Provides **PostgreSQL** (production-grade, relational) for free tier
- Built-in **Row Level Security** — user can only see their own saved queries
- Auth handles **email/password + Google OAuth** out of the box
- **Realtime** subscriptions available for future features (live alerts)
- Single dashboard for DB + Auth + Storage — reduces infra overhead

---

## 3. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        FRONTEND (Next.js)                        │
│                                                                  │
│  /            /screener       /stock/[ticker]    /map   /dash   │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  Zustand Store: { query, results, user, theme, history } │   │
│  └──────────────────────────────────────────────────────────┘   │
│         ↓ TanStack Query (caching + loading states)             │
└──────────────────────┬──────────────────────────────────────────┘
                       │ HTTP (fetch / axios)
                       │ JSON over REST
┌──────────────────────▼──────────────────────────────────────────┐
│                     BACKEND (FastAPI)                            │
│                                                                  │
│  POST /api/query    →  Guard → Filter → DuckDB → JSON           │
│  GET  /api/stock/:ticker  →  DuckDB fundamentals + prices       │
│  GET  /api/sectors  →  DOT language for Graphviz                │
│  GET/POST /api/saved  →  Supabase (user's saved queries)        │
│  GET/POST /api/history  →  Supabase (query history)             │
│  POST /api/auth/register / login / refresh                      │
│                                                                  │
│  ┌────────────────────────────────────────────────────────┐     │
│  │  Existing Pipeline (unchanged)                          │     │
│  │  GuardModel → Filter (Qwen3-14b) → SQLValidator         │     │
│  │  → DuckDB (fundamentals.parquet, prices/**/*.parquet)   │     │
│  └────────────────────────────────────────────────────────┘     │
└──────────────────────┬──────────────────────────────────────────┘
                       │ asyncpg
                       ▼
              Supabase (PostgreSQL)
              users | saved_queries | query_history
```

---

## 4. Backend API Design

### New File: `api.py` (FastAPI entry point)

Wraps the existing `process_prompt()` function from `main.py` — **no changes to existing models**.

```
POST   /api/query
       Body:    { "prompt": "tech stocks PE under 20" }
       Returns: { "columns": [...], "rows": [[...]], "sql": "...", "row_count": 42 }
                or { "error": "non-finance" | "unsafe" | "sql_error" }

GET    /api/stock/{ticker}
       Returns: { "fundamentals": {...}, "prices": [{date, open, high, low, close, volume}] }

GET    /api/sectors
       Returns: { "dot": "digraph G { ... }" }   ← Graphviz DOT for sector tree

GET    /api/columns
       Returns: { "columns": ["Ticker", "MarketCap", ...] }  ← for frontend hints

POST   /api/auth/register
       Body:    { "email": "...", "password": "..." }
       Returns: { "access_token": "...", "user": {...} }

POST   /api/auth/login
       Body:    { "email": "...", "password": "..." }
       Returns: { "access_token": "...", "refresh_token": "..." }

GET    /api/history          [Auth required]
       Returns: [{ "id", "prompt", "sql", "row_count", "created_at" }]

POST   /api/history          [Auth required]
       Body:    { "prompt": "...", "sql": "...", "row_count": 42 }

GET    /api/saved            [Auth required]
       Returns: [{ "id", "name", "prompt", "sql", "created_at" }]

POST   /api/saved            [Auth required]
       Body:    { "name": "My Screen", "prompt": "...", "sql": "..." }

DELETE /api/saved/{id}       [Auth required]
```

### Changes to `main.py`
Only: extract `process_prompt()` into a return-value form (it already nearly is — just
remove the `display_results()` side-effect call and return the DataFrame as a dict).

---

## 5. Database Schema (Supabase / PostgreSQL)

```sql
-- Managed by Supabase Auth
CREATE TABLE users (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email       TEXT UNIQUE NOT NULL,
  created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE saved_queries (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     UUID REFERENCES users(id) ON DELETE CASCADE,
  name        TEXT NOT NULL,
  prompt      TEXT NOT NULL,
  sql         TEXT,
  created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE query_history (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     UUID REFERENCES users(id) ON DELETE CASCADE,
  prompt      TEXT NOT NULL,
  sql         TEXT,
  row_count   INT,
  created_at  TIMESTAMPTZ DEFAULT now()
);

-- Row Level Security: users only see their own rows
ALTER TABLE saved_queries ENABLE ROW LEVEL SECURITY;
ALTER TABLE query_history ENABLE ROW LEVEL SECURITY;

CREATE POLICY "own rows" ON saved_queries FOR ALL USING (auth.uid() = user_id);
CREATE POLICY "own rows" ON query_history FOR ALL USING (auth.uid() = user_id);
```

---

## 6. Pages & Routes

```
/                     Landing page
/screener             Main NLP screener (core feature)
/screener?q=...       Pre-filled query via URL param
/stock/[ticker]       Individual stock detail
/map                  Sector heatmap + Graphviz tree
/dashboard            Saved queries + history (auth-gated)
/auth/login           Login
/auth/signup          Signup
```

### Page Descriptions

#### `/` — Landing
- Hero section with large NLP search bar (same bar as screener)
- 3 preset queries as clickable chips: *"Top 10 by Market Cap"*, *"High Dividend Yield > 3%"*, *"Undervalued Tech Stocks"*
- Feature highlights: AI-powered, real data, visual results
- No auth required

#### `/screener` — Core Feature
- Prominent NLP input bar (top, full width) with submit + clear
- Status banner: shows pipeline step (Safety Check → Generating SQL → Running Query)
- Generated SQL shown in a collapsible code block (transparency)
- Results area with **3 view toggles** (inspired by Finviz):
  - **Table view** — sortable columns, all metrics
  - **Cards view** — stock cards with key metrics + mini sparkline
  - **Chart view** — Recharts bar/line comparison of selected metric across results
- Column selector (show/hide columns)
- Export to CSV button
- Save screen button (auth-gated, opens name dialog)
- Recent queries sidebar (last 5 queries)

#### `/stock/[ticker]` — Stock Detail
- Header: Ticker, sector/industry breadcrumb, price, % change badges
- **Price Chart** (Recharts AreaChart): Close price over time with volume bar underneath
- **Fundamentals Grid**: MarketCap, P/E, P/B, EPS, Beta, DividendYield, FloatShares
- **Performance Badges**: MonthPercentageChange, YearPercentageChange (color-coded green/red)
- Back to screener results link

#### `/map` — Sector Explorer
- **Graphviz Sector Tree**: DOT-rendered directed graph showing Market → Sector → Industry → Ticker hierarchy
  - Nodes sized by MarketCap
  - Nodes colored by MonthPercentageChange (green = up, red = down)
  - Click node to navigate to stock or filter screener
- **Sector Performance Bars**: Recharts horizontal BarChart — each sector's average YearPercentageChange

#### `/dashboard` — User Dashboard (auth-gated)
- Saved Queries list — run, rename, delete
- Query History (last 50) — re-run any past query
- Redirect to `/auth/login` if not authenticated

#### `/auth/login` + `/auth/signup`
- Clean centered card layout
- Email/password fields
- Google OAuth button (Supabase)
- Link between login ↔ signup

---

## 7. Component Architecture

```
frontend/
├── app/                          # Next.js App Router pages
│   ├── page.tsx                  # Landing
│   ├── screener/page.tsx
│   ├── stock/[ticker]/page.tsx
│   ├── map/page.tsx
│   ├── dashboard/page.tsx
│   └── auth/
│       ├── login/page.tsx
│       └── signup/page.tsx
│
├── components/
│   ├── ui/                       # Base reusable (lucide-react icons throughout)
│   │   ├── Button.tsx            # variant: primary | ghost | outline | danger
│   │   ├── Input.tsx             # with icon slot (Search, X from lucide)
│   │   ├── Card.tsx              # with CardHeader, CardBody, CardFooter
│   │   ├── Badge.tsx             # variant: green | red | neutral | sector
│   │   ├── Table.tsx             # sortable, with SortIcon (lucide ChevronUp/Down)
│   │   ├── Tooltip.tsx
│   │   ├── Modal.tsx             # for save screen dialog
│   │   ├── Dropdown.tsx          # column selector, view toggles
│   │   ├── Spinner.tsx           # loading state (Loader2 from lucide)
│   │   ├── ThemeToggle.tsx       # Sun/Moon from lucide, toggles dark/light
│   │   ├── StatusBanner.tsx      # pipeline step indicator
│   │   └── CodeBlock.tsx         # SQL display (collapsible)
│   │
│   ├── layout/
│   │   ├── Navbar.tsx            # Logo, nav links, ThemeToggle, user avatar
│   │   ├── Sidebar.tsx           # Recent queries + saved queries (screener page)
│   │   └── PageWrapper.tsx       # Max-width container, consistent padding
│   │
│   ├── screener/
│   │   ├── QueryInput.tsx        # NLP textarea + submit button + preset chips
│   │   ├── PipelineStatus.tsx    # Animated step indicator (Safety → SQL → Execute)
│   │   ├── ResultsTable.tsx      # Sortable table with column selector
│   │   ├── ResultsCards.tsx      # Grid of StockCard components
│   │   ├── ViewToggle.tsx        # Table | Cards | Chart toggle (lucide icons)
│   │   ├── MetricChart.tsx       # Recharts bar chart for metric comparison
│   │   ├── ExportButton.tsx      # CSV export
│   │   └── SaveScreenDialog.tsx  # Modal to name + save a query
│   │
│   ├── charts/
│   │   ├── PriceAreaChart.tsx    # Recharts AreaChart — Close price over time
│   │   ├── VolumeBarChart.tsx    # Recharts BarChart — ShareVolume
│   │   ├── MetricBarChart.tsx    # Horizontal BarChart — compare metric across tickers
│   │   └── SectorPerfChart.tsx   # Sector avg performance bars
│   │
│   ├── graph/
│   │   └── SectorTree.tsx        # Graphviz WASM — renders DOT string from /api/sectors
│   │
│   ├── stock/
│   │   ├── StockHeader.tsx       # Ticker badge, name, price, change badge, breadcrumb
│   │   ├── FundamentalsGrid.tsx  # Metric grid cards (MarketCap, P/E, etc.)
│   │   └── StockCard.tsx         # Compact card used in ResultsCards grid
│   │
│   └── auth/
│       ├── LoginForm.tsx
│       └── SignupForm.tsx
│
├── store/
│   ├── queryStore.ts             # { prompt, results, sql, status, error }
│   ├── authStore.ts              # { user, session, isLoading }
│   └── uiStore.ts                # { theme, sidebarOpen }
│
├── hooks/
│   ├── useQuery.ts               # TanStack Query wrapper for POST /api/query
│   ├── useStock.ts               # GET /api/stock/:ticker
│   ├── useSectors.ts             # GET /api/sectors
│   ├── useSaved.ts               # GET/POST/DELETE /api/saved
│   └── useHistory.ts             # GET/POST /api/history
│
├── lib/
│   ├── api.ts                    # Typed fetch wrappers for all endpoints
│   ├── formatters.ts             # formatMarketCap(1e9) → "1.00B", formatPct, etc.
│   ├── supabase.ts               # Supabase client init
│   └── dotGenerator.ts           # Build DOT string from sector/industry data
│
├── styles/
│   └── globals.css               # User-provided (dark/light CSS variables)
│
└── types/
    └── index.js                  # JSDoc type definitions (QueryResult, StockRow, etc.)
```

---

## 8. Visualization Plan

### Recharts (Financial Charts)

Used on: `/screener` (metric comparison), `/stock/[ticker]` (price history)

| Component | Chart Type | Data Source | X Axis | Y Axis |
|---|---|---|---|---|
| `PriceAreaChart` | AreaChart | `GET /api/stock/:ticker` prices | Datetime | Close |
| `VolumeBarChart` | BarChart | Same | Datetime | ShareVolume |
| `MetricBarChart` | BarChart (horizontal) | Screener results | Ticker | Selected metric |
| `SectorPerfChart` | BarChart | Screener results grouped | Sector | Avg YearPercentageChange |

All charts:
- Respect theme (dark/light CSS vars via `stroke` and `fill` props)
- Have `<Tooltip>` showing formatted values (formatMarketCap, formatPct)
- Responsive via `<ResponsiveContainer width="100%" />`
- No animations (set `isAnimationActive={false}`)

### Graphviz WASM (`@hpcc-js/wasm`)

Used on: `/map`

**What it renders:** Sector → Industry → Ticker hierarchy as a directed graph.

**How it works:**
1. Backend `GET /api/sectors` queries DuckDB:
   ```sql
   SELECT DISTINCT Ticker, Sector, Industry, MarketCap, MonthPercentageChange
   FROM fundamentals
   ```
2. Backend generates a DOT string:
   ```dot
   digraph sectors {
     rankdir=LR;
     node [shape=box, style=filled];
     "Technology" -> "Software" -> "AAPL" [color="#22c55e"];
     "Technology" -> "Hardware" -> "MSFT";
     ...
   }
   ```
   - Node fill color: green if MonthPercentageChange > 0, red if < 0
   - Node size (fontsize): scaled to log(MarketCap)
3. Frontend `SectorTree.tsx` calls `@hpcc-js/wasm` `graphviz.layout(dot, "svg", "dot")`
4. Renders the SVG inline with click handlers on ticker nodes → navigate to `/stock/[ticker]`

**Why Graphviz over D3 force layout:**
- Hierarchical tree layouts (dot engine) look cleaner for sector/industry/ticker hierarchy
- No custom layout math needed
- DOT language is generated server-side (pure Python string formatting)

---

## 9. Theme System (Dark / Light)

- Default: **Dark mode**
- Toggle: `ThemeToggle` component (lucide `Sun` / `Moon` icon) in Navbar
- Implementation: CSS custom properties in `globals.css` (user-provided)
  - `data-theme="dark"` on `<html>` (default)
  - `data-theme="light"` on toggle
- Zustand `uiStore.theme` persists to `localStorage`
- **No color overrides** — all component colors reference CSS vars (`var(--bg)`, `var(--text)`, etc.)
- Tailwind `darkMode: 'class'` strategy

---

## 10. Auth Flow

```
Signup:  Email + Password → Supabase Auth → user row created → JWT issued
Login:   Email + Password → Supabase Auth → JWT returned → stored in httpOnly cookie
OAuth:   Google → Supabase OAuth flow → same JWT
Session: JWT validated on each API request via FastAPI dependency `get_current_user`
Logout:  Supabase signOut → cookie cleared → Zustand authStore reset
```

**Protected routes** (frontend): `/dashboard` — middleware in `middleware.ts` checks
Supabase session; redirects to `/auth/login` if missing.

**Protected endpoints** (backend): `GET/POST /api/saved`, `GET/POST /api/history` —
FastAPI `Depends(get_current_user)` verifies JWT from `Authorization: Bearer` header.

---

## 11. Features Inspired by Research

| Feature | Inspired By | Implementation |
|---|---|---|
| NLP query bar (always visible) | Simply Wall St, Stox.AI | `QueryInput.tsx` — full-width, top of screener |
| Preset query chips | Finviz preset screens | 4 chips below input: "Top Market Cap", "High Dividend", "Tech Momentum", "Undervalued" |
| Multi-mode results | Finviz (Table/Charts/Map views) | `ViewToggle.tsx` with Table / Cards / Chart modes |
| SQL transparency | — | Collapsible `CodeBlock.tsx` shows generated SQL |
| Pipeline status | — | `PipelineStatus.tsx` — Safety Check → SQL Gen → Execute steps |
| Saved screens | Finviz saved screeners | `SaveScreenDialog.tsx` + `/api/saved` |
| One-page stock detail | TradingView slide-in detail | `/stock/[ticker]` — chart + fundamentals in one scroll |
| Sector visualization | Finviz heat map | `/map` with Graphviz sector tree + sector perf bars |
| Formatters (1.00B, 3.2%) | Finviz table display | `lib/formatters.ts` used everywhere |
| Column selector | Finviz customizable columns | Dropdown in `ResultsTable.tsx` |
| Export CSV | Finviz Elite export | `ExportButton.tsx` — client-side CSV from results |
| Query history | TradingView recent searches | Sidebar in screener + full history in dashboard |

---

## 12. Implementation Phases

### Phase 1 — Backend API (Week 1)
- [ ] Create `api.py` with FastAPI app
- [ ] Add CORSMiddleware (allow `http://localhost:3000`)
- [ ] Extract `process_prompt()` into JSON-serializable return (remove `display_results`)
- [ ] `POST /api/query` endpoint
- [ ] `GET /api/stock/{ticker}` endpoint
- [ ] `GET /api/sectors` + DOT generation
- [ ] `GET /api/columns` endpoint
- [ ] Add `fastapi`, `uvicorn`, `python-jose`, `passlib`, `asyncpg` to `pyproject.toml`
- [ ] JWT auth middleware + Supabase DB connection
- [ ] `POST /api/auth/register`, `POST /api/auth/login`
- [ ] `GET/POST /api/saved`, `GET/POST /api/history` (auth-gated)
- [ ] Test all endpoints with `httpie` or Swagger UI at `/docs`

### Phase 2 — Frontend Foundation (Week 1-2)
- [ ] `npx create-next-app@latest` with JavaScript + Tailwind + App Router (no TypeScript)
- [ ] Install: `shadcn/ui`, `lucide-react`, `zustand`, `@tanstack/react-query`, `recharts`, `@hpcc-js/wasm`, `@supabase/supabase-js`, `react-hook-form`, `zod`
- [ ] Add `globals.css` (user-provided)
- [ ] Set up Zustand stores (`queryStore`, `authStore`, `uiStore`)
- [ ] Build `ui/` base components (Button, Input, Card, Badge, Spinner, ThemeToggle)
- [ ] Build `layout/` (Navbar, PageWrapper)
- [ ] Set up `lib/api.ts` typed fetch wrappers
- [ ] Set up `lib/formatters.ts`
- [ ] Configure Tailwind `darkMode: 'class'` + theme tokens

### Phase 3 — Auth Pages (Week 2)
- [ ] `/auth/login` + `/auth/signup` pages
- [ ] `LoginForm.tsx` + `SignupForm.tsx` with Zod validation
- [ ] Supabase JS client setup in `lib/supabase.ts`
- [ ] `middleware.ts` for route protection
- [ ] User avatar + logout in Navbar

### Phase 4 — Screener (Week 2-3)
- [ ] `/screener` page
- [ ] `QueryInput.tsx` with preset chips
- [ ] `PipelineStatus.tsx` (shows Safety → SQL → Execute)
- [ ] `POST /api/query` integration with TanStack Query
- [ ] `ResultsTable.tsx` (sortable, column selector)
- [ ] `ViewToggle.tsx`
- [ ] `ResultsCards.tsx` + `StockCard.tsx`
- [ ] `MetricBarChart.tsx` chart view
- [ ] `ExportButton.tsx`
- [ ] `SaveScreenDialog.tsx` (auth-gated)
- [ ] Recent queries sidebar
- [ ] URL param `?q=` for shareable query links

### Phase 5 — Stock Detail + Visualizations (Week 3)
- [ ] `/stock/[ticker]` page
- [ ] `StockHeader.tsx`
- [ ] `PriceAreaChart.tsx` + `VolumeBarChart.tsx` (Recharts)
- [ ] `FundamentalsGrid.tsx`
- [ ] `/map` page
- [ ] `SectorTree.tsx` (Graphviz WASM)
- [ ] `SectorPerfChart.tsx` (Recharts)
- [ ] Landing page `/` with hero search + feature highlights

### Phase 6 — Dashboard + Polish (Week 4)
- [ ] `/dashboard` page
- [ ] Saved queries list (run, rename, delete)
- [ ] Full query history (last 50)
- [ ] Mobile responsive review (Navbar hamburger, table horizontal scroll)
- [ ] Loading skeletons for all async states
- [ ] Error boundary components
- [ ] `CodeBlock.tsx` SQL display in screener
- [ ] `StatusBanner.tsx` for pipeline errors (unsafe input, non-finance, etc.)

---

## 13. Key Design Decisions

### Usability over animation
- `isAnimationActive={false}` on all Recharts components
- No CSS transitions beyond hover state changes (`transition-colors duration-150`)
- Instant table sort — no sort animation
- Focus: fast perceived load time, clear hierarchy, readable typography

### Reusability principles
- All colors via CSS vars — never hardcoded hex
- `Badge` component handles all status/sector/change indicators via `variant` prop
- `Card` used for stock cards, saved query rows, fundamentals grid cells — same component
- `Table` component is generic (takes `columns: ColDef[]` + `rows: T[]`) — used in screener and dashboard
- `formatters.ts` is the single source of truth for number display

### Component size limits
- No component file > 200 lines
- If a component needs local state + child components, split into container + presentational

### Error states
- Every async operation has: loading skeleton → data → error with retry button
- Guard model failures show a specific message: *"Query flagged as unsafe — try rephrasing"*
- Non-finance errors: *"This doesn't look like a financial query. Try: 'stocks with P/E under 15'"*

---

## 14. Environment Variables

### Frontend (`.env.local`)
```
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_SUPABASE_URL=https://xxx.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=eyJ...
```

### Backend (`.env` — existing + additions)
```
OPENROUTER_API=sk-or-v1-...         # existing
TOKENIZERS_PARALLELISM=false         # existing
SUPABASE_URL=https://xxx.supabase.co # new
SUPABASE_SERVICE_KEY=eyJ...          # new (service role, server-only)
JWT_SECRET=your-secret-here          # new
FRONTEND_URL=http://localhost:3000   # new (CORS origin)
```

---

## 15. File Structure Summary

```
final-proj/
├── be-project/              # Existing backend (this repo)
│   ├── api.py               # NEW: FastAPI entry point
│   ├── main.py              # Modified: process_prompt returns dict
│   ├── models/              # Unchanged
│   ├── data/                # Unchanged
│   └── ...
│
└── fe-project/              # NEW: Next.js frontend
    ├── app/
    ├── components/
    ├── store/
    ├── hooks/
    ├── lib/
    ├── styles/
    │   └── globals.css      # User-provided
    └── types/
```

---

*Plan authored: April 2026. Ready for implementation.*
