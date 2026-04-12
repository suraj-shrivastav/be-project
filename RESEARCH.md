# Stock Screener UI/UX Research Report

## Overview

A research summary of leading stock screener and financial data web apps — **Finviz**, **TradingView**, **Yahoo Finance**, **Simply Wall St**, and emerging AI-powered screeners — focused on UI/UX patterns worth replicating in a new app.

---

## 1. UI Features (Search, Filters, Charts, Tables)

### Finviz
- **67+ filters** grouped into three categories: Descriptive (market cap, sector, volume), Fundamental (P/E ratio, EPS growth, dividend yield), and Technical (moving averages, chart patterns, RSI)
- Intuitive **dropdown menus** that update the result universe in real time as filters are applied
- A fast **ticker search bar** that returns results in under a second — surfacing a chart, key fundamentals, and a news feed on a single page
- **Customizable column layouts** in the results table; columns can be sorted by any metric
- Ability to **save and reload** custom screener configurations
- **CSV export** of full screener results (Elite tier)
- **Push alerts** for price movements, news, insider trading, and SEC filings

### TradingView
- Dedicated screeners for **every major asset class**: stocks, ETFs, forex, crypto, bonds
- Covers 14,000+ stocks and 4,100+ ETFs for free
- Combines **both fundamental and technical metrics** in a single screener — rare among free tools
- Highly polished, visual-first interface with prolific dropdowns
- Deep **broker integration** — users can trade directly from the screener
- Best-in-class **mobile charting app**, though the screener itself is stronger on web

### Yahoo Finance
- Very **beginner-friendly**, minimal learning curve
- Includes **ESG data filters** alongside standard fundamentals
- **Streaming (live) quotes** even on the free tier
- Lacks depth in technical criteria compared to Finviz or TradingView

---

## 2. Chart Types Used

| Chart Type | Where Used |
|---|---|
| Candlestick | Finviz (free + Elite), TradingView (primary) |
| Line chart | All platforms — default for basic price history |
| OHLC bar chart | TradingView, Finviz Elite |
| Heat map / treemap | Finviz (signature feature) — color-coded by sector, sized by market cap |
| Performance bar charts | Finviz Groups section — sector/industry breakdowns |
| Area / filled line | Yahoo Finance, Robinhood |

### Finviz Heat Map
Finviz's **iconic market heat map** is its most distinctive feature. The entire U.S. market is rendered as a color-coded grid where:
- **Rectangle size** = relative market capitalization
- **Color** (green/red intensity) = price performance over the selected timeframe
- Users can **zoom in** to individual sectors or sub-industries
- Hovering over a ticker shows a quick-view popup; double-clicking opens the full stock page

This gives traders an instantaneous macro read on market-wide capital flows — far faster than scrolling a list.

---

## 3. How Results Are Displayed

| Mode | Description |
|---|---|
| **Data table** | Default for all platforms — sortable columns, ticker + key metrics per row |
| **Chart grid / ticker tape** | Finviz "Charts" view — thumbnail chart for every screened stock in a grid |
| **Heat map** | Finviz map view — spatial, market-wide visualization |
| **Cards** | Yahoo Finance, Robinhood — stock cards with price, change, and mini-chart |
| **Detail panel** | TradingView — clicking a result opens a slide-in detail pane without leaving the screener |

**Best pattern:** Offer multiple **view toggle modes** (table / grid / map) so users can switch based on their workflow. The one-page ticker detail view (chart + fundamentals + news in a single scroll) is universally valued.

---

## 4. Authentication & Account Flow

- **Finviz**: Fully functional free tier with no login required. Email registration unlocks saved screens and alerts. Elite ($24.96/mo annual) adds real-time data, intraday charts, and backtesting. A **7-day free trial** of Elite is offered at signup — no friction barrier.
- **TradingView**: Free account via email or OAuth (Google/Apple). Paywall is gradual — core features are free, premium unlocks more indicators, alerts, and data sources.
- **Yahoo Finance**: Google/Apple OAuth, very low friction. Premium tier (Yahoo Finance Plus) is $29/mo.
- **General pattern**: The best-performing screeners use a **freemium model** — generous free tier, frictionless signup, and a trial-based upgrade path rather than a hard paywall upfront.

---

## 5. What Makes Their UX Good for Financial Data Exploration

| Principle | Implementation |
|---|---|
| **Information density without clutter** | Finviz packs enormous data into one screen without feeling overwhelming — deliberate layout choices, consistent typography |
| **Speed as a feature** | Finviz ticker search returns results in <1 second; filters apply instantly. Speed is a core product value. |
| **No-navigation single-page views** | Clicking a ticker shows chart + fundamentals + news without leaving context |
| **Visual hierarchy** | Heat maps and color coding let users parse market state at a glance before drilling in |
| **Saveability** | Saved screener configurations, watchlists, and alert setups reduce repetitive work |
| **News clustering** | Finviz groups duplicate headlines from different sources under one entry — reduces noise |
| **Preset screens** | Offering pre-built screens (e.g., "High Dividend Yield", "Oversold Momentum") lowers the barrier for new users |

---

## 6. AI / NLP-Based Screeners

This is a rapidly emerging category. Key players and patterns:

### Simply Wall St — NLP Screener
Accepts conversational queries like:
> *"Show me Australian healthcare stocks with market cap above A$1B and a dividend yield of at least 3%"*
> *"US peers of AMZN with market cap of at least $20B and gross profit margin of at least 25%"*

Results return fundamentally filtered companies without the user touching a single dropdown.

### Intellectia AI
- Natural language screening interface
- Provides **daily pre-market top 5 picks** generated by AI
- Targets day and swing traders

### Stox.AI
- Accepts queries like: *"Large cap consumer stocks with improving margins and positive momentum"*
- Translates language into structured filter logic behind the scenes

### Composer
- Users describe a **trading strategy** in natural language
- The AI converts it into an executable algorithm, backtests it, and shows projected performance — all within ~60 seconds

### The NLP Stack (under the hood)
- **Named Entity Recognition (NER)** identifies tickers, metrics (P/E ratio, market cap), and time periods ("last quarter")
- **Intent classification** distinguishes screening queries from lookup or comparison queries
- **FinBERT** (BERT fine-tuned on financial text) significantly outperforms general-purpose NLP on financial terminology
- **Sentiment analysis** extracts signals from earnings calls, filings, and news headlines

---

## Key Takeaways for a New App

1. **Fast, always-visible search bar** — ticker lookup should be instant and omnipresent
2. **Multi-mode results display** — let users switch between table, chart grid, and map/heatmap views
3. **One-page stock detail** — chart + fundamentals + news without a full page navigation
4. **Tiered filters** — group into Descriptive / Fundamental / Technical tabs to reduce cognitive load
5. **Freemium with trial** — generous free tier, friction-free signup, trial-first upgrade path
6. **NLP query input** — a natural language search bar that coexists with traditional filters is a strong differentiator
7. **Saved screens + alerts** — essential for retention; users need to return to their own setups
8. **Heat map / treemap** — even a simple sector-level heatmap dramatically improves the macro exploration experience

---

*Research compiled April 2026. Sources: Finviz, TradingView, StockBrokers.com, Maverick Trading, AlphaLog, Simply Wall St, QuantVPS.*