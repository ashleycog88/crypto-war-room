# Phase 4 — Paper-watch the near misses + Step C research (phase-4)

## Goal
Phase 3 found no strategy that passes. User chose BOTH: (A) quietly paper-watch the two near misses live (S1 breakout longs on majors; S5 top-3 momentum on majors) in the daily summary, so reality decides; and (B) Step C research on non-price "edge" data we can test now: copying top Hyperliquid wallets (walk-forward, no hindsight) and the exchange-listing/delisting event patterns. Same honesty rules as Phase 3. Results stay private (gitignored), explained to the user in chat.

## Part A — Paper-watch (live bot, quiet)
- REQ-400 [always] The live bot shall paper-track S1 (4h Donchian breakout, LONG only, majors set) using the exact rules and parameters chosen in Phase 3, imported from research.py (no copy).
- REQ-401 [always] The live bot shall paper-track S5 (top-3 by 30-day return among majors, equal weight, daily rebalance, cash when BTC < 50-day SMA) using the Phase 3 rules, imported from research.py.
- REQ-402 [always] Paper-watch shall send no trade alerts; it shall only add a "🔭 Watching (paper, not tradeable yet)" block to the daily summary: per strategy — positions held now, trades opened/closed in the last 24 h, net result after fees since watch start, and days watched.
- REQ-403 [always] Paper-watch state shall live inside the encrypted state and survive restarts.
- REQ-404 [always] Paper-watch parameters shall default to the Phase 3 choices and be overridable through BOT_SETTINGS.
- REQ-405 [always] Paper-watch shall use only closed candles and next-candle-open fills with the Phase 3 fee models (S1 futures model F; S5 spot model S).
- REQ-406 [unwanted] If paper-watch data fetches fail, then the system shall skip that update, keep positions, and not disturb the main scan cycle (cycle time unchanged within 2 s).
- REQ-407 [event] When paper-watch first starts, the daily summary shall say "🔭 Started watching 2 near-miss strategies today."

## Part B — Step C research (pod only, private outputs)

### C1: Copying top Hyperliquid wallets
- REQ-410 [always] The research shall build a wallet universe from the public Hyperliquid leaderboard that includes winners AND losers (e.g. a stratified random sample across PnL ranks, ≥ 500 wallets), excluding vaults/HLP and high-frequency market makers, to limit survivorship bias.
- REQ-411 [always] The research shall download each sampled wallet's available fill history (userFillsByTime, up to the API's limit) and reconstruct its positions and realized PnL over time.
- REQ-412 [always] At each re-selection date the research shall rank wallets using only fills before that date (trailing window, e.g. 30 days, with a minimum trade count) and select the top K.
- REQ-413 [always] The research shall copy only position openings made by selected wallets AFTER the selection date, on coins that have a Binance USDT market, with execution delays of 1, 5 and 60 minutes, and exits when the wallet closes or reduces the position (plus a safety stop variant).
- REQ-414 [always] Copied trades shall be priced on Binance 1m candles (free archives) with Phase 3 costs (futures fees, slippage, funding).
- REQ-415 [always] The research shall compare against two baselines: copying randomly chosen wallets from the same universe, and random entries matched on coin, side and holding time.
- REQ-416 [always] The pre-registered grid shall be ≤ 12 combinations (K ∈ {5, 10, 20}, ranking metric ∈ {30-day PnL, 30-day ROI}, delay as above reported separately), chosen on the earlier part of the history and judged on the later unseen part.

### C2: Listing / delisting events
- REQ-420 [always] The research shall collect historical announcements (Binance, OKX, KuCoin; Upbit only if reachable) of new spot listings, new perpetual listings, delistings and monitoring tags, with exact publish timestamps, over as much history as the free APIs provide (target ≥ 2 years).
- REQ-421 [always] The research shall measure price paths after each event (from 5 min to 7 days) on the venue where the coin traded at that moment, entering no earlier than 5 minutes after publication.
- REQ-422 [always] The research shall test pre-registered event rules (≤ 12 combinations): e.g. short the post-listing fade (entry 5 min–6 h after announcement, hold 24 h–7 d, ATR stop) where a perp exists, and short/avoid on delisting/monitoring announcements.
- REQ-423 [always] The research shall report event counts per exchange and type, and flag any rule with < 30 events as "too few events to trust".

### Shared honesty rules
- REQ-430 [always] PASS requires, on unseen data after costs: expectancy > 0, profit factor ≥ 1.2, enough trades/events (≥ 50 trades for C1, ≥ 30 events for C2), positive in both halves, beats ≥ 95% of the matched random baseline, positive at 1.5× costs.
- REQ-431 [always] Every combination tested shall be listed in an appendix; no-look-ahead assertion over all trades; 2 hand-checked trades per study.
- REQ-432 [always] The report (private_reports/, gitignored) shall start with a ≤ 6-line plain summary and end with a plain recommendation.
- REQ-433 [always] The research shall not change live bot behaviour (except Part A), workflows or state; full test suite green.

## Quality requirements
```
Q-40 Tag: Trust.NoHindsightSelection
     Scale: copied trades from wallets selected using any fill at/after the trade's selection date
     Meter: automated assertion
     Must: 0   Plan: 0
Q-41 Tag: Trust.Survivorship
     Scale: share of losing/low-rank wallets in the C1 universe
     Meter: universe stats in the report
     Must: ≥ 40%   Plan: ≥ 50%
Q-42 Tag: Isolation.LiveCycle
     Scale: added seconds per live scan cycle from paper-watch
     Meter: 5 live dry cycles before/after
     Must: ≤ 2 s   Plan: ≤ 0.5 s
```
