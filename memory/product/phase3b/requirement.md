# Phase 3b — Edge-data recorder (phase-3b)

## Goal
News, smart-money wallets, whale orders, liquidations and listing announcements have no free history, so they can only be tested on data we record ourselves. Start recording them NOW (free, on GitHub Actions; moves to the private server in Phase E) into a separate `data` branch, without touching the live bot or its state.json. These recordings feed Phase C (smart-money / event research) and Phase D (combined score). Hyperliquid wallets only — no Solana/Ethereum memecoin wallets (user decision).

## Functional requirements

### Runner & storage
- REQ-200 [always] A separate workflow recorder.yml shall run the recorder in ~58-minute loops, self-chaining exactly like war_room.yml (dispatch next run at ~minute 50 when healthy, at most one queued), with an hourly cron backup and its own concurrency group.
- REQ-201 [always] The recorder shall write data only to an orphan branch named `data`, never to main.
- REQ-202 [always] The recorder shall write one new gzip JSON-lines file per source per UTC hour (path data/YYYY/MM/DD/HH_<source>.jsonl.gz) and never rewrite a file in an earlier commit, so git history grows only by new files.
- REQ-203 [always] The recorder shall commit and push new files at most every 15 minutes and at the end of every run (if: always()), with fetch/rebase retries; the bot's main branch and state.json are never touched.
- REQ-204 [always] The recorder shall write data/status.json on the data branch with, per source, the last successful record time and records in the last 24 h.
- REQ-205 [unwanted] If one source fails, then the recorder shall log it, mark that source failed in status.json, and keep recording the others.
- REQ-206 [always] Every record shall carry the UTC timestamp it was observed, the source name and the source's own event time when one exists.

### Sources (free, no keys; each with a fallback where possible)
- REQ-207 [always] Hyperliquid market state every 5 min: per coin funding, open interest, mark price, premium (metaAndAssetCtxs).
- REQ-208 [always] Hyperliquid smart-money wallets: download the public leaderboard once per run; select the top 100 wallets by a documented rule (e.g. 30-day PnL and all-time ROI, account value ≥ $100k, excluding vaults/HLP and obvious market makers by trade count) and store the selection with its ranking inputs.
- REQ-209 [always] Every 15 min, record each selected wallet's open positions (coin, side, size, entry price, leverage, unrealized PnL, account value).
- REQ-210 [always] Once per day, record each selected wallet's fills since the last recorded fill (userFillsByTime), so a complete forward trade history builds up beyond the API's 10,000-fill limit.
- REQ-211 [always] News every 5 min from free RSS feeds (at least CoinDesk, Cointelegraph, Decrypt, The Block, Bitcoin Magazine, CryptoSlate), de-duplicated by URL, with title, link, feed and published time.
- REQ-212 [always] Every 15 min, rate new headlines in batches with Gemini (≤10 headlines per call) into: coins mentioned, sentiment −2..+2, category (listing, delisting, hack/exploit, regulation, macro, ETF/institutional, partnership, token unlock, other) and urgency; store the model name used.
- REQ-213 [always] The recorder shall use its own AI model bucket (default gemini-3.1-flash-lite, fallback gemini-3.5-flash-lite) and a daily cap (default 300 calls) so it never starves the live bot's AI reviews.
- REQ-214 [unwanted] If AI rating fails or the cap is reached, then the recorder shall still store the raw headlines marked "unrated" and rate them later when possible.
- REQ-215 [always] Listing/delisting announcements every 5 min from Upbit (public announcements API), OKX (public announcements API) and Binance (public CMS endpoint, best-effort; may be blocked from US runners), with exchange, title, coin(s), type and published time.
- REQ-216 [always] Binance whale trades: stream public trades for the live top-50 coins from Binance's public market-data stream (e.g. data-stream.binance.vision) and record per coin per 5 min the count and USD volume of buy-side and sell-side trades ≥ a per-coin threshold (default $100k; $25k for coins outside the top 10 by volume).
- REQ-217 [always] Liquidations from a free reachable public source (e.g. OKX public liquidation orders; others if reachable), aggregated per coin per 5 min (long/short liquidated USD, count).
- REQ-218 [always] Long/short ratios for BTC, ETH and the top coins every 15 min from a free reachable source (e.g. OKX rubik stats).
- REQ-219 [always] Market context once per hour: Fear & Greed index (alternative.me), BTC dominance and total market cap (CoinGecko public /global), total stablecoin supply (DefiLlama stablecoins).

### Reporting
- REQ-220 [always] The live bot's daily summary shall show one recorder line, e.g. "📼 Recorder: ✅ 24/24 hours recorded (news 412 · wallets 96 · whales ✅ · listings 3)" or ❌ with the failing source, read from the public status.json.
- REQ-221 [unwanted] If status.json shows any source with no record for 3 hours, then the live bot's BOT ALIVE shall show "⚠️ Recorder: <source> stalled".

### Limits & safety
- REQ-222 [always] The recorder shall respect each source's rate limits (Hyperliquid weight budget 1,200/min per IP; Binance weights; CoinGecko/DefiLlama polite rates) and use timeouts on every call.
- REQ-223 [always] The recorder shall never print secrets and shall only need permissions contents: write and actions: write.
- REQ-224 [always] The data branch shall stay under ~5 MB per day compressed (interpreted); the recorder shall log daily size.
- REQ-225 [optional] Where DRY_RUN=1, the recorder shall write to a local folder and not push.

## Quality requirements
```
Q-21 Tag: Data.Completeness
     Scale: % of UTC hours in a 24 h window with a file for each source
     Meter: status.json + file listing
     Must: 90%   Plan: 98%
Q-22 Tag: Data.NoLookAhead
     Scale: records whose observed timestamp is earlier than the source event time
     Meter: automated check over a day of files
     Must: 0   Plan: 0
Q-23 Tag: Cost.Free
     Scale: $ per month
     Meter: services used
     Must: 0   Plan: 0
Q-24 Tag: Isolation.LiveBotSafe
     Scale: live-bot cycles slowed or AI reviews blocked by the recorder
     Meter: compare live cycle times and AI 429s before/after a day of recording
     Must: 0 AI reviews blocked   Plan: 0
```
