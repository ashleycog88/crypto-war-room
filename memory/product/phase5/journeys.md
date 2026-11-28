# Phase 5 Journeys — Edge Hunt v2

- id: JRN-150
  steps: Read the report's data section.
  expected: History span per data type (spot 1h/4h/1d, perp 1h/4h/1d, funding, majors 5m) starting 2020-01 or the coin's first month, ending 2026-11; count of coins; count of later-delisted coins included; list of missing archive months (no invented prices).

- id: JRN-151
  steps: Audit the point-in-time coin sets.
  expected: Monthly majors (20) and top50 (50) sets ranked on trailing 30-day volume; stablecoins, leveraged tokens, tokenized stocks/gold excluded; assertion "no coin selected before it traded" passes; every later-delisted coin with data appears in the months it qualified (Q-51 = 0 missing).

- id: JRN-152
  steps: Read the walk-forward section.
  expected: ≈ 9 unseen 6-month windows (18-month look-back each, rolled every 6 months, ending 2026-11); for each family: chosen setting per window (from look-back only), per-window result after costs, joined-unseen expectancy, PF, trade/rebalance count, % windows positive, latest-12-month result, random percentile (≥ 500 matched runs), 1.5× stress result, max drawdown; verdict PASS v2 / REJECTED with the failing rules named.

- id: JRN-153
  steps: Read the near-miss re-judgement.
  expected: S1 long breakout, S5, S6, S8 each judged under PASS v2 on point-in-time sets with their original grids, plus the old Phase 3 rule verdict side by side.

- id: JRN-154
  steps: Read the S9 AdaptiveTrend section.
  expected: Source arXiv 2602.11708 cited; paper parameters used unchanged (6h, ATR trailing stop, monthly rolling-Sharpe selection γL 1.3 / γS 1.7, 70/30 long/short, model F); separate results for 2022-01→2024-12 (paper period) and 2025-01→2026-11 (post-paper); differences from the paper listed; walk-forward verdict.

- id: JRN-155
  steps: Read the S10 volatility-spike bounce section.
  expected: Source kquant.app cited; spike defined on closed 5m Garman-Klass vol with the adaptive threshold described; long at next 5m open, 12 h time exit, one open trade per coin; results with model F and model S; verdict; note on how much profit came from the 10 best days.

- id: JRN-156
  steps: Read the S11 selective carry section.
  expected: Entry/exit on mean of last 3 funding settlements; 4 taker legs + real spot-perp gap charged; liquidation count at 1× and 2× short-leg leverage; net APR vs the stated USDT flexible-earn benchmark (rate + source); % windows above benchmark; random-entry baseline percentile; verdict.

- id: JRN-157
  steps: Read the S12 near-miss blend section.
  expected: S1-long, S5, S6, S8 streams combined with inverse 60-day-vol weights, weekly rebalance, no tuning; correlation table between the 4 streams; PASS v2 verdict.

- id: JRN-158
  steps: Check honesty artefacts.
  expected: Appendix lists every combination (total ≤ 100); no-look-ahead assertion 0 violations; 2 hand-checked trades per new family; parked ideas listed with reasons (cascade-fade scalper, 21–23 UTC seasonality, pairs trading, ETF-flow signal); outputs only under private_reports/edge_v2/ (gitignored); full test suite green.

- id: JRN-159
  steps: Read the report's first lines.
  expected: ≤ 6 plain lines: which (if any) passed, best result, how often it trades, plain recommendation, "past results don't guarantee future results".

- id: JRN-160
  steps: (Only if something passed) dry-run the live bot over a mocked day with the new candidate.
  expected: No trade alerts; daily summary shows "🧪 New candidate: <name> — passed the 6-year test, now paper-watching. NOT real money yet." once, then a readiness line (days, trades, live result after fees, backtest 10th–90th range); decisions identical to the research module on the same candles; state survives restart; main cycle +≤ 2 s.

- id: JRN-161
  steps: (Only if something passed) simulate 30+ watched days with ≥ 10 trades and a live result above the 10th percentile; then simulate a result below the 10th percentile.
  expected: First case shows "✅ Ready for a small real-money trial: <name>"; second shows "⚠️ <name> is doing worse than its test — not ready".

- id: JRN-162
  steps: (Only if nothing passed) diff the live bot, workflows and tests against main.
  expected: Live bot behaviour, workflows and state unchanged; only research files, tests and gitignore entries added.
