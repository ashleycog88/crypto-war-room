# PRD — Crypto War Room bot v10

## Original problem statement
Phase 1 build brief: harden the Crypto War Room bot (Python, GitHub Actions, Telegram), with no web app. Full specs are in `memory/product/overview.md`, `memory/product/phase1/requirement.md` (REQ-1..103, Q-1..9) and `memory/product/phase1/journeys.md` (JRN-1..53).

## Architecture
- `war_room_bot.py`: one file containing pure functions (indicators, scoring, grades, trade resolution, fees, coin list), data routers (Binance data-api → KuCoin → OKX → Kraken → Gate → Bybit; derivatives Hyperliquid → OKX → Gate → KuCoin Futures → Binance fapi), Telegram, Gemini AI review, atomic state, git sync, and the modes `--watchdog`, `--diagnose`, `--commit-state` and `--test-messages`.
- Workflows: `war_room.yml` (hourly at :07, 58-minute loop), `watchdog.yml` (every 2 h at :37), `diagnostics.yml` (manual).
- Tests: `tests/` (81 offline pytest tests; `fakes.py` holds the shared fakes).

## Implemented (2026-11-27)
- Phase 1 is complete. 81 offline tests pass.
- Live checks from the pod:
  - Dry run.
  - Telegram: JRN-30 (2 messages) and JRN-31 (5 messages), all HTTP 200.
  - Gemini 3.5-flash-lite: OK.
  - Diagnostics.
- Coin list now leaves out Binance tokenized stocks (permission group TRD_GRP_261), gold tokens (XAUT, PAXG) and the new stablecoins (U, RWUSD). Extra coins can be excluded with `EXCLUDE_COINS`.

## Backlog
- Done 2026-11-27, Phase 2: `backtest.py` (3 months, 50 coins, scored with the live code). Result: every variant lost money after fees on both the training months and the unseen month; 0 of 50 coins were profitable. Report: `backtest_report.md`.
- Done 2026-11-27: AI fallback chain `gemini-3.1-flash-lite` → `gemini-3.6-flash`, configurable with `LLM_FALLBACK_MODELS`, at most 30 s per alert. Fixed the flaky test (fake clock was tied to wall-clock time); 20 of 20 full-suite runs pass.
- Note: gemini-3.6-flash works but took about 31 s live, so it usually won't fit in the time budget.
- P0: user sets the GitHub secrets and pushes the files.
- P1: Phase 2. Backtest that imports `evaluate_pair`, `resolve_trade` and `net_pnl`.
- P2: Phase 3 edge watchers. Phase 4 apply the chosen rules.
- Done 2026-11-28: Phase 2b quiet mode. The workflow defaults QUIET_MODE to 1 via repo variable. Paper-tracking and the AI gate keep running; per-trade messages are suppressed. Daily summary at 12:01 PM India time (Asia/Kolkata). The "🕐 time updated" notice goes out once whenever the configured time differs from the one last announced, time-zone aware and DST-safe, changeable with the DAILY_SUMMARY_TIME/DAILY_SUMMARY_TZ variables; a missed one is sent once at the next run start. One-time quiet notice; BOT ALIVE shows (quiet mode). Suite: 120 passed, 3 runs out of 3.
- Done 2026-11-28 (Phase 2c): self-chaining via workflow_dispatch at minute 50 (healthy cycles only, at most one waiting run, one retry); a 🔗 line in BOT ALIVE; Healthchecks.io steps in the README; time-zone-aware daily summary.
- Done 2026-11-28 (Phase 3b+3c): recorder.py plus recorder.yml writes encrypted hourly chunk files to the orphan `data` branch (Hyperliquid market and smart wallets, news with its own AI bucket and daily cap, listings, whale stream, OKX liquidations and long/short, context). datalock.py does AES-256-GCM + scrypt with a committed salt, plus a decrypt CLI. state.json.enc with safe migration; a wrong key never overwrites. BOT_SETTINGS secret overrides; quiet public logs (LOG_DETAIL=1 for detail); backtest outputs gitignored and moved to private_reports/.
- Done 2026-11-28 (Phase 3 research A): research.py runs on cached 1h archives, 2 years, train 16 months / unseen 8 months, 26 pre-registered settings × sides, two coin sets (20 majors, top 50). Cost models F and S, 1.5× stress, turnover-matched random baselines (200 runs per S1–S4 setting, 2000 per S5 setting, seeded per test). Result: NO strategy passed. Nearest miss: S5 momentum on the majors (94.85th percentile vs the 95 needed; it lost 52% in training). Outputs are only in private_reports/research/ (gitignored). 167 tests pass; live code unchanged.
- Next: Phase 3+ research on the recorder's edge data once enough of it has built up (weeks), with no live rule changes until then.
- Done 2026-11-28 (Phase 4 Part A): paper_watch.py quietly paper-watches S1 4h breakout longs (n=20, channel exit, BTC filter) and S5 top-3 momentum on the majors, using research.py's own s1_setup/s5_matrix/s5_top/net. It runs in a background thread every 4h; the daily summary shows a 🔭 block; state is encrypted; BOT_SETTINGS PAPER_WATCH/WATCH_S1/WATCH_S5 override it. On the real 2-year majors data it matches research S1-LONG exactly (920 = 920 closed trades). New 🩺 Healthchecks status line in BOT ALIVE and the daily summary. 183 tests pass (3 out of 3 runs).
- Next: Phase 4 Part B (C1 wallet copying, C2 listing events) once root says go.
- Done 2026-11-28 (Phase 4 Part B, Step C research): step_c_data.py (600 stratified Hyperliquid wallets, 53% losers; announcements from Binance CMS 48/161/49, OKX and KuCoin; Upbit returns 403) and step_c.py (C1 copying walk-forward with delays 1/5/60 min on Binance 1m; C2 events, R1–R3 rules). Result: NOTHING passed. C1 fails on random entries (66–72th pct; without the single top wallet the average is −0.86%). R3 delisting/monitoring short reaches the 86th pct (a false PASS from 1h re-pricing was caught by the tester and fixed). Outputs only in private_reports/step_c/. 193 tests pass.
- Next: Part C (S6 funding factor, S7 CTREND, S8 vol-managed momentum) once root says go.
- Done 2026-11-28 (Phase 4 Part C): factors.py tests S6 funding factor, S7 CTREND-style trend and S8 vol-managed momentum on majors and top-50, with turnover-matched random portfolios (1000 runs). Result: NOTHING passed. Nearest: majors S7 monthly long-short (+24% unseen, but only 7 rebalances and random 93); top-50 S6 (+21.5%, random 87); top-50 S8 long (+49%, random 69). Outputs in private_reports/factors/. 197 tests pass. Files to save with step_c: factors.py, tests/test_factors.py.

## 2026-11 — Phase 4 Part D (paper-watch S6 + S8)
- paper_watch.py: S6 top-50 funding factor (look 14, q 5, weekly, futures model) + S8 top-50 vol-managed momentum (long, 84-day window, weekly, spot model); decisions/accounting from factors.py (weights/period/is_reb, calendar anchor); funding from Hyperliquid hourly ×8; catch-up-safe funding window; held coins priced after leaving top list; S6/S8 failures never stop S1/S5.
- war_room_bot.py: WATCH_S6/WATCH_S8 switches, top list passed to updater, note logging (n_notes), "🔭 Now watching 4 near-miss strategies." once.
- Tests 202 green 3×; testing agent iterations 11–13 (HIGH funding-window bug found and fixed).
- Next: recorded-data research in ~4 weeks.
- 2026-11: S6/S8 eligibility = current top-50 (+BTC for the regime filter) at each rebalance; held coins outside it are sold at that rebalance (priced normally) and can be re-picked after re-entry. Test added; 203 green 3×; testing agent iteration 14 passed (mutation-checked).

## 2026-11 — Phase 5 "Edge Hunt v2" (research only)
- edge_data.py: Binance archives 2020-01→2026-11 (spot 1h 593 USDT pairs incl. delisted, perp 1h + funding 918, 5m for PIT majors months), Hyperliquid funding top-up for Nov 2026, monthly point-in-time majors/top50 sets (82 months, 0 PIT violations, Q-51 missing 0), relaunch/redenomination splits. Cache backtest_data/edge_v2 (~0.8 GB, gitignored).
- edge_v2.py: rolling walk-forward PASS v2 (11 unseen windows), old Phase 3 rule side by side, S1/S5/S6/S8 re-judged, S9 AdaptiveTrend, S10 vol-spike, S11 carry, S12 blend; 43 settings; report private_reports/edge_v2/.
- Result: NOTHING passed PASS v2 → Part D not built; live bot/workflows/state untouched. Tests 219 green 3×; testing agent iterations 15–16.
