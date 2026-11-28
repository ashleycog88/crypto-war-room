# Phase 6 Journeys — Blend paper-watch, weekly recap, crash watch

- id: JRN-170
  steps: Dry-run the live bot over a mocked day after this update.
  expected: Daily summary "🔭 Watching" block shows 5 lines (S1, S5, S6, S8, S12); S12 line shows weights of its 4 parts, 24 h change, net after fees since start, days watched; first summary says "🔭 Now watching 5 near-miss strategies (added the blend)." once only.

- id: JRN-171
  steps: Compare S12 paper-watch with edge_v2.py on the same data; restart mid-week.
  expected: Identical weights and results; functions imported (no copy); S12 state survives restart in the encrypted state; WATCH_S12=0 removes the line.

- id: JRN-172
  steps: Let a mocked Sunday reach 12:01 India time.
  expected: One "📊 WEEKLY RECAP" message: strategies ranked by 7-day and since-start net after fees; "₿ Just holding Bitcoin" and "🏦 USDT in Binance Flexible Earn" (5.0% a year default) for the same periods; each strategy marked "beat both" / "beat one" / "beat neither"; A/B scoreboards for the week; last line "Paper only — nothing here is ready for real money yet."

- id: JRN-173
  steps: Recompute every recap number from the saved state.
  expected: 0 differences (Q-63); EARN_APR in BOT_SETTINGS changes the Earn benchmark.

- id: JRN-174
  steps: Make the bot down at Sunday 12:01, start it 5 h later; then make the recap send fail once.
  expected: Recap arrives with "(sent late)" in the first line; after a failed send it retries via the outbox and arrives exactly once that week; no recap if more than 24 h late.

- id: JRN-175
  steps: Feed closed 5m candles where BTC falls 3.4% in 60 minutes with open interest −6%.
  expected: One "🚨 CRASH WATCH" message within 90 s of the candle close: BTC, −3.4% in 60 min, current price, OI −6% "many traders forced out", last line "Heads-up only — not a trade signal."

- id: JRN-176
  steps: Feed an alt (not BTC/ETH) falling 9% in 60 minutes, then another falling 11%.
  expected: No message for the 9% fall; one CRASH WATCH message for the 11% fall.

- id: JRN-177
  steps: Feed 16 of the top-50 coins each falling ≥ 5% in 60 minutes in the same cycle.
  expected: One "🚨 MARKET-WIDE CRASH WATCH" message (no per-coin flood), listing the count and the biggest fallers.

- id: JRN-178
  steps: After a BTC crash alert, feed BTC falling a bit more 1 h later, then a further full 3% below the alerted price.
  expected: No repeat for the small extra fall within 6 h; a new alert only after the further full-threshold fall; never more than 3 crash messages in any hour; several coins in one cycle grouped into one message.

- id: JRN-179
  steps: Turn quiet mode on; then set CRASH_WATCH=0; then set CRASH_ALT_PCT=8 in BOT_SETTINGS.
  expected: Quiet mode still sends crash messages; CRASH_WATCH=0 sends none; CRASH_ALT_PCT=8 makes the 9% alt fall trigger.

- id: JRN-180
  steps: Feed stale/broken candles for a coin; make a crash send fail; restart the bot right after an old 2-hour-ago crash.
  expected: No message for stale/broken data; failed send → no cooldown, retried next cycle; after restart no alert for the old move.

- id: JRN-181
  steps: Check the encrypted state and the daily summary after mocked crash events.
  expected: Events saved (cap 300) with price 1 h / 4 h / 12 h / 24 h later filled in as time passes; daily summary shows "🚨 Crash watch: N heads-ups in the last 24 h" (or "none").

- id: JRN-182
  steps: Replay 2026-01→2026-11 closed 5m history through crash watch.
  expected: Message count per month reported; ≤ 30 per month (Q-60).

- id: JRN-183
  steps: Run the full suite 3 times; measure 5 live dry cycles before/after; check the save list.
  expected: All tests green 3/3; main cycle +≤ 2 s; edge_data.py, edge_v2.py, tests/test_edge_v2.py included in the save; private_reports/ and backtest_data/ still gitignored; production state files untouched.

## Threshold change after the 2026 replay (defaults 4% / 12%)
- id: JRN-184
  superseded: [JRN-175]
  steps: Feed closed 5m candles where BTC falls 3.4% in 60 minutes, then 4.3% with open interest −6%.
  expected: No message at −3.4%; one "🚨 CRASH WATCH" within 90 s at −4.3% with current price, OI −6% "many traders forced out", last line "Heads-up only — not a trade signal."

- id: JRN-185
  superseded: [JRN-176]
  steps: Feed an alt (not BTC/ETH) falling 11% in 60 minutes, then another falling 12.5%.
  expected: No message for 11%; one CRASH WATCH message for 12.5%.

- id: JRN-186
  superseded: [JRN-178, JRN-179]
  steps: After a BTC alert, feed a small extra fall within 6 h, then a further full 4% below the alerted price; set CRASH_WATCH=0; set CRASH_ALT_PCT=10 and feed an alt −11%.
  expected: No repeat for the small fall; new alert after the further full 4%; ≤ 3 crash messages per hour; quiet mode still sends; CRASH_WATCH=0 sends none; CRASH_ALT_PCT=10 makes the −11% alt trigger.

- id: JRN-187
  superseded: [JRN-182]
  steps: Replay 2026-01→2026-11 with the new defaults.
  expected: Every month ≤ 30 messages (Q-60).
