# Markets — UX Spec for the Frontend

The backend now supports a **market scope** (Global / India / Nasdaq) on every query. This document is the contract the frontend builds against.

The UX principle: **market is locale, not a filter chip**. It's a persistent ambient setting (like region on global e-commerce sites), always visible, never modal. No market gets default favoritism — Global is the unbiased default.

---

## 1. Available markets

`GET /api/markets` →

```json
{
  "markets": [
    {
      "key": "global",
      "label": "Global",
      "description": "All listed markets",
      "currency": "mixed",
      "currency_symbol": "¤",
      "exchanges": []
    },
    {
      "key": "india",
      "label": "India",
      "description": "NSE and BSE listings",
      "currency": "INR",
      "currency_symbol": "₹",
      "exchanges": ["NSE", "BSE"]
    },
    {
      "key": "nasdaq",
      "label": "Nasdaq",
      "description": "Nasdaq-listed companies",
      "currency": "USD",
      "currency_symbol": "$",
      "exchanges": ["NASDAQ"]
    }
  ],
  "default": "global"
}
```

Frontend caches this on first load. Adding a new market in the future (e.g. LSE) is a backend-only change — the dropdown auto-grows.

---

## 2. The market selector — design

### Placement
Top-right of the global header, immediately left of the user avatar. **Always visible.** Same z-layer as the chat composer so users never lose track of context.

### Visual

```
┌─────────────────────────────────────────────────────────────────┐
│  [Logo]  Stock Screener         [Global ▾]  [🔍]  [👤]         │
└─────────────────────────────────────────────────────────────────┘
```

Closed state: shows the current market label + chevron. ~80px wide. Subtle border, no fill — it's ambient, not a CTA.

### Open state

```
┌─────────────────────────────────────┐
│  ▸ Global                           │
│    All listed markets               │
├─────────────────────────────────────┤
│    India          ₹                 │
│    NSE and BSE listings             │
├─────────────────────────────────────┤
│    Nasdaq         $                 │
│    Nasdaq-listed companies          │
└─────────────────────────────────────┘
```

- Currency symbol on the right of each row gives instant visual recognition.
- Description under each label — beginners learn what BSE/NSE/Nasdaq mean implicitly.
- Active row gets a subtle accent stripe on the left, not a full highlight (preserves scan-ability).
- Keyboard: `↑` `↓` to navigate, `Enter` to select, `Esc` to close.

### Microcopy
- Tooltip on hover (closed state): *"Change market scope — affects screening results, chat, and presets."*

---

## 3. Persistence

```ts
// On selection change
localStorage.setItem("market", market.key);

// On app boot
const saved = localStorage.getItem("market");
const market = saved && KNOWN_MARKETS.has(saved) ? saved : "global";
```

- **Default = `"global"`** if nothing saved. No first-visit nag, no geo-detect, no signup prompt.
- Optional: if the user is signed in, sync to `user_events` so the preference follows them across devices. Not MVP.

---

## 4. Wiring market into requests

Every request that hits a market-aware endpoint sends `market` either as JSON body field or query param.

| Endpoint | Method | How to send |
|---|---|---|
| `/api/query` | POST | body: `{"prompt": "...", "market": "india"}` |
| `/api/query/stream` | POST | body: `{"prompt": "...", "market": "nasdaq"}` |
| `/api/chat` | POST | body: `{"message": "...", "session_id": "...", "market": "india"}` |
| `/api/presets` | GET | query: `?market=india` |

Helper:
```ts
async function apiPost<T>(url: string, body: object): Promise<T> {
  const market = localStorage.getItem("market") || "global";
  return fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...body, market }),
  }).then(r => r.json());
}
```

---

## 5. Presets — adapt to selected market

The home page surfaces 5–6 preset chips. They re-render whenever the market changes.

```
Market = Global:
  [Blue-chip leaders]  [High growth]  [Cheap value plays]
  [Dividend yields]    [Safe & stable]  [Growing tech worldwide]

Market = India:
  [Sensex / Nifty blue chips]  [Safe Nifty dividend stocks]
  [Fast-growing Indian]        [Cheap Indian large-caps]
  [Indian IT leaders]          [Indian banking stocks]

Market = Nasdaq:
  [Nasdaq mega-cap tech]   [Fast-growing Nasdaq names]
  [Cheap Nasdaq stocks]    [Safer Nasdaq picks]
  [Nasdaq dividend payers] [Semiconductor leaders]
```

**Why dynamic presets matter:** "Growing tech" means different things in India (Infosys, TCS) vs Nasdaq (NVDA, AMD). Showing the same generic preset across markets would feel hollow. Tailored copy = "the app knows where I'm looking."

Implementation: refetch `/api/presets?market={key}` when the dropdown changes. Cache by market key.

---

## 6. Result rows — show provenance

Each stock row in the result table renders a small **exchange chip** to the right of the ticker:

```
AAPL [NASDAQ]    Apple Inc.    $187.32 ▲ 1.4%   Tech
RELIANCE [NSE]   Reliance      ₹2,431  ▼ 0.6%   Energy
TCS [NSE]        TCS           ₹3,890  ▲ 0.2%   Tech
```

Chip styling:
- `NASDAQ` — blue (`#0070ba`-ish)
- `NSE` — orange/saffron (`#f37021`)
- `BSE` — red (`#e91e1e`)
- Subtle, not loud. ~10px font, rounded.

**Currency rendering rule:** use the row's own `currency` field, not the market's. In Global view, ₹ and $ appear side-by-side. Don't convert — that introduces phantom precision.

---

## 7. Empty / mismatch states

### A. No matches in current market
```
No matches for "high-growth tech" in Nasdaq.

[Switch to Global ↗]  or  [Try a preset ↓]
```

The "Switch to Global" CTA is the key UX — turns a dead-end into a 1-click escape.

### B. User mentions a stock not in the selected market
Detected backend-side via the chat agent: when user says "Reliance" with Nasdaq selected, the LLM responds with:

> "Reliance Industries is listed on NSE/BSE — not in your current Nasdaq scope. Want to switch to India to see it?"

Frontend renders this as text (no special handling needed) but can post-process to add a clickable chip:

```tsx
// detect "switch to <market>" phrases in agent text → render chip
if (text.match(/switch to (India|Global|Nasdaq)/i)) {
  return <SuggestedSwitchChip target={...} />
}
```

Optional polish, not required for MVP.

### C. Clarify response (non-finance / ambiguous)
Backend already returns:
```json
{
  "type": "clarify",
  "question": "What matters most to you?",
  "presets": [...]
}
```

Frontend renders the question above 4 chips. Clicking a chip re-submits with `{prompt: "<chip label>", market}`. Same SSE stream resumes.

---

## 8. Stock detail page

`GET /api/stock/{ticker}` doesn't take a `market` param — a stock has one canonical exchange. But the page should:

1. Render the exchange chip prominently next to the ticker.
2. Use the row's `currency` symbol throughout.
3. If the stock's `country` ≠ user's selected market, show an inline note:

```
You're viewing TCS (NSE / India) while in Nasdaq scope.
[Pin to India ↗]
```

The "Pin" CTA switches the dropdown — small but pleasant.

---

## 9. Chat UX — currency in narratives

The chat agent receives the market context automatically (backend injects). It will respond in the right currency:

- India scope: "Reliance trades at ₹2,431 with a P/E of 24.6x..."
- Nasdaq scope: "AAPL trades at $187.32, P/E around 28x..."
- Global scope: "Apple ($187, 28x P/E) trades at a similar multiple to Reliance (₹2,431, 24x P/E)..."

No frontend work — this is automatic from the system prompt. **Verify** during testing that the agent doesn't slip currency.

---

## 10. Edge cases / polish

### A. Stale localStorage value
If `localStorage.market = "lse"` (a market that was added then removed), the backend coerces unknowns to `"global"`. Frontend should mirror — on app boot, validate the saved key against the markets list.

### B. Race conditions on dropdown change mid-query
Pattern: when user changes market while a query is in-flight, abort the old fetch and refire with the new market. SSE: close the EventSource and reopen.

### C. Skeleton loading for presets
First load: render 6 placeholder chips immediately, replace with real labels when `/api/presets?market=…` returns. Avoids layout shift.

### D. Mobile
Dropdown collapses into the hamburger menu on `<640px`. Show the current market as a chip in the chat input area for visibility.

### E. Analytics
Log `market_changed` events to `user_events` (frontend → `POST /api/history` or a new `/api/event` endpoint — TBD). Useful to learn which market is actually used most.

---

## 11. Files the frontend will need

```
src/
├── lib/
│   ├── markets.ts           # types + KNOWN_MARKETS set + helpers
│   └── api.ts               # apiPost / apiStream wrappers that auto-attach market
├── components/
│   ├── MarketSelector.tsx   # dropdown component
│   ├── ExchangeChip.tsx     # NSE/BSE/NASDAQ pill
│   └── PresetGrid.tsx       # market-aware preset chips
└── stores/
    └── marketStore.ts       # zustand/context with localStorage sync
```

---

## 12. Acceptance checklist

A novice teammate, no docs, should be able to:

1. ✅ See the market dropdown in the header on first load.
2. ✅ See "Global" selected by default with no nag.
3. ✅ Click the dropdown, see 3 options with descriptions and currency hints.
4. ✅ Switch to "India" — presets immediately re-render with Indian-specific labels.
5. ✅ Type "safe stocks" — get NSE/BSE results with ₹ prices and NSE/BSE chips.
6. ✅ Switch to "Nasdaq" — same prompt now returns AAPL/MSFT/etc. with $ prices.
7. ✅ Switch to "Global" — see a mix, each row showing its own exchange + currency.
8. ✅ Refresh the page — selection persists.

If all 8 land, the market UX is shipped.
