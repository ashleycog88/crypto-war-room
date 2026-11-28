# Crypto War Room bot v10

A free Python bot that runs on **GitHub Actions** and sends trade alerts to **Telegram**. There is no website or app.
Every hour GitHub starts the bot. It runs for about 58 minutes and scans the top 50 Binance USDT coins every 60 seconds.

## Files
| file | what it is |
|---|---|
| `war_room_bot.py` | the whole bot: scan loop plus `--watchdog`, `--diagnose`, `--commit-state` and `--test-messages` modes |
| `requirements.txt` | exact Python library versions |
| `.github/workflows/war_room.yml` | hourly run at minute :07 (and the "Run workflow" button) |
| `.github/workflows/watchdog.yml` | every 2 hours at :37. Sends **BOT DOWN** if no scan has finished for 2 hours |
| `.github/workflows/diagnostics.yml` | manual check of which data sources work from GitHub |
| `state.json` | the bot's memory (open trades, records, cooldowns). The bot updates it. Don't edit it by hand. With `DATA_KEY` it becomes the encrypted `state.json.enc` |
| `datalock.py` + `datalock.salt` | encryption helper (AES-256-GCM) and its public salt; also a `decrypt` command for research |
| `recorder.py` + `.github/workflows/recorder.yml` | the edge-data recorder (hourly at :23), see below |
| `research.py` | Phase 3 strategy research (`python research.py`; results only in the gitignored `private_reports/`) |
| `crash_watch.py` / `recap.py` | the 🚨 crash heads-up rules and the 📊 Sunday recap (plain functions the bot calls) |
| `edge_data.py` / `edge_v2.py` / `crash_replay.py` | Phase 5/6 research: 6-year Binance archives, walk-forward judge, crash-watch replay (results only in `private_reports/`) |
| `paper_watch.py` | quietly paper-watches the 2 Phase 3 near misses using research.py's own rules (daily summary only) |
| `tests/` | offline tests (`pip install pytest==9.2.0 && pytest tests -q`) |

## 1. GitHub secrets (Settings → Secrets and variables → Actions → New repository secret)
| name | value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | the token from @BotFather (looks like `123456789:AA...`) |
| `TELEGRAM_CHAT_ID` | your chat id (a number; group ids start with `-100`) |
| `LLM_API_KEY` | your free Google Gemini key from https://aistudio.google.com/apikey. **Leave it empty if you don't have one.** The bot still works and alerts say "AI unavailable" |
| `LLM_BASE_URL` | `https://generativelanguage.googleapis.com/v1beta/openai/` |
| `LLM_MODEL` | `gemini-3.5-flash-lite` |
| `LLM_FALLBACK_MODELS` | optional. Models to try in order if the main one fails, separated by commas. Default `gemini-3.1-flash-lite,gemini-3.6-flash`. The AI gets at most ~30 s per alert in total |
| `HEALTHCHECK_URL` | strongly recommended: a free https://healthchecks.io ping URL. It is the only watchdog that works even if GitHub stops starting the bot (see "Outside watchdog" below) |
| `DATA_KEY` | strongly recommended: a long secret passphrase (e.g. 6+ random words). It locks the bot's memory and all recorded data so nobody can read them in this public repo. **Write it down somewhere safe — without it the encrypted data can never be read again.** Until it is set, BOT ALIVE shows "🔓 Data not encrypted — add DATA_KEY" |
| `BOT_SETTINGS` | optional hidden tunables as JSON, e.g. `{"MIN_TP1_FEE_MULT": 5, "OB_PENALTY": 10}`. Overrides the defaults without showing them in public files. Unknown keys or bad values are ignored, and BOT ALIVE lists them |

Never link a billing account to the Gemini key. The free tier is enough, and the bot backs off when Google says "too many requests".

## 2. Turn it on
1. Actions tab → enable workflows if GitHub asks.
2. **War Room diagnostics** → *Run workflow*. Read the PASS/FAIL lines: one per spot exchange, one per derivatives source, then Telegram and Gemini. Gemini shows "skipped: no key" if you have no key. Tick *send_test_message* to also get one TEST message.
3. **Crypto War Room v10** → *Run workflow* (after that it runs every hour by itself).
4. GitHub switches off scheduled workflows in public repos after 60 days without commits. The bot commits `state.json` every 15 minutes, so this shouldn't happen. If it ever does, open Actions and press *Enable workflow*.

## Self-chaining (why the bot keeps running)
GitHub's hourly timer (cron) is not reliable. Sometimes it skips hours. So at about minute 50 of each run, the bot asks GitHub to start the next run itself. The next run waits in the queue and starts as soon as the current one ends. It only does this if it scanned fine in the last 10 minutes (so a broken version can't keep restarting itself), and only if no run is already waiting. The hourly timer stays on as a backup. BOT ALIVE shows `🔗 Next run queued ✅` or `❌ not queued (reason)`. You don't need to set anything up: it uses GitHub's built-in token (`actions: write` permission in `war_room.yml`). Change the minute with `CHAIN_AT_MINUTE` (default 50).

## Outside watchdog (Healthchecks.io, free, recommended)
The BOT DOWN watchdog runs on GitHub too, so if GitHub stops starting workflows it can't warn you either. Healthchecks.io runs outside GitHub. The bot "pings" it after every finished scan. If the pings stop, it sends you a Telegram message.
1. Go to https://healthchecks.io and click **Sign Up** (free). Confirm your e-mail.
2. Your project shows a check called "My First Check". Click its name, or **Add Check**.
3. Click **Change Schedule** (or the period setting). Set **Period = 1 hour** and **Grace Time = 30 minutes**. Save.
4. Copy the **ping URL** (it looks like `https://hc-ping.com/1234abcd-...`).
5. On GitHub: **Settings → Secrets and variables → Actions → Secrets tab → New repository secret**. Name `HEALTHCHECK_URL`, paste the URL, **Add secret**.
6. Back on Healthchecks.io: click **Integrations** (top menu) → **Telegram** → **Add Integration**. Follow the steps: it opens the Healthchecks bot in Telegram, you press **Start**, and it connects.
7. Done. If the bot stops scanning for more than 1½ hours, Telegram tells you "check is DOWN". When it starts again, it tells you "UP".

BOT ALIVE and the daily summary show a line about it: "🩺 Outside watchdog (Healthchecks): ✅ connected" (last ping worked), "❌ ping failing (HTTP …)" (URL set but pings fail; check the URL) or "➖ not set up" (no `HEALTHCHECK_URL` secret). The URL itself is never shown.

## Quiet mode (on by default)
The backtest showed these rules lose money, so by default the bot is **quiet**. It keeps scanning, asking the AI and paper-tracking trades, but it does **not** send a message for every trade. Instead you get:
- one **📅 DAILY SUMMARY** every day at 12:01 PM India time. If the bot wasn't running at that time, the summary comes at the start of the next run.
- the hourly **💓 BOT ALIVE (quiet mode)** and, if something breaks, **⚠️ BOT DOWN**.

**To turn quiet mode OFF (get a message for every trade again):**
1. Open your repository on GitHub.
2. Click **Settings** (top bar).
3. On the left, click **Secrets and variables**, then **Actions**.
4. Click the **Variables** tab (next to "Secrets").
5. Click **New repository variable**.
6. Name: `QUIET_MODE` · Value: `0` → **Add variable**.

**To turn it back ON:** same place, click the pencil next to `QUIET_MODE`, change the value to `1`, then **Update variable** (or delete the variable, since on is the default).
The change works from the next hourly run. No code edits needed.
To move the summary time, add a variable `DAILY_SUMMARY_TIME` in 24-hour format, e.g. `07:00` = 7:00 AM or `18:30` = 6:30 PM. The default is `12:01` (12:01 PM). To use another time zone, add `DAILY_SUMMARY_TZ`, e.g. `America/New_York` (US Eastern) or `Europe/London`. The default is `Asia/Kolkata` (India time). Summer/winter clock changes are handled automatically. After a change, the bot sends one "🕐 Daily summary time updated: every day at …" message, and the next summary comes at the new time.

## Paper-watch of the 2 near misses (on by default, no alerts)
Phase 3 and Part C found four strategies that came close to passing. The bot follows them **on paper** and only reports in the daily summary, in a "🔭 Watching (paper, not tradeable yet)" block. It shows what each one holds now, its trades in the last 24 hours, its result after fees since the watch started, and the number of days watched.
- **4h breakout (buy only, majors):** buy when a 4-hour candle closes above the highest high of the previous 20 candles, and only while BTC is above its 200-candle average. The stop is 2 ATR below entry. Exit at the next open after a close below the lowest low of the previous 10 candles. Futures fees and funding are counted.
- **Top-3 momentum (majors, spot):** after every daily close, if BTC is above its 50-day average, hold the 3 majors with the biggest 30-day gain in equal parts; otherwise hold cash. Spot fees are counted.
- Both use research.py's own code, closed candles only, and fills at the next candle's open. The first summary says "🔭 Started watching 2 near-miss strategies today."
- **Funding factor (top 50, futures):** every week (Tuesday close) rank the top-50 coins by their average funding over the last 14 days. Buy the lowest fifth and short the highest fifth, in equal parts. Futures fees and funding are counted.
- **Vol-managed momentum (top 50, spot):** every week, the top-3 momentum rule (BTC above its 50-day average), sized down when the last 84 days were wild (target 40% yearly volatility, never more than 100% invested). Spot fees are counted.
- Live funding comes from Hyperliquid (hourly rates ×8 for the 8-hour comparison), because Binance futures data is blocked from US servers; the research used Binance funding, so funding levels and costs can differ a little.
- Only coins in the bot's current top-50 list (plus BTC) can be picked at a rebalance. A held coin that has left the list is sold at the next rebalance, and it can be picked again once it is back in the list.
- These two come from `factors.py` (Part C research) and use its own code. The summary shows how many coins each holds and the top names. The first summary after the update says "🔭 Now watching 4 near-miss strategies."
- **Blend of the 4 (S12):** each week, the 4 watches above are mixed with more weight on the calmer ones (1 ÷ their swings over the last 9 weeks), using edge_v2.py's own code. A week counts once all its parts have finished (about 2 weeks later). The first summary after the update says "🔭 Now watching 5 near-miss strategies (added the blend)."
- To switch everything off, put `"PAPER_WATCH": false` in the `BOT_SETTINGS` secret. Single strategies: `"WATCH_S6": false`, `"WATCH_S8": false` or `"WATCH_S12": false`. Settings can be changed the same way, e.g. `"WATCH_S1": {"n": 55}` or `"WATCH_S5": {"k": 5}`.
- **These are not tradeable. Both failed the Phase 3 honesty rules.**

## 3. Telegram messages
| message | meaning |
|---|---|
| 🤖 **v10 LIVE** | sent once, the first time v10 starts |
| 📘 **HOW TO READ MY MESSAGES** | sent once: a plain guide to every message |
| 🟢/🔴 **A-GRADE 85/100 · COIN LONG/SHORT** | a setup. **A** = score 80+ (held up to 24 h), **B** = 65–79 (held up to 4 h). LONG = "Spot or Futures", SHORT = "Futures only". It shows entry, stop loss (SL), two take-profits (TP1, TP2), funding rate, open-interest change over 1 h, BTC trend and the AI note |
| 🎯 **TP1 HIT … SL moved to breakeven** | sell half at TP1 and move your stop to the entry price |
| ✅/🛑 **CLOSED** | the trade ended (TP2 HIT, STOP LOSS, BREAKEVEN after TP1, or TIME EXIT). Shows net % **after Binance fees** and the separate A and B win/loss records |
| 💓 **BOT ALIVE** | hourly check-in: scans done, closest near-misses, data sources in use, open trades, records, AI status, unavailable coins |
| 🤫 **Quiet mode on** | sent once when quiet mode starts |
| 📅 **DAILY SUMMARY** | quiet mode only, once a day: paper trades opened and closed (WIN/LOSS with % after fees), best and worst trade, A/B scoreboard for the last 24 h and all time, open trades, and what the AI approved, rejected or found unclear |
| 🚨 **CRASH WATCH** | a heads-up (also in quiet mode) when BTC or ETH fell 4%+ or another top-50 coin fell 12%+ in the last 60 minutes of closed 5-minute candles: the fall, the price now and the open-interest change ("many traders forced out" when it fell 5%+). Several coins in one message; the same coin again only after 6 h or a further full fall; at most 3 an hour. Heads-up only — not a trade signal |
| 🚨 **MARKET-WIDE CRASH WATCH** | instead of the above when 15+ top-50 coins each fell 5%+ in 60 minutes: how many, and the biggest falls |
| 📊 **WEEKLY RECAP** | Sundays at the daily-summary time (needs paper-watch on): the watched strategies ranked over 7 days and since they started, after fees, against just holding Bitcoin and USDT in Flexible Earn (beat both / one / neither), and the A/B scoreboard of the week. "(sent late)" if the bot was down (up to 24 h). Paper only |
| 🚨 **BOT DOWN** | from the watchdog: no scan has finished for over 2 hours. Open Actions and check the latest run |

Fees counted: LONG 0.10% each way (0.20% round trip), SHORT 0.05% each way (0.10%). A trade only counts as a win if it is positive after fees.

## 4. Optional settings
You can add these as `env:` lines in `war_room.yml`. Defaults are in brackets:
`TOP_N_COINS` (50), `MAX_HOLD_A_HOURS` (24), `MAX_HOLD_B_HOURS` (4), `FEE_PCT_SPOT` (0.10), `FEE_PCT_FUTURES` (0.05), `USE_DERIV_SCORE` (off; `1` adds up to ±10 points from funding, OI and BTC trend), `RUN_MINUTES` (58), `SCAN_INTERVAL` (60), `DRY_RUN` (`1` = print messages instead of sending), `MIN_TP1_FEE_MULT` (3; skip setups whose Take profit 1 is smaller than 3× the round-trip fee, `0` = off), `OB_PENALTY` (15; points removed when the order book leans against the trade, `0` = off), `EXCLUDE_COINS` (comma list).

## 5. Changing files for free
Open the file on GitHub → pencil icon → paste the new content → *Commit changes*. The bot keeps your edits. If you and the bot both change `state.json`, the bot's copy wins.

## Privacy (free public repo, everything valuable locked)
**Turn on encryption (once):** 1) Think of a long passphrase (6+ random words) and write it on paper or in your password manager. 2) GitHub → **Settings → Secrets and variables → Actions → Secrets tab → New repository secret**. 3) Name `DATA_KEY`, paste the passphrase, **Add secret**. 4) Done: from the next run the bot and recorder lock everything, and the "🔓" warning disappears. Never change or delete this secret, or old data can't be read.
- **Encryption:** with `DATA_KEY` set, the bot's memory is stored as `state.json.enc` and every recorded data file as `.jsonl.gz.enc` (AES-256-GCM; the key is made from your passphrase plus the public salt in `datalock.salt`). On the first run with the key, the old `state.json` is converted and removed from the latest version. Older versions stay in git history, so they still show what was there before.
- **Wrong key:** the bot never overwrites the locked file. It warns "⚠️ Can't unlock my memory — check the DATA_KEY secret" and keeps scanning.
- **Hidden settings:** put tunables in the `BOT_SETTINGS` secret (keys: `A_MIN`, `B_MIN`, `SL_ATR`, `TP1_ATR`, `TP2_ATR`, `OB_PENALTY`, `COOLDOWN_S`, `MIN_TP1_FEE_MULT`, `MAX_HOLD_A_HOURS`, `MAX_HOLD_B_HOURS`, `TOP_N_COINS`, `EXCLUDE_COINS` (list), `USE_DERIV_SCORE`, `FEE_PCT_SPOT`, `FEE_PCT_FUTURES`, `STRATEGY` (object for future strategies)).
- **Phase 6 settings** (also in `BOT_SETTINGS`):

| key | default | what it does |
|---|---|---|
| `WATCH_S12` | `true` | paper-watch the blend of the 4 near misses (5th line of the 🔭 block) |
| `EARN_APR` | `5.0` | yearly % for "🏦 USDT in Binance Flexible Earn" in the Sunday recap |
| `CRASH_WATCH` | `true` | send 🚨 CRASH WATCH heads-ups (also in quiet mode) |
| `CRASH_BIG_PCT` | `4` | BTC or ETH fall in 60 minutes that triggers a heads-up |
| `CRASH_ALT_PCT` | `12` | fall in 60 minutes for any other top-50 coin |
| `CRASH_MARKET_PCT` | `5` | fall per coin that counts towards a market-wide crash |
| `CRASH_MARKET_COUNT` | `15` | how many top-50 coins must fall that much for one 🚨 MARKET-WIDE message |
| `CRASH_OI_PCT` | `5` | open interest down this much in 1 h adds "many traders forced out" |

- **Quiet public logs:** on GitHub, Actions logs show only health lines (cycle done, counts, push ok, source failures without details). No coins, prices, scores, AI replies or wallet addresses. For local debugging run with `LOG_DETAIL=1`.
- **Private results:** backtest/research outputs (`backtest_report.md`, `research_report.md`, CSV/JSON results) are git-ignored. The earlier public backtest report was moved out of the repo into the ignored `private_reports/` folder.
- **Reading encrypted files (research):** `DATA_KEY='your passphrase' python datalock.py decrypt FILE.jsonl.gz.enc > out.jsonl`. A wrong key or a changed file gives a clear error.

## Edge-data recorder (`recorder.py`, workflow `recorder.yml`)
It records free public data that has no history you can download later, so every day counts. Data goes **only** to a separate `data` branch (main stays small), as new hourly files `data/YYYY/MM/DD/HH_<source>-<chunk>.jsonl.gz.enc`. `data/status.json` is public health only. It runs hourly like the bot, queues its own next run at minute 50, and pushes every 15 minutes.

| source | what | how often | where from |
|---|---|---|---|
| `hl_market` | funding, open interest, mark/oracle price, premium, 24h volume for every Hyperliquid perp | 5 min | Hyperliquid `metaAndAssetCtxs` |
| `hl_leaderboard` / `hl_wallets` / `hl_fills` | top-100 "smart" wallets (rule below), their open positions, and their fills | once per run / 15 min / daily | Hyperliquid leaderboard, `clearinghouseState`, `userFillsByTime` |
| `news` (+ `news_ai`) | headlines from CoinDesk, Cointelegraph, Decrypt, The Block, Bitcoin Magazine, CryptoSlate, rated by Gemini (coins, sentiment −2..+2, category, urgency) | 5 min (AI 15 min) | RSS feeds; Gemini on its own model `REC_LLM_MODEL` (default `gemini-3.1-flash-lite`, fallback `gemini-3.5-flash-lite`) with a daily cap `REC_AI_DAILY_CAP` (300), so the live bot's AI is never starved. When the cap or AI is hit, headlines are stored "unrated" and rated later |
| `listings` | new listing / delisting announcements | 5 min | OKX ✅, Binance ✅, KuCoin ✅ (added as a free fallback), Upbit ❌ (HTTP 403 from US servers; kept as best-effort) |
| `whales` | per coin per 5 min: number and USD of aggressive buys/sells ≥ $100k (top-10 coins) or ≥ $25k (others) | live stream | Binance public trade stream `data-stream.binance.vision` |
| `liquidations` | per coin per 5 min: long/short liquidated USD | 5 min | OKX public liquidation orders |
| `longshort` | long/short account ratio for BTC, ETH and the top 10 | 15 min | OKX Rubik |
| `context` | Fear & Greed, BTC dominance, total market cap, stablecoin supply | hourly | alternative.me, CoinGecko, DefiLlama |

Smart-wallet rule: account value ≥ $100k, 30-day PnL > 0, all-time ROI > 0. HLP/vault addresses and market makers (30-day volume > 300× account value) are excluded. Ranked by 30-day PnL, top 100 kept. The selection with its inputs is stored, so it can be checked later.
Every record has `obs` (when it was recorded, UTC ms) and `event_ts` (when it happened, when known). Research must only use `obs`, so it never looks ahead.
The daily summary shows a line like `📼 Recorder: ✅ 24/24 hours recorded (news 412 · wallets 100 · whales ✅ · listings 3)`. BOT ALIVE warns `⚠️ Recorder: <source> stalled` if a source has had no successful fetch for 3 hours.
Check all sources by hand: `python recorder.py --probe`. Run it outside GitHub (e.g. a home server): `RUN_MINUTES=0 REC_PUSH=0 REC_DIR=/path python recorder.py`.

## Data sources
- Prices, candles and order books come from Binance public market data at `https://data-api.binance.vision`. It works from US GitHub runners, where `api.binance.com` is blocked.
- If Binance is unreachable the bot tries KuCoin, then OKX, Kraken, Gate.io and Bybit. The alert then shows which exchange the data came from.
- Derivatives data (no key needed) is tried in this order: Hyperliquid, OKX, Gate.io, KuCoin Futures, Binance Futures. It shows "n/a" if all of them fail.
