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
| `state.json` | the bot's memory (open trades, records, cooldowns). The bot updates it. Don't edit it by hand |
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
| `HEALTHCHECK_URL` | optional: a free https://healthchecks.io ping URL that warns you by e-mail if the bot stops |

Never link a billing account to the Gemini key. The free tier is enough, and the bot backs off when Google says "too many requests".

## 2. Turn it on
1. Actions tab → enable workflows if GitHub asks.
2. **War Room diagnostics** → *Run workflow*. Read the PASS/FAIL lines: one per spot exchange, one per derivatives source, then Telegram and Gemini. Gemini shows "skipped: no key" if you have no key. Tick *send_test_message* to also get one TEST message.
3. **Crypto War Room v10** → *Run workflow* (after that it runs every hour by itself).
4. GitHub switches off scheduled workflows in public repos after 60 days without commits. The bot commits `state.json` every 15 minutes, so this shouldn't happen. If it ever does, open Actions and press *Enable workflow*.

## 3. Telegram messages
| message | meaning |
|---|---|
| 🤖 **v10 LIVE** | sent once, the first time v10 starts |
| 🟢/🔴 **A-GRADE 85/100 · COIN LONG/SHORT** | a setup. **A** = score 80+ (held up to 24 h), **B** = 65–79 (held up to 4 h). LONG = "Spot or Futures", SHORT = "Futures only". It shows entry, stop loss (SL), two take-profits (TP1, TP2), funding rate, open-interest change over 1 h, BTC trend and the AI note |
| 🎯 **TP1 HIT … SL moved to breakeven** | sell half at TP1 and move your stop to the entry price |
| ✅/🛑 **CLOSED** | the trade ended (TP2 HIT, STOP LOSS, BREAKEVEN after TP1, or TIME EXIT). Shows net % **after Binance fees** and the separate A and B win/loss records |
| 💓 **BOT ALIVE** | hourly check-in: scans done, closest near-misses, data sources in use, open trades, records, AI status, unavailable coins |
| 🚨 **BOT DOWN** | from the watchdog: no scan has finished for over 2 hours. Open Actions and check the latest run |

Fees counted: LONG 0.10% each way (0.20% round trip), SHORT 0.05% each way (0.10%). A trade only counts as a win if it is positive after fees.

## 4. Optional settings
You can add these as `env:` lines in `war_room.yml`. Defaults are in brackets:
`TOP_N_COINS` (50), `MAX_HOLD_A_HOURS` (24), `MAX_HOLD_B_HOURS` (4), `FEE_PCT_SPOT` (0.10), `FEE_PCT_FUTURES` (0.05), `USE_DERIV_SCORE` (off; `1` adds up to ±10 points from funding, OI and BTC trend), `RUN_MINUTES` (58), `SCAN_INTERVAL` (60), `DRY_RUN` (`1` = print messages instead of sending).

## 5. Changing files for free
Open the file on GitHub → pencil icon → paste the new content → *Commit changes*. The bot keeps your edits. If you and the bot both change `state.json`, the bot's copy wins.

## Data sources
- Prices, candles and order books come from Binance public market data at `https://data-api.binance.vision`. It works from US GitHub runners, where `api.binance.com` is blocked.
- If Binance is unreachable the bot tries KuCoin, then OKX, Kraken, Gate.io and Bybit. The alert then shows which exchange the data came from.
- Derivatives data (no key needed) is tried in this order: Hyperliquid, OKX, Gate.io, KuCoin Futures, Binance Futures. It shows "n/a" if all of them fail.
