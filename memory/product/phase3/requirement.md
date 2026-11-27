# Phase 3 — Strategy research on bigger timeframes (phase-3)

## Goal
The Phase 2 backtest proved the v10 5-minute rules have no edge (≈ coin flip before costs, −0.31 R after fees). Find out — honestly — whether any of a small, pre-registered set of well-known strategy families on 1h / 4h / daily candles would have made money after Binance fees, slippage and funding over ~2 years, judged only on data it was not tuned on and against a random-entry baseline. Output a plain-words research report. No live bot changes in this phase (except Phase 2b quiet mode, delivered separately). User trades Binance Futures for everything and prefers Spot for buys → test both fee models.

## Functional requirements

### Data & periods
- REQ-154 [always] The research shall use Binance spot 1h, 4h and 1d candles from free public archives (data.binance.vision, data-api.binance.vision) for 2024-11-27 → 2026-11-27, cached in a gitignored folder.
- REQ-155 [always] The research shall test two coin sets: "majors" (coins with full 2-year history among BTC, ETH, SOL, BNB, XRP, DOGE, ADA, LINK, AVAX, LTC, DOT, TRX, BCH, NEAR, ATOM, UNI, FIL, ETC, APT, ARB — those actually listed) and "current top-50" (live list; partial-history coins flagged).
- REQ-156 [always] The research shall use 2024-11-27 → 2026-03-27 (16 months) for training and 2026-03-27 → 2026-11-27 (8 months) as the unseen test, and also report the two 4-month halves of the unseen test separately.
- REQ-157 [always] The research shall use historical funding rates (data.binance.vision futures fundingRate archives, Hyperliquid fundingHistory or OKX) for funding costs and the funding strategy, and record coverage per coin.

### Strategy families (pre-registered; ≤ 12 parameter combinations each)
- REQ-158 [always] S1 Trend breakout on 4h: Donchian breakout N ∈ {20, 55}, initial stop 2×ATR(4h), exit by 3×ATR chandelier trail or opposite N/2 channel, with and without BTC-trend filter (BTC close above its 4h EMA200 for longs / below for shorts).
- REQ-159 [always] S2 Trend pullback on 1h with 4h trend: 4h EMA50 above EMA200 for longs (below for shorts), 1h RSI(14) dips below {35, 40} (above {65, 60} for shorts) then closes back; stop 1.5×ATR(1h); exit at {2R, 3R} or 2×ATR trail.
- REQ-160 [always] S3 v10 idea on bigger timeframes: the live score_sides/grade logic applied on 1h signals with 4h trend, and on 4h signals with 1d trend, same ATR multiples and TP1-half + breakeven style, A-GRADE and B-GRADE reported separately; scoring must come from war_room_bot.py functions (generalised by timeframe only if live behaviour stays identical).
- REQ-161 [always] S4 Funding extremes on 1h (futures only): when funding is in the top/bottom 5% of its trailing 30-day distribution and price is at a 24h extreme, fade it; stop 2×ATR(1h); max hold {24, 48} h; take profit {1.5R, 2.5R}.
- REQ-162 [always] S5 Momentum rotation on daily candles (long-only, spot-friendly): hold the top K ∈ {3, 5} coins by 30-day return, equal weight, rebalance {weekly, daily}, only while BTC closes above its 50-day SMA (else cash).
- REQ-163 [always] The research shall evaluate both LONG and SHORT sides for S1–S4 and report them separately; S5 is long-only.

### Costs
- REQ-164 [always] The research shall apply fee model F (Binance Futures taker 0.05% each way for both sides plus actual funding paid/received while a position is open).
- REQ-165 [always] The research shall apply fee model S (Spot buys 0.10% each way; shorts on Futures 0.05% each way plus funding).
- REQ-166 [always] The research shall apply 0.05% adverse slippage per fill, and also report a stress case at 1.5× fees and 0.15% slippage.
- REQ-167 [always] The research shall enter on the next candle's open after a signal closes (no look-ahead) and assume stops fill at the stop price, stop-first when stop and target are in the same candle.

### Honesty checks
- REQ-168 [always] The research shall choose each family's best parameter combination using training data only, then report that exact combination on the unseen test.
- REQ-169 [always] The research shall run a random-entry baseline for each chosen combination (≥ 200 runs with the same coins, trade count, side mix and holding-time distribution) and report the strategy's percentile.
- REQ-170 [always] A strategy shall be labelled "PASSED" only if on the unseen 8 months, after model-F costs: expectancy > 0, profit factor ≥ 1.2, ≥ 50 trades (≥ 20 rebalances for S5), positive in both 4-month halves, beats ≥ 95% of random runs, and stays positive in the 1.5× cost stress case; otherwise "REJECTED" with the failing rule(s) named.
- REQ-171 [always] The research shall list every parameter combination tested with its training and unseen results in an appendix (nothing hidden).
- REQ-172 [always] The research shall run an automated no-look-ahead assertion over every trade and hand-check 2 trades per strategy family against raw candles.

### Outputs
- REQ-173 [always] The research shall write research_report.md whose first section is a ≤ 6-line plain-English summary: which strategies passed, the best one, how often it would alert (per week), and its unseen-period result with a "past results don't guarantee future results" line.
- REQ-174 [always] The report shall show per strategy: trades, trades/week, win rate, avg net %, expectancy R, profit factor, max drawdown (R and % at 1% risk per trade), total return at 1% risk per trade, average hold, longest losing streak — for training, unseen, each unseen half, both fee models and the stress case.
- REQ-175 [always] The report shall end with a plain recommendation: which (if any) strategy to paper-alert live in Phase 4 for 4 weeks, with its exact rules in plain words, or "none passed — don't trade".
- REQ-176 [always] The research shall be runnable with one command; first run < 60 min on the pod, cached re-run < 15 min (interpreted).
- REQ-177 [always] The research shall not change live bot behaviour, workflows or state.json; backtest.py must keep working; big data/CSV outputs gitignored.

## Quality requirements
```
Q-16 Tag: Trust.NoLookAhead
     Scale: trades using any candle/funding value not available at signal time
     Meter: automated assertion over all trades + 2 hand checks per family
     Must: 0   Plan: 0
Q-17 Tag: Trust.NoHiddenTuning
     Scale: parameter combinations tested but not listed in the appendix
     Meter: code inspection vs appendix
     Must: 0   Plan: 0
Q-18 Tag: Trust.BeatsRandom
     Scale: percentile of a PASSED strategy vs random-entry runs on unseen data
     Meter: ≥200 random runs
     Must: ≥95th   Plan: ≥99th
Q-19 Tag: Money.CostsRealistic
     Scale: cost components applied (fees per side, slippage, funding) per trade
     Meter: 2 hand-checked trades per family incl. one futures trade held across funding times
     Must: all 3 applied   Plan: all 3 applied
Q-20 Tag: UX.Readable
     Scale: minutes for the non-technical user to understand which strategy (if any) passed and what it would do
     Meter: human
     Must: 5   Plan: 2 (estimate)
```
