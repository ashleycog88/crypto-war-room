# Phase 3 Journeys — Strategy research lab

All checks run on the pod with bash/python. Phase 1/2 journeys stay valid (live bot unchanged except Phase 2b quiet mode).

- id: JRN-81
  steps: Run the research command once from a clean cache.
  expected: Finishes < 60 min; writes research_report.md and a compact results JSON; data cache + big CSVs gitignored; git shows no change to live defaults, workflows or state.json; `python backtest.py` still runs.

- id: JRN-82
  steps: Re-run with cache.
  expected: < 15 min; identical headline numbers.

- id: JRN-83
  steps: Run the no-look-ahead assertion; hand-check 2 trades per family (S1–S5) from raw candles, one futures trade spanning funding timestamps.
  expected: 0 violations; entries at next-candle open + 0.05% slippage; fees per model F and S; funding paid/received matches archive values.

- id: JRN-84
  steps: Inspect S3 code path and score one fixed 1h window via the live functions.
  expected: S3 uses war_room_bot.py scoring/grade functions (no copy); identical score; live 5m behaviour unchanged (Phase 1 suite green).

- id: JRN-85
  steps: Read the appendix.
  expected: Every tested combination (≤12 per family) with training and unseen results under both fee models; the chosen one per family marked and chosen on training only.

- id: JRN-86
  steps: Read the random-baseline section.
  expected: For each chosen combination: ≥200 random-entry runs matched on coins, trade count, side mix and hold time; strategy percentile shown.

- id: JRN-87
  steps: Read the verdict table.
  expected: Each family labelled PASSED or REJECTED; REJECTED names the failing rule(s) (expectancy, PF ≥1.2, ≥50 trades / ≥20 rebalances, both halves positive, ≥95th percentile vs random, stress case positive).

- id: JRN-88
  steps: Read the first section of research_report.md.
  expected: ≤6 plain lines: which passed, best one, alerts per week, unseen result after fees, and a "past results don't guarantee future results" line; if none passed, the first line says so plainly.

- id: JRN-89
  steps: Read per-strategy tables.
  expected: Training, unseen, unseen halves, fee model F and S, stress case — with trades, trades/week, win %, avg net %, expectancy R, PF, max DD (R and % at 1% risk), total return at 1% risk, avg hold, longest losing streak; LONG and SHORT separate for S1–S4.

- id: JRN-90
  steps: Read the coin-set comparison.
  expected: Results for "majors (full history)" and "current top-50" shown separately, partial-history coins flagged, survivorship bias explained.

- id: JRN-91
  steps: Read the recommendation.
  expected: Either one strategy to paper-alert live for 4 weeks with its exact rules in plain words, or "none passed — don't trade".
