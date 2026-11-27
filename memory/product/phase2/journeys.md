# Phase 2 Journeys — 3-month backtest

All checks run on the pod with bash/python. Phase 1 journeys JRN-1..JRN-53 stay valid (live bot unchanged).

- id: JRN-54
  steps: Run `python backtest.py` once from a clean cache.
  expected: Finishes in under 30 min; writes backtest_report.md, backtest_results.json and a trades CSV; data cache folder is gitignored; `git status` shows no change to war_room_bot.py defaults, workflows or state.json.

- id: JRN-55
  steps: Run it again with the cache present.
  expected: Finishes in under 10 min with identical headline numbers.

- id: JRN-56
  steps: Inspect backtest.py imports and run a test that scores one fixed historical candle window with the live functions and with the backtest path.
  expected: Scoring/grade/trade-resolution/fee functions are imported from war_room_bot.py (not copied); both scores are identical.

- id: JRN-57
  steps: Run the no-look-ahead assertion over all generated signals; hand-check one A-GRADE and one B-GRADE signal against raw candles.
  expected: 0 signals use a 5m or 1h candle that closed after the signal time; hand-checked indicators match.

- id: JRN-58
  steps: Recompute 5 trades by hand from cached candles (1 SL, 1 TP1→TP2, 1 TP1→BE, 1 SHORT, 1 TIME EXIT).
  expected: Entry = next candle open ± 0.05% slippage; net PnL matches the CSV to 0.01% with LONG 0.20% / SHORT 0.10% round-trip fees.

- id: JRN-59
  steps: Read the report's first section.
  expected: A ≤5-line plain-words summary: does it make money after fees on the unseen month (yes/no), best grade, best side, best hold time, and a one-line recommendation.

- id: JRN-60
  steps: Read the comparison tables.
  expected: A vs B; LONG vs SHORT; hold times 1/2/4/8/12/24/48 h per grade; exit styles (a) live, (b) full TP1, (c) half + 1-ATR trail, (d) half + hold to time; derivatives off / bonus on / BTC-regime filter — each with trades, win rate, avg net %, total net %, profit factor, max drawdown, expectancy R, avg hold.

- id: JRN-61
  steps: Read the walk-forward section.
  expected: Best settings chosen on the first 2 months are shown next to their result on the last month; a warning appears when unseen-month expectancy is below 50% of training.

- id: JRN-62
  steps: Read the per-coin ranking.
  expected: All tested coins listed by net result; coins with <10 trades flagged "not enough data"; partial-history coins flagged.

- id: JRN-63
  steps: Look for small-sample results.
  expected: Every variant with <30 trades is marked "too few trades to trust".

- id: JRN-64
  steps: Read the biases section.
  expected: Plainly states survivorship bias (current top-50 list), no order-book history (OB = 0 in primary run), no AI review, slippage assumption, and which derivatives sources covered which coins.

- id: JRN-65
  steps: Read the order-book what-if section.
  expected: Results with thresholds scaled to 85 max (A ≥ 68, B ≥ 55) are shown separately and labelled as a what-if, not the main result. (interpreted)

- id: JRN-66
  steps: Read the recommendations section.
  expected: Concrete settings (coins keep/drop, grades, sides, max hold per grade, exit style, derivatives filter on/off), each with training and unseen-month numbers. If nothing is profitable on the unseen month, the first line of the report says so and no real-money trading is recommended.

- id: JRN-67
  steps: Make one coin's derivatives history unavailable (or find one naturally).
  expected: That coin still runs in the base variants; the report says derivatives variants excluded it.
