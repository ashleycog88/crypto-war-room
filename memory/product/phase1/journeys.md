# Phase 1 Journeys — War Room bot v10

Most checks are offline (pytest with synthetic data / mocked exchange, derivatives, Telegram and AI) plus a short live dry run from the pod. JRN-30..32 use the user's real Telegram secrets. No browser/UI exists. Default fees: LONG 0.10% each way (0.20% round trip), SHORT 0.05% each way (0.10% round trip).

- id: JRN-1
  steps: Feed an order book of 20 bids and 20 asks as [price, size] pairs (bids total size 300, asks total 100).
  expected: Order-book imbalance = +0.50; the setup gets "Bid Wall +50%" and +15 to the long score. fetch_order_book was called with limit=20.

- id: JRN-2
  steps: Make the order book fetch raise an error.
  expected: Imbalance = 0, the error is logged with the pair name, the scan continues.

- id: JRN-3
  steps: Build 5m candles where the last CLOSED candle has volume 2x the average and the forming candle has tiny volume.
  expected: vol_r ≈ 2.0 is used (closed candle), not the forming candle's value. Same for RSI and EMA9/EMA21.

- id: JRN-4
  steps: Build 1h candles where the closed candle is bullish-stacked and the forming candle is not.
  expected: 1H Bull EMA is scored from the closed 1h candle. (interpreted)

- id: JRN-5
  steps: Run a live scan from the pod against the real exchange.
  expected: fetch_ohlcv is requested with ≥300 (5m) and ≥250 (1h) candles; at least 200 closed candles are used for EMA50.

- id: JRN-6
  steps: Return only 150 candles for a pair; separately return candles whose newest timestamp is 30 minutes old; separately include a NaN close.
  expected: Each time that pair is skipped with the reason logged ("insufficient candles" / stale / bad data); other pairs still scan.

- id: JRN-7
  steps: Make a setup score 85 (long).
  expected: Alert first line contains "A-GRADE" and "85/100".

- id: JRN-8
  steps: Make a setup score 70 (short).
  expected: Alert first line contains "B-GRADE" and "70/100".

- id: JRN-9
  steps: Make setups score 80 and 65 exactly, then 60.
  expected: 80 → A-GRADE, 65 → B-GRADE, 60 → no alert (but recorded as a near-miss).

- id: JRN-10
  steps: After an alert on a pair, run another 60 cycles on the same closed candle and within 1 hour.
  expected: Zero further alerts for that pair (cooldown, open trade, same-candle rules).

- id: JRN-11
  steps: Mock the AI to reply "APPROVED: momentum aligned, watch BTC dominance."
  expected: Code reads res.choices[0].message.content; alert is sent and contains the AI sentence.

- id: JRN-12
  steps: Mock the AI to reply "REJECTED: thin liquidity."
  expected: No alert sent; no trade opened; rejection counted for the heartbeat.

- id: JRN-13
  steps: Run with LLM_API_KEY empty (the user's real situation today).
  expected: Alert is sent with the note "AI unavailable"; no crash.

- id: JRN-14
  steps: Mock the AI to raise 429, hang past 20 s, and return content None — one at a time.
  expected: Each time the alert is sent with "AI unavailable"; the hang is cut off at ~20 s.

- id: JRN-15
  steps: Leave LLM_BASE_URL and LLM_MODEL empty, then make the first model call fail with 404.
  expected: Client uses base_url "https://generativelanguage.googleapis.com/v1beta/openai/", model "gemini-3.5-flash-lite", then retries once with "gemini-3.8-flash".

- id: JRN-16
  steps: Mock the AI to reply with text containing `<script>`, `a < b & c > d`, an unclosed `<b>`, and 5000 characters.
  expected: The final Telegram payload has these escaped (&lt; &gt; &amp;), total length ≤ 4096, valid Telegram HTML.

- id: JRN-17
  steps: Mock Telegram to return 400 "can't parse entities"; separately return 429 with retry_after 3.
  expected: 400 → resent once as plain text without parse_mode. 429 → waits ~3 s then resends once.

- id: JRN-18
  steps: LONG trade, entry 100, SL 98.2, TP1 102, TP2 103.6. Candles after entry: low 98.0 first.
  expected: Closed "STOP LOSS", gross -1.80%, net -2.00%, counts as a loss in its grade's record.

- id: JRN-19
  steps: Same LONG trade; candles: high 102.1, later high 103.7.
  expected: "TP1 HIT … SL moved to breakeven" message, then closed "TP2 HIT" with gross +2.80%, net +2.60%.

- id: JRN-20
  steps: Same LONG trade; candles: high 102.1, later low 99.9.
  expected: TP1 message, then closed at breakeven with gross +1.00%, net +0.80%, counts as a win.

- id: JRN-21
  steps: Same LONG trade; one candle with high 102.5 AND low 98.0.
  expected: SL treated as first: closed "STOP LOSS", net -2.00%.

- id: JRN-22
  steps: SHORT trade mirror of JRN-18..20 (entry 100, SL 101.8, TP1 98, TP2 96.4).
  expected: Same results with direction mirrored but futures fees: net -1.90%, +2.70%, +0.90%.

- id: JRN-23
  steps: A-GRADE trade open 24h with no level touched, live price 100.5 (LONG entry 100).
  expected: Closed "TIME EXIT" gross +0.50%, net +0.30%.

- id: JRN-24
  steps: B-GRADE trade open 4h with no level touched, live price 99.5 (LONG entry 100). Also a B-GRADE trade open 3h59m. Then set MAX_HOLD_B_HOURS=2 and repeat.
  expected: First closes "TIME EXIT" net -0.70%; second stays open; with MAX_HOLD_B_HOURS=2 a 2h-old B trade closes.

- id: JRN-25
  steps: Close 2 A trades (1 net win, 1 net loss) and 1 B trade (net win). Also a trade with gross +0.15% (net -0.05%).
  expected: Close message shows separate records like "A: 1W/1L (50%)" and "B: 1W/0L (100%)"; the +0.15% gross trade counts as a loss (net below 0).

- id: JRN-26
  steps: Make candle fetch fail for an open trade.
  expected: Trade stays open; retried next cycle; no crash.

- id: JRN-27
  steps: Load the current repo's v9 state.json (and a v9 file with an old active trade that has no grade/entry_time).
  expected: Loads without error, upgraded to v10 fields, cooldowns kept, old trade still tracked.

- id: JRN-28
  steps: Write corrupted JSON into state.json and start the bot.
  expected: Backup copy kept, bot starts from empty state, no crash.

- id: JRN-29
  steps: Kill the bot mid-run right after a trade opens, then restart.
  expected: state.json on disk already has the trade (atomic write); no duplicate alert for it after restart.

- id: JRN-30
  steps: Run the bot from the pod with the user's real TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID, RUN_MINUTES=3, no AI key, using a throwaway copy of state.json.
  expected: The user's chat receives the one-time "v10 LIVE" message and a "BOT ALIVE" heartbeat; Telegram answers HTTP 200 to every send. Repo state.json unchanged.

- id: JRN-31
  steps: From the pod, send one sample A-GRADE alert, one B-GRADE alert, one TP1 HIT, one CLOSED and one BOT DOWN message (each clearly marked "TEST") to the real chat.
  expected: All 5 arrive, HTTP 200, formatting intact, derivatives fields shown on alerts.

- id: JRN-32
  steps: Read the heartbeat after a short run.
  expected: Contains "BOT ALIVE", scan cycles done, top 3 near-miss scores (pair, direction, score below 65), exchange, derivatives source, open trades, A/B net records, AI status, unavailable pairs.

- id: JRN-33
  steps: Run with DRY_RUN=1 RUN_MINUTES=2 SCAN_INTERVAL=60.
  expected: Messages printed, not sent; 2 cycles ~60 s apart; bot exits cleanly; each cycle under 45 s.

- id: JRN-34
  steps: Make one scan cycle raise an exception, and make the exchange unreachable once.
  expected: Loop continues to the next cycle; data source reconnects or falls back (Binance data-api → KuCoin → OKX → Kraken → Gate.io → Bybit).

- id: JRN-35
  steps: Read .github/workflows/war_room.yml.
  expected: Hourly cron on a minute other than :00, workflow_dispatch present, no pull_request triggers, concurrency group with cancel-in-progress false, contents: write only, job + step timeouts, ::add-mask:: of the cleaned token, commit step with if: always(), push retries 5× with pull --rebase, state.json conflict resolved with the bot's version, actions pinned to SHAs.

- id: JRN-36
  steps: In a local git sandbox, simulate a remote commit to state.json (and to another file) before the bot's push; run the workflow's commit script.
  expected: Push succeeds after rebase; remote ends with the bot's state.json and the other file's change preserved.

- id: JRN-37
  steps: Check the in-run checkpoint.
  expected: State is pushed mid-run no more often than every 15 minutes, and only when state changed. (interpreted)

- id: JRN-38
  steps: Check the repo tree after the build.
  expected: No frontend/, backend/ or web/app files; only bot files, workflows, requirements.txt, helper scripts, tests and README changed. state.json unchanged in git.

- id: JRN-39
  steps: Mock derivatives sources: first source errors, second returns funding 0.0001 and OI rising 3% in 1h.
  expected: Second source used and remembered; alert shows funding and "OI +3.0% 1h" and BTC regime; with USE_DERIV_SCORE unset the score is unchanged.

- id: JRN-40
  steps: Make every derivatives source fail.
  expected: Alert still sent with derivatives fields "n/a"; heartbeat shows derivatives source "none".

- id: JRN-41
  steps: Mock Telegram to fail (network error) for a qualifying A-GRADE setup.
  expected: No trade recorded, no cooldown started; next cycle the alert is retried and, once delivered, the trade is recorded exactly once.

- id: JRN-42
  steps: Use a fallback exchange mock that lacks BNB/USDT.
  expected: Heartbeat lists "BNB/USDT unavailable"; other pairs scan normally.

- id: JRN-43
  steps: Run the full offline test suite and a dry run with fake secrets (token "123456789:FAKEtokenFAKEtokenFAKEtoken123", key "FAKEKEY") while capturing stdout/stderr; force Telegram/AI errors.
  expected: Neither fake secret nor the chat ID appears anywhere in the captured logs.

- id: JRN-44
  steps: Run the watchdog logic with last-cycle time 3 hours old, then 30 minutes old.
  expected: 3h → "BOT DOWN" message sent (shows how long since last cycle); 30m → nothing sent.

- id: JRN-45
  steps: Run the diagnostics script from the pod (and read the diagnostics workflow file).
  expected: One pass/fail line per spot exchange, per derivatives source, Telegram and Gemini (Gemini "skipped: no key" when empty); script exits 0 even if some checks fail; workflow is workflow_dispatch only with contents: read.

- id: JRN-46
  steps: Set HEALTHCHECK_URL to a local mock and run 2 cycles; then unset it.
  expected: Mock receives 2 pings; with it unset, no ping attempted and no error.

- id: JRN-47
  steps: Run a live dry cycle from the pod.
  expected: Candles, order book and prices come from https://data-api.binance.vision (not api.binance.com); alerts/heartbeat show "BINANCE" as source.

- id: JRN-48
  steps: Mock 24h tickers containing BTCUSDT, ETHUSDT, USDCUSDT, FDUSDUSDT, EURUSDT, BTCUPUSDT, WBTCUSDT and 60 normal coins.
  expected: Coin list has exactly 50 entries, ordered by quote volume, with none of the stablecoin/fiat/wrapped/leveraged pairs. (interpreted)

- id: JRN-49
  steps: Make the 24h ticker call fail on a run with a saved list in state.json; then with no saved list.
  expected: First uses the saved list; second uses the built-in list that includes the original 8 pairs.

- id: JRN-50
  steps: Open a trade on a coin, then rebuild the coin list without that coin.
  expected: The trade keeps being tracked to TP/SL/time exit; no new alerts for that coin.

- id: JRN-51
  steps: Send a LONG alert and a SHORT alert; then a SHORT setup on a coin known to have no perpetual market.
  expected: LONG shows "Spot or Futures"; SHORT shows "Futures only"; the no-perp SHORT is not sent. (interpreted)

- id: JRN-52
  steps: Run 5 live cycles over 50 coins from the pod.
  expected: Every cycle under 45 s; no HTTP 429/418 from Binance; 1h candles re-fetched only after an hour boundary.

- id: JRN-53
  steps: Make 10 setups qualify in the same cycle (mocked).
  expected: 10 alerts sent roughly 1 s apart, all HTTP 200, each recorded once.
