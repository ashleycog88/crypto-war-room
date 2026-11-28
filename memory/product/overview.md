# Requirements: Crypto Quant War Room Bot (v10)

## Core Value
A free, always-on, fail-safe Python bot on GitHub Actions that scans the top 50 Binance USDT coins with live Binance spot data + derivatives data, sends trustworthy graded Telegram trade alerts (A/B) the user can take on Binance Spot (longs) and Binance Futures (longs + shorts), tracks each trade to a real after-fee outcome, proves it is alive every hour, and whose rules (coins, hold times, extra signals) are justified by data — a 3-month backtest plus live forward tracking. NO website, NO app screen, NO backend server — only bot files in the repo root.

## Personas & Roles
- Trader (repo owner, non-technical, trades manually on Binance.com spot AND futures, standard fees, wants real profit, zero spend unless essential): receives Telegram alerts, reads win rates, edits GitHub secrets. Only persona.

## Features
| id | feature | persona | phase |
|---|---|---|---|
| F-1 | Order book score works: size from b[1]/a[1], limit=20 (KuCoin only 20 or 100; 20 valid everywhere) | Trader | 1 |
| F-2 | Volume, RSI, EMA, VWAP read from last CLOSED 5m candle; 1h trend from last closed 1h candle (interpreted) | Trader | 1 |
| F-3 | Fetch 200+ candles (5m: 300, 1h: 250) so EMA50 is accurate | Trader | 1 |
| F-4 | Two alert grades clearly labelled: A-GRADE (80+) and B-GRADE (65–79) | Trader | 1 |
| F-5 | AI review fixed: res.choices[0].message.content, free Gemini via OpenAI-compatible URL, exact model name given; REJECTED drops the alert | Trader | 1 |
| F-6 | All dynamic text escaped so Telegram never rejects a message (+ plain-text fallback, 4096 cap, send throttle) | Trader | 1 |
| F-7 | Trade tracking from candle highs/lows since entry; TP1 → bank half + SL to breakeven; max-hold auto-close; separate A/B records | Trader | 1 |
| F-8 | state.json saved and committed every run without push conflicts | Trader | 1 |
| F-9 | One GitHub Actions run per hour, scanning every 60 s inside, runs never overlap | Trader | 1 |
| F-10 | Hourly "BOT ALIVE" Telegram with scans done and closest near-miss scores | Trader | 1 |
| F-11 | Fees subtracted from every recorded result: Binance standard — LONG recorded at spot 0.10% each way (conservative; valid for spot or futures), SHORT at USDⓈ-M futures taker 0.05% each way; configurable | Trader | 1 |
| F-12 | Live derivatives watch: funding rate, open-interest 1h change, BTC market regime; multi-source fallback; shown on alerts + heartbeat; scoring use behind a switch until proven | Trader | 1 (data) / 2 (proof) |
| F-13 | Fail-proofing: Telegram-before-record, timeouts everywhere, exchange + data-source fallbacks, missing-pair reporting, candle sanity checks, Telegram 429 handling | Trader | 1 |
| F-14 | Security hardening: token masked in logs, actions pinned to SHAs, least-privilege permissions, no secret ever printed | Trader | 1 |
| F-15 | Watchdog workflow: "BOT DOWN" Telegram if no completed cycle for 2 hours; optional free Healthchecks.io ping (HEALTHCHECK_URL) | Trader | 1 |
| F-16 | One-click diagnostics workflow reporting which spot exchanges / derivatives sources / Telegram / Gemini work from GitHub's runners | Trader | 1 |
| F-17 | Binance as primary price source via the public market-data host that works from US runners (data-api.binance.vision), with KuCoin → OKX → Kraken → Gate.io → Bybit fallbacks | Trader | 1 |
| F-18 | Coin universe = top 50 Binance USDT pairs by 24h quote volume (no stablecoins, leveraged tokens or fiat pairs), refreshed every run | Trader | 1 |
| F-19 | Market labels on alerts: LONG = "Spot or Futures", SHORT = "Futures only" | Trader | 1 |
| F-20 | Backtest: last 3 months, all 50 coins, same scoring code as the live bot, after fees + slippage, A vs B, with/without derivatives, walk-forward check, per-coin ranking | Trader | 2 |
| F-21 | Data-driven exits: backtest compares hold times and exit styles per grade; recommends values | Trader | 2 |
| F-22 | Plain-words results report + suggested rule changes; user decides | Trader | 2 |
| F-23 | Edge watchers: news headlines rated by free Gemini, Binance big-order (whale trade) flow, top-winning Hyperliquid wallets' net positioning — shown on alerts, logged for forward-testing, score only once proven | Trader | 3 |
| F-24 | Apply the rule changes, coin list and hold times the user picks | Trader | 4 |
| F-25 | Kid-level GitHub settings guide + how to save files to GitHub (paid button and free copy-paste path) | Trader | 1 and 4 |
| F-26 | Quiet mode: paper-track every AI-approved setup silently; one plain daily summary at 09:00 IST instead of per-trade messages; BOT ALIVE / BOT DOWN unchanged | Trader | 2b |
| F-27 | Strategy research lab: test several pre-registered strategy families on 1h/4h/daily candles over ~2 years, honest walk-forward, random-entry baseline, futures-fee and spot-buy-fee models; plain report | Trader | 3 |
| F-28 | Edge Hunt v2: ≈ 6-year point-in-time history, rolling walk-forward "PASS v2", re-judge near misses, new 2026 literature families (S9–S12) | Trader | 5 |
| F-29 | Auto paper-watch for anything that passes + daily "real-money readiness" line (✅ ready / ⚠️ not ready) | Trader | 5 |

## Phases
- Phase 1 → working, hardened live bot on Binance data, top 50 coins, temporary hold times A 24h / B 4h (configurable), verified offline + real Telegram test messages → F-1..F-19, F-25
  - [phase1/requirement.md](phase1/requirement.md)
  - [phase1/journeys.md](phase1/journeys.md)
- Phase 2 → 3-month backtest of the exact live rules; results + recommendations in plain words → F-12 (proof), F-20, F-21, F-22
  - [phase2/requirement.md](phase2/requirement.md)
  - [phase2/journeys.md](phase2/journeys.md)
  - Phase 1 status: DONE, tested (82 builder tests + independent tester 9/9), saved to GitHub main 2026-11-27 20:25 UTC; diagnostics run succeeded; first v10 run started 20:28 UTC.
  - Phase 1b + code-review fixes: DONE, retest 10/10, merged to GitHub main (PR #1, 19e7464) 2026-11-27 21:27 UTC.
  - Phase 2 status: DONE. RESULT = NO EDGE: all 19 variants lost after fees on training and unseen month (live rules −0.31 R/trade, ~44% win, ~350 signals/day; before costs ≈ coin flip). User told honestly; chose: research bigger-timeframe strategies, quiet mode meanwhile.
- Phase 2b → Quiet mode for the live bot: paper-tracking continues, per-trade messages replaced by one daily summary (09:00 IST) → F-26
  - requirements: phase2/requirement.md section "Phase 2b"; journeys JRN-75..80 in phase2/journeys.md
- Phase 3 → Strategy research on bigger timeframes (1h/4h/daily), several pre-registered strategy families, 2 years of data, futures + spot-buy fee models, walk-forward + random-entry baseline; keep only what passes on unseen data → F-27
  - [phase3/requirement.md](phase3/requirement.md)
  - [phase3/journeys.md](phase3/journeys.md)
- Phase 4 → If a strategy passes: implement it in the live bot as paper alerts first (then edge watchers: news, whale trades, smart-money wallets — old F-23), apply user's choices → F-23, F-24, F-25

## Roadmap approved 2026-11-27 ("make it formidable", time no object, $0 unless essential)
- A = Phase 3 strategy research (bigger timeframes, 2y data) — starts after Phase 2c save.
- B = Phase 3b edge-data recorder (news+AI ratings, Hyperliquid top-100 wallets positions/fills, Binance whale trades, liquidations, long/short, listings, market context) → `data` branch — starts ASAP (time-bound).
  - [phase3b/requirement.md](phase3b/requirement.md), [phase3b/journeys.md](phase3b/journeys.md)
- C = smart-money copy research (walk-forward wallet selection), listing/delisting event study (Upbit/Binance/OKX; post-listing fade), macro-event filter (FOMC/CPI).
- D = combine only proven signals into one War Room score + pro risk rules (0.5–1% risk/trade, max open trades, correlation cap, daily loss stop) → 4 weeks paper trading.
- E = private always-on server: Oracle Cloud Always Free (A1 Ampere, now 2 OCPU/12 GB; idle-reclaim risk → consider Pay-As-You-Go with budget alert, still $0), home region: user could only select a US region (recommended US Midwest Chicago or US West San Jose for A1 capacity; Ashburn/Phoenix acceptable) → same US data constraints as GitHub runners (data-api.binance.vision, Binance fapi blocked; Hyperliquid/OKX/Gate/KuCoin for derivatives); repo made private afterwards; Healthchecks.io outside watchdog. User chose to PLAN IT NOW (account signup now, build after A/B).
- User decisions: no Solana/Ethereum memecoin wallets; daily summary 12:01 PM India time (Asia/Kolkata).
- Oracle signup failed (form wouldn't let the user continue) → user chose Plan B now: Phase 3c privacy lock on free GitHub (encrypted state + data, hidden BOT_SETTINGS, quiet logs); Oracle/private server decided later.
  - [phase3c/requirement.md](phase3c/requirement.md), [phase3c/journeys.md](phase3c/journeys.md)
- Status 2026-11-28: Phase 2c (self-chaining, 12:01 IST summary) merged PR #3, verified on GitHub (github-actions[bot] dispatches). Phase 3b+3c merged PR #4: state.json.enc live, `data` branch recording (Upbit 403 expected). Phase 3 research: NO strategy passed (near misses: S1 breakout longs on majors failed "both halves"; S5 top-3 momentum majors 94.85th pct vs 95 needed). User chose BOTH paper-watching the near misses and Step C research.
- Phase 4 → Part A paper-watch S1/S5 (daily summary only) + Part B Step C research (C1 Hyperliquid wallet copying, walk-forward; C2 listing/delisting events)
  - [phase4/requirement.md](phase4/requirement.md), [phase4/journeys.md](phase4/journeys.md)
  - Status 2026-11-28 14:45 UTC: Part A merged (PR #5, retest 6/6). Part B: C1 + C2 all REJECTED (C1 ≈ random, one wallet drove profits; C2 R3 fragile, fake pass caught). Part C: S6/S7/S8 all REJECTED (best: top-50 S8 +49% unseen @69th pct; top-50 S6 +21.5% @86.6th pct). Part D: S6+S8 top-50 added to paper-watch (current top-50 only; BTC always eligible), retest 5/5, merged (PR #6, main 94d5dc0). Live: state.json.enc, recorder fresh, self-chaining OK, Healthchecks connected.
  - NEXT (≈ 2026-12-26): recorded-data research (news AI ratings, liquidations, wallet crowd positioning) + review 4 weeks of live paper-watch. Oracle/private server not needed unless a strategy passes or needs sub-minute speed.
- Phase 5 → Edge Hunt v2 (user 2026-11-29: "you are the boss, make it formidable and earn real money"; full delegation): ≈ 6-year Binance history (2020→2026) with point-in-time coin sets, rolling walk-forward (18-month look-back → 6-month unseen, ≈ 9 windows, "PASS v2"), re-judge S1/S5/S6/S8, new families S9 AdaptiveTrend (arXiv 2602.11708), S10 volatility-spike bounce (kquant.app), S11 selective funding carry vs USDT-earn benchmark, S12 near-miss blend; anything that passes → quiet paper-watch + "real-money readiness" line; nothing passes → live bot unchanged → F-28, F-29
  - [phase5/requirement.md](phase5/requirement.md), [phase5/journeys.md](phase5/journeys.md)
  - Parked with reasons: cascade-fade 1–3 min scalper (needs auto execution), 21–23 UTC seasonality, pairs trading, ETF-flow signal.

## UI/UX Rules
- The only "UI" is Telegram messages (and reports relayed in chat). HTML-formatted, every dynamic value escaped, emojis kept.
- Grade obvious in the first line ("A-GRADE" / "B-GRADE"); market label obvious ("Spot or Futures" / "Futures only").
- Prices shown are Binance prices whenever Binance data is reachable; the source exchange is shown otherwise.
- Results always shown after fees.
- Never build a website, app, React frontend or FastAPI backend.

## Integrations
| service | use | cost | key/secret |
|---|---|---|---|
| Telegram Bot API | alerts | free | TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID (in GitHub secrets; user provides them here for the live test) |
| Google Gemini free tier via `https://generativelanguage.googleapis.com/v1beta/openai/` | AI review (later: news rating) | free (never link billing) | LLM_API_KEY, LLM_BASE_URL, LLM_MODEL=`gemini-3.5-flash-lite` (fallback `gemini-3.8-flash`). User has no key yet — bot must work without it. |
| Binance public market data `https://data-api.binance.vision` (api.binance.com / fapi return 451 from US runners) | candles, order book, tickers, top-50 list | free, no key | none |
| ccxt public spot fallbacks: KuCoin → OKX → Kraken → Gate.io → Bybit | fallback prices | free | none |
| Public derivatives (no key), fallback chain chosen by builder after probing — e.g. Hyperliquid info API (one call for all coins), OKX, KuCoin Futures, Gate.io, Kraken Futures, Binance fapi (works only if not geo-blocked) | funding rate, open interest | free | none |
| GitHub Actions (public repo) | runner, watchdog, diagnostics | free, unlimited for public repos | built-in GITHUB_TOKEN |
| Healthchecks.io (optional) | outside dead-man switch | free tier | HEALTHCHECK_URL (optional) |
| Phase 3 (later): crypto news RSS feeds, Binance aggTrades, Hyperliquid leaderboard + clearinghouseState | edge watchers | free | none |

User directive: spend nothing unless absolutely essential; keep every free alternative.

## Edge Cases
- AI key missing / 429 / wrong model / empty reply / timeout → alert sent with "AI unavailable" (only explicit REJECTED drops it).
- Gemini thinking tokens can eat max_tokens → low reasoning effort, enough max_tokens, handle None.
- SL and TP inside the same candle → SL first.
- Old v9 state.json must load and upgrade automatically; state.json is NOT edited here.
- GitHub cron late or skipped → concurrency queue, back-to-back runs, watchdog.
- User edits files on GitHub → pull-rebase retry; bot's state.json wins.
- Binance main API and many derivatives APIs block US (GitHub runner) IPs → data-api host + fallback chains; heartbeat shows sources in use.
- Coin drops out of the top 50 while a trade is open → trade is still tracked to the end.
- Coin missing on a fallback exchange / derivatives source → reported, never silent.
- 50 coins every 60 s → parallel fetching, caching (1h candles only refreshed when a new 1h candle closes), rate-limit headers respected, cycle under 45 s.
- Many alerts at once → Telegram send throttle (~1 msg/s) + 429 handling.
- SHORT signals on coins without a futures market → still labelled "Futures only"; if futures listing is known absent, no SHORT alert (interpreted).
- Telegram send fails → trade not recorded, cooldown not started.
- Stale/duplicate/NaN candles → rejected.
- Public-repo scheduled workflows can be disabled after 60 days of no activity → hourly state commits keep activity; guide covers re-enabling.

## Seed Data
None. state.json in the repo is live production state and must NOT be modified in this environment.
