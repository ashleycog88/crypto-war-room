# Phase 5 — Edge Hunt v2: 6-year walk-forward + new published strategies (phase-5)

## Goal
Find a strategy honest enough for a small real-money trial. User delegated full authority ("you are the boss — make it formidable and earn real money"). Two upgrades over Phases 3–4:
1. **Much longer history** (Binance free archives from 2020 where they exist, ≈ 6 years: 2021 bull, 2022 bear, 2023–26 recovery and chop) judged by **rolling walk-forward** across many unseen windows, with **point-in-time coin sets** (no survivorship bias from using today's winners).
2. **Four new families** from the 2026 web / GitHub / Reddit scan, plus a re-judgement of the 4 watched near misses.
Anything that passes is added to the quiet paper-watch automatically; real money only after live paper confirms it. Research outputs stay private (gitignored). Overview: [../overview.md](../overview.md).

## Part A — Long-history data (pod only)
- REQ-500 [always] The research shall download free Binance archives (data.binance.vision) from 2020-01, or the coin's first listed month, to 2026-11 for spot klines (1h, 4h, 1d) of every Binance USDT pair that traded in that span.
- REQ-501 [always] The research shall download USDⓈ-M perpetual klines (1h, 4h, 1d) and funding-rate history for the same span.
- REQ-502 [always] The research shall download 5m spot klines for every coin that was ever in a point-in-time majors set (interpreted).
- REQ-503 [always] The research shall build monthly point-in-time coin sets from the coins trading at that moment, ranked on trailing 30-day quote volume: "majors" = top 20, "top50" = top 50, excluding stablecoins, leveraged tokens, tokenized stocks/gold (interpreted).
- REQ-504 [always] Point-in-time sets shall include coins that were later delisted whenever their archive data exists.
- REQ-505 [unwanted] If an archive month is missing, then the research shall treat it as no data and log it in the report's data section.
- REQ-506 [unwanted] If a price gap or invalid candle appears, then the research shall drop that candle without inventing prices.
- REQ-507 [always] The research shall reuse the existing cost models from research.py / factors.py: model F (futures 0.05%/side + funding), model S (spot 0.10%/side), 0.05% slippage per fill, and the stress case (1.5× fees, 0.15% slippage).

## Part B — Rolling walk-forward judge ("PASS v2")
- REQ-510 [always] The research shall judge each family by rolling walk-forward: pick settings using only the previous 18 months, trade the next 6 months unseen, then roll forward 6 months, until 2026-11 (≈ 9 unseen windows).
- REQ-511 [always] PASS v2 shall require, on the joined unseen windows after costs: expectancy > 0, profit factor ≥ 1.2, ≥ 100 trades (portfolio families: ≥ 40 rebalances), positive in ≥ 70% of unseen windows, positive over the latest 12 months, beats ≥ 95% of matched random baselines, positive at the 1.5× cost stress, max drawdown ≤ 35%.
- REQ-512 [always] The report shall also show each family's verdict under the old Phase 3 rule (training 2024-11→2026-03, unseen 2026-03→2026-11, both 4-month halves) for comparison.
- REQ-513 [always] The research shall re-judge the 4 watched near misses (S1 long breakout, S5 top-3 momentum, S6 funding factor, S8 vol-managed momentum) with their original pre-registered grids under REQ-510/511 on point-in-time sets.
- REQ-514 [always] Each family shall have ≤ 12 pre-registered setting combinations and a fixed selection rule written before the run; every combination shall be listed in the appendix.
- REQ-515 [always] Random baselines shall be matched on coin set, side, holding time and exposure (portfolios: turnover, coins per leg, cash days), ≥ 500 runs each.

## Part C — New families from the 2026 scan
- REQ-520 [always] S9 "AdaptiveTrend" (arXiv 2602.11708, Feb 2026) shall be implemented with the paper's published parameters and no tuning: 6h candles on Binance perps, ATR trailing stop, monthly rolling-Sharpe selection (γL = 1.3 for the top-15 long universe, γS = 1.7 for shorts), 70/30 long/short capital split, model F costs.
- REQ-521 [always] S9 shall be reported separately for the paper's own period (2022-01→2024-12, "does our build reproduce the paper?") and the post-paper period (2025-01→2026-11, truly unseen to the authors), in addition to REQ-510.
- REQ-522 [always] S10 "Volatility-spike bounce" (kquant.app indicator study, 676 Binance perps 2020–2026) shall go long at the next 5m open after a closed-5m Garman-Klass volatility spike above kquant's adaptive threshold, exit after 12 h, max one open trade per coin, reported with model F and model S.
- REQ-523 [always] S11 "Selective funding carry" shall hold long spot + short perp of the same coin (1× notional each), entering when the mean of the last 3 funding settlements exceeds the entry threshold and exiting below the exit threshold, charging 4 taker legs plus the real spot-perp price gap at entry and exit.
- REQ-524 [always] S11 shall count a liquidation if the short leg's loss exceeds its margin at the tested margin level (1× and 2× leverage on the short leg) before the exit.
- REQ-525 [always] S11 PASS shall require net APR on unseen windows above the Binance USDT flexible-earn benchmark (rate and source stated in the report) by ≥ 3 percentage points, above benchmark in ≥ 70% of windows, zero liquidations at 1×, and beating ≥ 95% of random-entry-time baselines with matched durations (interpreted).
- REQ-526 [always] S12 "Near-miss blend" shall combine the S1-long, S5, S6 and S8 return streams with inverse trailing-60-day-volatility weights, rebalanced weekly, with no tuning, judged under REQ-511 portfolio rules.
- REQ-527 [always] The report shall cite each family's source next to it and state where our build differs from the source.
- REQ-528 [always] The report shall list parked ideas with the reason: cascade-fade 1–3-minute scalper (needs automatic order execution, user trades by hand), 21–23 UTC intraday seasonality (published 2025–26 tests lose after fees), pairs trading (2026 literature negative after two-leg costs), ETF-flow next-day signal (vanishes once timestamps are aligned).

## Part D — Only if something passes (pre-approved)
- REQ-530 [event] When a family gets PASS v2, the live bot shall paper-watch it quietly with its functions imported from the research module (no copy).
- REQ-531 [event] When a passed family starts paper-watch, the next daily summary shall say "🧪 New candidate: <name> — passed the 6-year test, now paper-watching. NOT real money yet."
- REQ-532 [always] The daily summary shall show for each passed candidate a readiness line: days watched, trades (or rebalances), live result after fees, and the backtest's expected 10th–90th percentile range for the same length.
- REQ-533 [state] While a passed candidate has ≥ 30 days watched, ≥ 10 trades (portfolios: ≥ 4 rebalances) and a live result at or above the backtest's 10th percentile, the daily summary shall show "✅ Ready for a small real-money trial: <name>".
- REQ-534 [unwanted] If a passed candidate's live result falls below the backtest's 10th percentile, then the daily summary shall show "⚠️ <name> is doing worse than its test — not ready".
- REQ-535 [unwanted] If nothing passes, then the system shall leave the live bot, workflows and state unchanged.
- REQ-536 [unwanted] If candidate data fetches fail, then the system shall skip that update, keep positions, and not slow the main cycle (≤ 2 s added).

## Shared honesty rules
- REQ-540 [always] Every trade shall pass a no-look-ahead assertion (signal uses only closed candles before entry; selection uses only data before the window).
- REQ-541 [always] Every point-in-time set shall pass an assertion that no coin is selected in a month before it traded.
- REQ-542 [always] The report shall include 2 hand-checked trades per new family.
- REQ-543 [always] The report (private_reports/edge_v2/, gitignored) shall start with ≤ 6 plain lines and end with a plain recommendation including "past results don't guarantee future results".
- REQ-544 [always] The full test suite shall stay green with new tests for the data builder, walk-forward splitter, each new family and (if built) Part D.

## Quality requirements
```
Q-50 Tag: Trust.NoLookAhead
     Scale: trades or selections using any data at/after their decision time
     Meter: automated assertion over every trade, selection and point-in-time set
     Must: 0   Plan: 0
Q-51 Tag: Trust.Survivorship
     Scale: later-delisted coins with archive data that are missing from the point-in-time sets they qualified for
     Meter: universe audit table in the report
     Must: 0   Plan: 0
Q-52 Tag: Trust.MultipleTesting
     Scale: total setting combinations tried across Phase 5 (listed in appendix)
     Meter: appendix count
     Must: ≤ 100   Plan: ≤ 70
Q-53 Tag: Speed.ResearchRun
     Scale: minutes for the full Phase 5 run on the pod (after data is cached)
     Meter: timed run
     Must: ≤ 120   Plan: ≤ 45 (estimate)
Q-54 Tag: Isolation.LiveCycle (Part D only)
     Scale: added seconds per live scan cycle from new paper-watch candidates
     Meter: 5 live dry cycles before/after
     Must: ≤ 2 s   Plan: ≤ 0.5 s
Q-55 Tag: Trust.MoneyClaims
     Scale: user-facing lines that call a strategy profitable/tradeable without PASS v2 plus live readiness
     Meter: review of report + daily-summary copy
     Must: 0   Plan: 0
```
