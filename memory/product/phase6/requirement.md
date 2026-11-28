# Phase 6 — Blend paper-watch, weekly recap, crash watch (phase-6)

## Goal
User approved the three buildable next steps ("go ahead and be better in everything"): (A) paper-watch the closest near miss from Phase 5, the S12 near-miss blend; (B) a Sunday recap ranking every watched strategy against simply holding Bitcoin and against parking USDT in Binance Flexible Earn; (C) a live "crash watch" Telegram heads-up when a top coin crashes suddenly or many traders get forced out. Still paper only — no trade advice, no real money. The recorded-data test stays scheduled for ≈ 2026-12-26 (not this phase). Overview: [../overview.md](../overview.md).

## Part A — S12 blend paper-watch
- REQ-600 [always] The live bot shall paper-watch S12 (inverse trailing-60-day-volatility blend of S1-long, S5, S6, S8, weekly rebalance) with its functions imported from edge_v2.py (no copy).
- REQ-601 [always] S12 paper-watch shall make the same weights and results as edge_v2.py on the same data.
- REQ-602 [always] The daily summary "🔭 Watching" block shall show S12 as a fifth line: current weights of the 4 parts, 24 h change, net result after fees since watch start, days watched.
- REQ-603 [event] When S12 paper-watch starts, the next daily summary shall say "🔭 Now watching 5 near-miss strategies (added the blend)."
- REQ-604 [unwanted] If a part's data is missing at a rebalance, then S12 shall handle it exactly as edge_v2.py does and log the reason (health-only in Actions logs).
- REQ-605 [optional] Where BOT_SETTINGS sets WATCH_S12=0, the system shall skip S12.

## Part B — Weekly recap
- REQ-610 [event] When the Sunday daily summary time arrives (12:01 Asia/Kolkata, same settings as the daily summary), the system shall send one "📊 WEEKLY RECAP" message.
- REQ-611 [always] The recap shall rank all watched strategies by net result after fees over the last 7 days and since watch start.
- REQ-612 [always] The recap shall show two benchmarks for the same periods: "₿ Just holding Bitcoin" (BTC buy-and-hold, spot fees once) and "🏦 USDT in Binance Flexible Earn" (EARN_APR, default 5.0% a year, set in BOT_SETTINGS).
- REQ-613 [always] The recap shall mark each strategy "beat both", "beat one" or "beat neither" for the period since watch start.
- REQ-614 [always] The recap shall list the main live bot's A and B paper scoreboards for the week (trades, win %, net after fees).
- REQ-615 [always] The recap shall end with "Paper only — nothing here is ready for real money yet." while no strategy has PASS v2 plus live readiness.
- REQ-616 [unwanted] If the recap send fails, then the system shall retry through the existing outbox and send it at most once per week.
- REQ-617 [unwanted] If a run is down at the recap time, then the next run shall send that week's recap late with "(sent late)" in the first line, at most 24 h late.

## Part C — Crash watch (live heads-up)
- REQ-620 [event] When BTC or ETH falls ≥ 4% (changed from 3% after the replay: 3% gave up to 43 messages/month) over the last 60 minutes of closed 5m candles, the system shall send a "🚨 CRASH WATCH" message.
- REQ-621 [event] When any other top-50 coin falls ≥ 12% (changed from 10% after the replay) over the last 60 minutes of closed 5m candles, the system shall send a "🚨 CRASH WATCH" message.
- REQ-622 [event] When ≥ 15 of the top-50 coins each fall ≥ 5% over the last 60 minutes, the system shall send one "🚨 MARKET-WIDE CRASH WATCH" message instead of per-coin messages.
- REQ-623 [always] Each crash message shall show per coin: the % fall in 60 minutes, the current price, the open-interest 1 h change when available, and the label "many traders forced out" when open interest fell ≥ 5% in that hour.
- REQ-624 [always] Each crash message shall end with "Heads-up only — not a trade signal."
- REQ-625 [always] Several coins triggering in the same scan cycle shall be grouped into one message.
- REQ-626 [always] The same coin shall not trigger again within 6 h unless it falls a further full threshold below its last alerted price.
- REQ-627 [always] Crash messages shall be capped at 3 per hour.
- REQ-628 [state] While quiet mode is on, the system shall still send crash messages.
- REQ-629 [optional] Where BOT_SETTINGS sets CRASH_WATCH=0, the system shall send no crash messages.
- REQ-630 [always] Thresholds (CRASH_BIG_PCT=4, CRASH_ALT_PCT=12, CRASH_MARKET_PCT=5, CRASH_MARKET_COUNT=15, CRASH_OI_PCT=5) shall be overridable through BOT_SETTINGS.
- REQ-631 [always] Every crash event shall be saved in the encrypted state (last 300 events, with price 1 h / 4 h / 12 h / 24 h later filled in) for later research.
- REQ-632 [always] The daily summary shall include "🚨 Crash watch: N heads-ups in the last 24 h" (or "none").
- REQ-633 [unwanted] If candles are stale, missing or fail the existing sanity checks, then the system shall not send a crash message for that coin.
- REQ-634 [unwanted] If a crash message send fails, then the system shall not start that coin's cooldown and shall retry next cycle.
- REQ-635 [unwanted] If the first cycle of a run starts, then crash watch shall only use candles that closed within the last 60 minutes (no alerts for old moves after a restart) (interpreted).

## Also save Phase 5
- REQ-640 [always] The Phase 5 research files (edge_data.py, edge_v2.py, tests/test_edge_v2.py) shall be included in this phase's save, still with private_reports/ and backtest_data/ gitignored.

## Quality requirements
```
Q-60 Tag: Trust.FalseCrashAlarms
     Scale: crash messages over a replay of 2026-01→2026-11 history (BTC/ETH + top-50, closed 5m)
     Meter: replay script count, per month
     Must: ≤ 30 per month   Plan: ≤ 12 per month (estimate)
Q-61 Tag: Speed.CrashLatency
     Scale: seconds from the 5m candle close that crosses a threshold to the Telegram send
     Meter: dry-run with mocked candles, 5 trials
     Must: ≤ 90 s   Plan: ≤ 70 s
Q-62 Tag: Isolation.LiveCycle
     Scale: added seconds per live scan cycle from S12 + crash watch
     Meter: 5 live dry cycles before/after
     Must: ≤ 2 s   Plan: ≤ 0.5 s
Q-63 Tag: Trust.RecapNumbers
     Scale: recap figures that differ from a recomputation from state
     Meter: test recomputing every recap number
     Must: 0   Plan: 0
Q-64 Tag: Clarity.Messages
     Scale: crash/recap messages a non-technical reader misreads (jargon, unexplained numbers)
     Meter: human (user reads first live messages)
     Must: 0 confusing terms per message   Plan: 0
Q-65 Tag: Trust.MoneyClaims
     Scale: user-facing lines that suggest buying/selling or call a strategy tradeable
     Meter: copy review
     Must: 0   Plan: 0
```
