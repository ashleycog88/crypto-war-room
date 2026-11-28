# Phase 4 Journeys — Paper-watch + Step C research

- id: JRN-140
  steps: Dry run the live bot over a mocked day with S1/S5 signals (one S1 breakout entry and exit; one S5 rebalance).
  expected: No alert messages; daily summary has the "🔭 Watching (paper, not tradeable yet)" block with S1 and S5 positions, 24 h trades, net after fees since start, days watched; first summary says "🔭 Started watching 2 near-miss strategies today."

- id: JRN-141
  steps: Compare paper-watch decisions with research.py on the same candles.
  expected: Identical entries/exits/holdings (functions imported, not copied); closed candles only; next-open fills; S1 fee model F, S5 model S.

- id: JRN-142
  steps: Restart the bot mid-position (encrypted state).
  expected: Paper-watch positions and results survive; no duplicate entries.

- id: JRN-143
  steps: Make paper-watch data fetches fail; measure 5 live dry cycles before/after enabling paper-watch.
  expected: Update skipped, positions kept, main cycle unaffected (≤ 2 s added).

- id: JRN-144
  steps: Read the C1 report section.
  expected: Universe ≥ 500 wallets incl. ≥ 40% losing/low-rank, exclusions stated; walk-forward selection with 0 hindsight violations; results per K, metric and delay (1/5/60 min); baselines (random wallets, random entries); PASS/REJECTED with failing rules.

- id: JRN-145
  steps: Read the C2 report section.
  expected: Event counts per exchange/type and history span; post-event price paths (5 min–7 d); pre-registered rules with PASS/REJECTED; rules with < 30 events flagged.

- id: JRN-146
  steps: Check honesty artefacts.
  expected: Appendix lists every combination; no-look-ahead assertion passes; 2 hand-checked trades per study; outputs only under private_reports/ (gitignored); live behaviour unchanged apart from Part A; full suite green.

- id: JRN-147
  steps: Read the report's first section.
  expected: ≤ 6 plain lines: which (if any) passed, best result, how often it would trade, recommendation, "past results don't guarantee future results".
