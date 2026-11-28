# Phase 3b Journeys — Edge-data recorder

- id: JRN-100
  steps: DRY_RUN=1 recorder run for 10 minutes on the pod.
  expected: Local hourly gzip JSONL files for hl_market, hl_positions (or leaderboard selection), news, news_ai (or unrated), listings, whales, liquidations, longshort, context; every record has observed UTC time and source; status.json written; no secrets in logs.

- id: JRN-101
  steps: Inspect the leaderboard selection file.
  expected: ≤100 wallets with ranking inputs (30-day PnL, all-time ROI, account value), vaults/HLP and high-frequency market makers excluded, rule documented.

- id: JRN-102
  steps: Feed 25 new mock headlines with a mocked AI.
  expected: ≤10 headlines per AI call; each rated with coins, sentiment −2..+2, category, urgency, model; with AI failing or cap reached, headlines stored "unrated" and rated later.

- id: JRN-103
  steps: Mock a Binance trade stream with trades of $20k, $60k and $150k on BTC and $30k on a non-top-10 coin.
  expected: BTC whale bucket counts only the $150k trade (≥$100k); the non-top-10 coin counts the $30k trade (≥$25k); buy/sell side split correct.

- id: JRN-104
  steps: Make the Upbit source fail and Binance CMS return 403.
  expected: Recorder keeps going; status.json marks those sources failed with reason; other sources still recorded.

- id: JRN-105
  steps: In a local git sandbox, run two checkpoint pushes and one end-of-run push to an orphan `data` branch with a concurrent remote commit.
  expected: Pushes succeed; only new hourly files added (no rewritten blobs); main branch untouched.

- id: JRN-106
  steps: Read recorder.yml.
  expected: Self-chaining at ~minute 50 (healthy only, ≤1 queued), hourly cron backup, own concurrency group, permissions contents: write + actions: write only, pinned action SHAs, if: always() push step, timeouts.

- id: JRN-107
  steps: Run the live bot's daily summary with a mock status.json (all OK), then with news stalled 4 h.
  expected: Daily summary shows "📼 Recorder: ✅ 24/24 hours recorded …"; with the stall, BOT ALIVE shows "⚠️ Recorder: news stalled".

- id: JRN-108
  steps: Run the recorder and live bot together for 10 min (dry) with real AI disabled/mocked.
  expected: Live bot cycle times unchanged (<45 s); recorder AI calls go to its own model bucket and respect the daily cap.

- id: JRN-109
  steps: Check the no-look-ahead rule on recorded files.
  expected: 0 records with observed time earlier than the source event time.
