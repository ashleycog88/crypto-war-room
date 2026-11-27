# Phase 1 — Fix and harden the War Room bot (phase-1)

## Goal
Rewrite `war_room_bot.py`, `.github/workflows/war_room.yml` and `requirements.txt` (plus a watchdog workflow, a diagnostics workflow, optional helper scripts and offline tests) so the bot scores setups correctly with live spot + derivatives data, sends graded alerts Telegram always accepts, tracks trades to a real after-fee outcome, persists state every run, scans every 60 s inside one hourly run, reports an hourly heartbeat, and fails loudly instead of silently. All free. Scoring/indicator code must live in importable functions so Phase 2's backtest reuses the exact same rules. See `/app/memory/product/overview.md` for integrations and edge cases.

## Functional requirements

### Market data & scoring
- REQ-1 [always] The system shall fetch the order book with limit=20 for every pair.
- REQ-2 [always] The system shall compute bid volume as the sum of b[1] over the top 20 bids.
- REQ-3 [always] The system shall compute ask volume as the sum of a[1] over the top 20 asks.
- REQ-4 [unwanted] If the order book fetch fails, then the system shall log the error with the pair name and score the order book as 0 for that scan.
- REQ-5 [always] The system shall read 5m volume ratio, RSI, EMA9/EMA21 and VWAP from the last CLOSED 5m candle (the candle still forming is excluded).
- REQ-6 [always] The system shall read the 1h EMA21/EMA50 trend and 1h RSI from the last CLOSED 1h candle (interpreted).
- REQ-7 [always] The system shall fetch at least 300 5m candles and at least 250 1h candles per pair.
- REQ-8 [always] The system shall use the live last price as the alert entry price, with SL/TP sized from the closed-candle ATR (interpreted).
- REQ-9 [unwanted] If fewer than 200 closed candles are returned for a timeframe, then the system shall skip that pair for that scan and log "insufficient candles".
- REQ-10 [unwanted] If the newest candle is stale (older than 2 candle periods), duplicated, or contains NaN/zero prices, then the system shall skip that pair for that scan with a logged reason (interpreted).
- REQ-11 [always] The system shall keep the existing base scoring weights (1H EMA 25, 5M EMA+VWAP 25, RSI 20, Volume 15, Order book 15) unchanged in this phase.

### Binance data & coin universe
- REQ-92 [always] The system shall use Binance spot market data from the public host "https://data-api.binance.vision" as the primary source for candles, order book and prices.
- REQ-93 [unwanted] If Binance data is unreachable, then the system shall fall back to KuCoin, then OKX, Kraken, Gate.io, Bybit, and show the source exchange on alerts.
- REQ-94 [event] When a run starts, the system shall build the coin list as the top 50 Binance USDT spot pairs by 24h quote volume.
- REQ-95 [always] The system shall exclude stablecoin, fiat, wrapped and leveraged-token pairs (e.g. USDC, FDUSD, TUSD, USDP, DAI, EUR, *UP/*DOWN) from the coin list (interpreted).
- REQ-96 [unwanted] If the top-50 list cannot be built, then the system shall use the last saved list from state.json, then a built-in list that includes the original 8 pairs.
- REQ-97 [always] The system shall keep tracking an open trade even after its coin leaves the top-50 list.
- REQ-98 [always] The system shall label LONG alerts "Spot or Futures".
- REQ-99 [always] The system shall label SHORT alerts "Futures only".
- REQ-100 [unwanted] If a coin is known to have no perpetual futures market on Binance or the derivatives sources, then the system shall not send SHORT alerts for it (interpreted).
- REQ-101 [always] The system shall fetch the 50 coins in parallel with rate-limit awareness so a full cycle finishes in under 45 seconds.
- REQ-102 [always] The system shall refresh 1h candles only when a new 1h candle has closed (cached otherwise) (interpreted).
- REQ-103 [always] The system shall throttle Telegram sends to at most about 1 message per second.

### Derivatives watch (live data)
- REQ-12 [always] The system shall fetch the current perpetual funding rate for each pair every scan cycle from the first reachable free derivatives source.
- REQ-13 [always] The system shall fetch open interest for each pair and compute its 1-hour % change.
- REQ-14 [always] The system shall compute a BTC market regime (up / down / flat) from BTC's closed 1h candles.
- REQ-15 [always] The system shall show funding rate, OI 1h change and BTC regime on every alert.
- REQ-16 [optional] Where USE_DERIV_SCORE=1 is set, the system shall apply the derivatives score adjustment (default off until Phase 2 proves it helps) (interpreted).
- REQ-17 [unwanted] If every derivatives source fails for a pair, then the system shall show "n/a" for that pair's derivatives fields and continue.
- REQ-18 [always] The system shall try derivatives sources in a fixed fallback order and remember the working one for the run.

### Grades & alerts
- REQ-19 [event] When a pair's winning side scores 80 or more, the system shall label the alert "A-GRADE".
- REQ-20 [event] When a pair's winning side scores 65 to 79, the system shall label the alert "B-GRADE".
- REQ-21 [always] The system shall send no alert for scores below 65.
- REQ-22 [always] The system shall show the grade label in the first line of the alert message.
- REQ-23 [always] The system shall keep the per-pair 1-hour cooldown and the one-open-trade-per-pair rule.
- REQ-24 [always] The system shall never alert twice for the same pair on the same closed 5m candle (interpreted).
- REQ-25 [event] When Telegram confirms an alert was delivered, the system shall record the trade and start the cooldown.
- REQ-26 [unwanted] If an alert fails to deliver, then the system shall not record the trade, not start the cooldown, and retry on the next cycle.

### AI review (Gemini)
- REQ-27 [always] The system shall read the AI reply from res.choices[0].message.content.
- REQ-28 [always] The system shall default LLM_BASE_URL to "https://generativelanguage.googleapis.com/v1beta/openai/" when the secret is empty.
- REQ-29 [always] The system shall default LLM_MODEL to "gemini-3.5-flash-lite" when the secret is empty.
- REQ-30 [unwanted] If the configured model call fails, then the system shall retry once with "gemini-3.8-flash".
- REQ-31 [event] When the AI reply starts with "REJECTED", the system shall drop the alert.
- REQ-32 [unwanted] If the AI key is missing, then the system shall send the alert with the note "AI unavailable".
- REQ-33 [unwanted] If the AI call errors, times out, is rate-limited or returns empty content, then the system shall send the alert with the note "AI unavailable".
- REQ-34 [always] The system shall request low reasoning effort with enough max_tokens that a one-sentence reply is not cut off.
- REQ-35 [always] The system shall cap every AI call at a 20-second timeout.

### Telegram safety
- REQ-36 [always] The system shall HTML-escape the AI text before it is placed in any Telegram message.
- REQ-37 [always] The system shall HTML-escape every other dynamic value placed in a Telegram message (interpreted).
- REQ-38 [always] The system shall truncate any message to at most 4096 characters.
- REQ-39 [unwanted] If Telegram rejects an HTML message, then the system shall resend it as plain text without parse_mode.
- REQ-40 [unwanted] If Telegram returns 429, then the system shall wait the retry_after seconds and resend once.

### Trade tracking & fees
- REQ-41 [always] The system shall store each new trade with grade, score, entry time, entry price, SL, TP1, TP2 and the exchange.
- REQ-42 [always] The system shall evaluate open trades every scan cycle using the highs and lows of 5m candles opened at or after the entry time plus the live price.
- REQ-43 [event] When TP1 is touched first, the system shall bank half the position at TP1.
- REQ-44 [event] When TP1 is touched first, the system shall move the stop loss to the entry price (breakeven).
- REQ-45 [event] When TP1 is touched, the system shall send a Telegram message containing "TP1 HIT" and "SL moved to breakeven".
- REQ-46 [event] When TP2 is touched after TP1, the system shall close the trade with gross PnL = 0.5 × TP1% + 0.5 × TP2%.
- REQ-47 [event] When the breakeven stop is touched after TP1, the system shall close the trade with gross PnL = 0.5 × TP1%.
- REQ-48 [event] When the original stop loss is touched before TP1, the system shall close the full trade at the SL price.
- REQ-49 [unwanted] If SL and a TP level are both touched inside the same candle, then the system shall treat the SL as hit first.
- REQ-50 [always] The system shall subtract round-trip fees from every closed trade's gross PnL to give net PnL: LONG trades at 2 × FEE_PCT_SPOT (default 0.10 → 0.20%), SHORT trades at 2 × FEE_PCT_FUTURES (default 0.05 → 0.10%) — Binance standard fees.
- REQ-51 [always] The system shall count a trade as a win only when its net PnL is above 0.
- REQ-52 [event] When an A-GRADE trade has been open MAX_HOLD_A_HOURS (temporary default 24), the system shall close it at the live price with status "TIME EXIT".
- REQ-53 [event] When a B-GRADE trade has been open MAX_HOLD_B_HOURS (temporary default 4), the system shall close it at the live price with status "TIME EXIT".
- REQ-54 [event] When a trade closes, the system shall send a Telegram message with the exit reason, net PnL % (after fees), and separate A and B records as W/L and win %.
- REQ-55 [unwanted] If price data for an open trade cannot be fetched, then the system shall keep the trade open and retry next cycle.

### State persistence
- REQ-56 [always] The system shall write state.json atomically (temp file then rename) after every state change.
- REQ-57 [event] When an old v9-format state.json is loaded, the system shall upgrade it in place without losing trades or cooldowns.
- REQ-58 [unwanted] If state.json is unreadable, then the system shall keep a backup copy and start from an empty state.
- REQ-59 [always] The workflow shall commit and push state.json at the end of every run, even when the bot step failed.
- REQ-60 [unwanted] If the push is rejected, then the workflow shall pull with rebase and retry up to 5 times.
- REQ-61 [unwanted] If the rebase conflicts on state.json, then the workflow shall keep the bot's version of state.json.
- REQ-62 [always] The system shall checkpoint-push state.json during the run at most every 15 minutes when it has changed (interpreted).
- REQ-63 [always] The system shall cap closed_trades history at the most recent 1000 trades while keeping all-time A/B totals (interpreted).

### Scheduling & resilience
- REQ-64 [always] The workflow shall be triggered once per hour on a cron minute that is not :00, plus manual "Run workflow".
- REQ-65 [always] The workflow shall use a concurrency group with cancel-in-progress false so runs never overlap.
- REQ-66 [always] The bot shall run a scan cycle every 60 seconds for about 58 minutes per run.
- REQ-67 [unwanted] If a scan cycle errors, then the system shall log it and continue the loop.
- REQ-68 [unwanted] If the exchange becomes unreachable, then the system shall reconnect or fall back to the next exchange in the list.
- REQ-69 [always] The workflow shall set job and step timeouts so the commit step always gets time to run.
- REQ-70 [always] The system shall put a timeout on every network call (exchange, derivatives, Telegram, AI).
- REQ-71 [always] The system shall keep the spot exchange fallback order Binance (data-api host), KuCoin, OKX, Kraken, Gate.io, Bybit.
- REQ-72 [unwanted] If a pair is not listed on the exchange in use, then the system shall report it in the heartbeat as "unavailable".

### Heartbeat
- REQ-73 [event] When 60 minutes have passed since the last heartbeat, the system shall send a Telegram message containing "BOT ALIVE".
- REQ-74 [always] The heartbeat shall show the number of scan cycles done in the last hour.
- REQ-75 [always] The heartbeat shall show the top 3 near-miss scores below 65 (pair, direction, score).
- REQ-76 [always] The heartbeat shall show the exchange in use, derivatives source in use, open trade count, A and B net records, AI status and unavailable pairs.
- REQ-77 [event] When v10 starts for the first time, the system shall send a one-time "v10 LIVE" message.

### Watchdog & diagnostics
- REQ-78 [always] The system shall store the time of the last completed scan cycle in state.json.
- REQ-79 [event] When the watchdog workflow (every 2 hours, offset from the main cron) finds the last completed cycle older than 2 hours, it shall send a Telegram message containing "BOT DOWN".
- REQ-80 [optional] Where HEALTHCHECK_URL is set, the system shall ping it after every completed scan cycle.
- REQ-81 [event] When the user runs the diagnostics workflow manually, it shall print a pass/fail line for each spot exchange, each derivatives source, Telegram and Gemini.
- REQ-82 [unwanted] If a diagnostics check fails, then the workflow shall still finish and print the failure reason.

### Security
- REQ-83 [always] The system shall never print the Telegram token, chat ID, AI key or full request URLs containing them.
- REQ-84 [always] The workflow shall register the cleaned Telegram token with GitHub's log masking (::add-mask::) before running the bot.
- REQ-85 [always] The workflows shall pin every third-party action to a full commit SHA.
- REQ-86 [always] The workflows shall request only contents: write (main) or contents: read (watchdog, diagnostics) permissions.
- REQ-87 [always] The workflows shall trigger only on schedule and workflow_dispatch (no pull_request triggers).
- REQ-88 [always] requirements.txt shall pin exact versions of current, maintained releases that pass the tests.

### Config & ops
- REQ-89 [optional] Where DRY_RUN=1 is set, the system shall print Telegram messages instead of sending them.
- REQ-90 [optional] Where RUN_MINUTES, SCAN_INTERVAL, FEE_PCT_SPOT, FEE_PCT_FUTURES, MAX_HOLD_A_HOURS, MAX_HOLD_B_HOURS or TOP_N_COINS are set, the system shall use them instead of the defaults.
- REQ-91 [always] The repo shall contain no frontend, backend, website or app code.

## Quality requirements
```
Q-1  Tag: Delivery.TelegramAccepted
     Scale: % of bot messages Telegram accepts (HTTP 200)
     Meter: offline test with hostile AI text (<, >, &, unclosed <b>, 5000 chars) + live test messages to the user's chat
     Must: 100%   Plan: 100%
Q-2  Tag: Money.PnLCorrect
     Scale: net PnL % error vs hand-computed value for synthetic candle paths (SL first, TP1→TP2, TP1→BE, time exit, same-candle SL+TP), LONG and SHORT
     Meter: unit tests, 10+ scenarios
     Must: 0.01   Plan: 0.00
Q-3  Tag: Speed.CycleTime
     Scale: seconds per full 50-coin scan cycle incl. derivatives + trade checks
     Meter: live run from the pod, 5 cycles
     Must: 45 s   Plan: 20 s
Q-4  Tag: Data.StateLoss
     Scale: trades or cooldowns lost across a forced crash + restart
     Meter: kill the bot mid-run in a test, reload state.json
     Must: 0   Plan: 0
Q-5  Tag: Trust.NoDuplicateOrLostAlerts
     Scale: duplicate alerts per pair+candle, and trades recorded without a delivered alert, across 60 simulated cycles
     Meter: offline simulation
     Must: 0   Plan: 0
Q-6  Tag: Security.SecretLeak
     Scale: occurrences of the token / key / chat id in stdout/stderr across a full test run with fake secrets
     Meter: grep logs of test runs
     Must: 0   Plan: 0
Q-7  Tag: Reliability.SilentFailure
     Scale: minutes from bot stopping to the user being told ("BOT DOWN")
     Meter: offline watchdog test with stale timestamp
     Must: 240   Plan: 120
Q-8  Tag: Cost.Free
     Scale: $ per month to run
     Meter: human review of services used
     Must: 0   Plan: 0
Q-9  Tag: UX.Clarity
     Scale: minutes for the non-technical user to update GitHub secrets using the guide unaided
     Meter: human
     Must: 15   Plan: 5 (estimate)
```
