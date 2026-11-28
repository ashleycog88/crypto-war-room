# Phase 2 — 3-month backtest of the live rules (phase-2)

## Goal
Answer honestly, with data, "would the v10 rules have made money after fees?" — by replaying the EXACT live scoring and trade-resolution code over the last 3 months for the top-50 coins, comparing grades, sides, hold times, exit styles and derivatives filters, checking the winner on a month it never saw, and producing a plain-words report with suggested rule changes. No change to live bot behaviour in this phase (Phase 4 applies what the user picks). See `/app/memory/product/overview.md`; Phase 1 delivered `war_room_bot.py` with importable pure functions.

## Functional requirements

### Data
- REQ-104 [always] The backtest shall use Binance spot 5m and 1h candles for the last 3 months (about 2026-08-27 → 2026-11-27) for every coin in the live bot's current top-50 list plus BTCUSDT.
- REQ-105 [always] The backtest shall download from free public sources only (data-api.binance.vision and/or data.binance.vision archive files) and cache them locally under a gitignored folder.
- REQ-106 [unwanted] If a coin has less than 3 months of history, then the backtest shall test the available period and flag the coin as "partial history".
- REQ-107 [always] The backtest shall fetch historical funding rates and open interest from free sources reachable from a US IP (e.g. data.binance.vision futures metrics/fundingRate files, Hyperliquid fundingHistory, OKX history), and record which source covered which coin.
- REQ-108 [unwanted] If derivatives history is unavailable for a coin, then the backtest shall run that coin without derivatives variants and say so in the report.

### Same rules as live (no drift)
- REQ-109 [always] The backtest shall import indicator, scoring, grading, trade-resolution and fee functions from war_room_bot.py instead of re-implementing them.
- REQ-110 [always] The backtest shall only use candles that had closed at the moment of each signal (no look-ahead), including the 1h candle.
- REQ-111 [always] The backtest shall enter each trade at the next 5m candle's open plus 0.05% adverse slippage (interpreted).
- REQ-112 [always] The backtest shall apply the live cooldown (1 h per pair), one-open-trade-per-pair, same-candle SL-first, TP1-bank-half + breakeven rules and the live fees (LONG 0.10% / SHORT 0.05% each way).
- REQ-113 [always] The backtest shall score the order book component as 0 because order-book history does not exist (primary run).
- REQ-114 [always] The backtest shall also run an order-book sensitivity variant with grade thresholds scaled to an 85-point maximum (A ≥ 68, B ≥ 55) and label it clearly as a what-if (interpreted).
- REQ-115 [always] The backtest shall skip the AI review and state in the report that AI filtering cannot be backtested.

### What gets compared
- REQ-116 [always] The backtest shall report A-GRADE and B-GRADE results separately.
- REQ-117 [always] The backtest shall report LONG and SHORT results separately.
- REQ-118 [always] The backtest shall compare max hold times of 1, 2, 4, 8, 12, 24 and 48 hours per grade.
- REQ-119 [always] The backtest shall compare exit styles: (a) live style — half at TP1, breakeven, rest to TP2; (b) full exit at TP1; (c) half at TP1 then trailing stop of 1 ATR on the rest; (d) half at TP1 then hold to max-hold time with breakeven stop.
- REQ-120 [always] The backtest shall compare derivatives variants: off (live default), USE_DERIV_SCORE bonus on, and a BTC-regime filter (no alt LONGs when BTC is down, no alt SHORTs when BTC is up).
- REQ-121 [always] The backtest shall rank coins by net result and flag coins with fewer than 10 trades as "not enough data".
- REQ-144 [always] The backtest shall compare the fee floor MIN_TP1_FEE_MULT at 0, 2, 3 (live default) and 5.
- REQ-145 [always] The backtest shall apply the live stable-like/peg filter and the live coin exclusions; the order-book penalty is inert in the primary run because order-book history does not exist (stated in the report).

### Honesty checks
- REQ-122 [always] The backtest shall pick the best settings using only the first 2 months (training) and report their result on the last month (unseen test) next to it.
- REQ-123 [always] The backtest shall report for every variant: trade count, win rate, average net % per trade, total net %, profit factor, max drawdown, expectancy in R (net % ÷ SL %), and average hold time.
- REQ-124 [always] The backtest shall mark any result with fewer than 30 trades as "too few trades to trust".
- REQ-125 [always] The backtest shall state its known biases in plain words (current top-50 list = survivorship bias, no order book, no AI, slippage assumption).
- REQ-126 [unwanted] If no variant is profitable on the unseen month after fees, then the report shall say so plainly in its first line and not recommend trading real money.

### Outputs
- REQ-127 [always] The backtest shall be one script (backtest.py) runnable with a single command that finishes in under 30 minutes on the pod (cached re-runs under 10 minutes) (interpreted).
- REQ-128 [always] The backtest shall write a plain-English report (backtest_report.md) whose first section is a 5-line summary a non-technical trader understands.
- REQ-129 [always] The backtest shall write machine-readable results (backtest_results.json) and a per-trade CSV.
- REQ-130 [always] The report shall end with concrete recommended settings (coins to keep/drop, grades to trade, sides, max hold per grade, exit style, derivatives filter on/off) each with its train and unseen-month numbers.
- REQ-131 [always] The backtest shall not change live bot defaults, workflows or state.json.
- REQ-132 [always] Downloaded data caches shall be gitignored and the repo shall stay free of website/app code.

## Quality requirements
```
Q-10 Tag: Trust.NoLookAhead
     Scale: number of signals that used a candle not yet closed at signal time
     Meter: automated assertion over every generated signal + 1 hand-checked trade per grade
     Must: 0   Plan: 0
Q-11 Tag: Trust.SameRulesAsLive
     Scale: scoring/trade functions duplicated instead of imported from war_room_bot.py
     Meter: code inspection + a test that a live-scored sample candle set equals the backtest score
     Must: 0   Plan: 0
Q-12 Tag: Money.PnLCorrect
     Scale: net PnL error on 5 hand-computed backtest trades (incl. a SHORT and a TIME EXIT)
     Meter: manual recompute from cached candles
     Must: 0.01%   Plan: 0.00%
Q-13 Tag: Speed.Runtime
     Scale: minutes for a full run on the pod (first run / cached)
     Meter: timed run
     Must: 30 / 10   Plan: 15 / 5
Q-14 Tag: UX.Readable
     Scale: minutes for the non-technical user to understand "does it make money, what should I change" from the summary
     Meter: human
     Must: 5   Plan: 2 (estimate)
Q-15 Tag: Trust.Overfitting
     Scale: gap between training-period and unseen-month expectancy for the recommended settings, shown in the report
     Meter: report inspection
     Must: shown for every recommendation   Plan: shown + warning when unseen < 50% of training
```

## Phase 2b — Quiet mode (live bot), decided after the backtest showed no edge

- REQ-146 [optional] Where QUIET_MODE=1 (new default, set in the workflow), the system shall keep scanning, AI-reviewing and paper-tracking trades exactly as before but send no per-trade alert, TP1 or close messages.
- REQ-147 [event] When the daily summary time is reached (DAILY_SUMMARY_UTC "03:30" = 09:00 IST, configurable), the system shall send one plain "📅 DAILY SUMMARY" message.
- REQ-148 [always] The daily summary shall show, for the last 24 h: paper trades opened (A/B, buy/short counts), paper trades closed with WIN/LOSS and net % after fees, the A/B scoreboard (last 24 h and all-time counted trades), best and worst closed trade, open trades count, AI approved/rejected/unclear counts, and the line "Paper test only — don't trade these with real money."
- REQ-149 [unwanted] If a run is not active at the summary time, then the system shall send the missed daily summary at the start of the next run (once per day, tracked in state).
- REQ-150 [always] The system shall keep the hourly BOT ALIVE and BOT DOWN messages unchanged in quiet mode, with "(quiet mode)" shown in BOT ALIVE.
- REQ-151 [event] When quiet mode first starts, the system shall send a one-time plain notice: "🤫 Quiet mode on — I'll keep testing silently and send one summary every day at 09:00 (India time)."
- REQ-152 [optional] Where QUIET_MODE=0, the system shall behave exactly as before (per-trade messages).
- REQ-153 [always] Quiet mode shall be switchable without code edits (GitHub repository variable or workflow env), documented in README in kid-level steps.

## Phase 2c — Self-chaining runs (GitHub's scheduler proved unreliable)
Evidence 2026-11-27: after the v10 push at 20:25 UTC, ZERO scheduled runs fired for either war_room.yml ('7 * * * *') or watchdog.yml ('37 */2 * * *') in 3 hours; v9's 5-minute cron fired only twice that day. Only manual runs ran (chained via the concurrency queue).

- REQ-154b [event] When a run has completed at least one healthy scan cycle and reaches about minute 50 of its loop, the bot shall dispatch the next run of war_room.yml via the GitHub API (workflow_dispatch with the run's GITHUB_TOKEN), so it waits in the concurrency queue and starts as soon as this run ends.
- REQ-155b [always] The bot shall dispatch at most one next run per run, and only if no other run of the workflow is already queued/pending (interpreted).
- REQ-156b [unwanted] If the bot has had no healthy cycle in the last 10 minutes, then it shall not dispatch (cron and the outside watchdog take over), so a broken version cannot loop.
- REQ-157b [unwanted] If the dispatch API call fails, then the bot shall log it, retry once, and mention "⚠️ next run not queued" in the next BOT ALIVE.
- REQ-158b [always] war_room.yml shall keep the hourly cron as a backup and add only the extra permission actions: write.
- REQ-159b [always] The watchdog shall also be triggered by the main bot's end-of-run step? No — instead the README and final guide shall recommend the free outside Healthchecks.io check (HEALTHCHECK_URL) as the only watchdog independent of GitHub, with kid-level setup steps including its Telegram integration.
- REQ-160b [always] BOT ALIVE shall show "🔗 next run queued ✅" or "❌ not queued (reason)".
