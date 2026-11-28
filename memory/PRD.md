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
