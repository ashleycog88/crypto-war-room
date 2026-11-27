# War Room v10 — 3-month backtest report

## Summary (plain English)

**NO — none of the tested settings made money after fees on the unseen month. Do not trade real money with these rules.**
- Live rules as they are now: 11256 trades on the unseen month, -2128.8% total after fees, win rate 44.4%; over all 3 months -6733.1% in 31673 trades.
- A-GRADE and B-GRADE are about equally bad (-0.308 vs -0.310 R per trade); less bad side: SHORT (-0.292 R vs -0.348). R = the result as a multiple of the stop-loss distance; -0.3 R means you lose about a third of your risk on every trade.
- Least-bad settings found on the first 2 months (all still lost money): hold A 4 h, B 1 h; exit style (b) full exit at TP1; fee floor 5×. Together on the unseen month: 4107 trades, -534.4% total.
- Recommendation: keep the bot as an alert/learning tool only and do not risk real money on it (recommended settings: train: 4817 trades, -774.3% total, -0.231 R/trade, win 43.4% → unseen: 4107 trades, -534.4% total, -0.179 R/trade, win 45.6%).

## How this was tested

- Period: 2026-08-27 → 2026-11-27 (training = first 2 months to 2026-10-27, unseen test = last month). 50 coins: today's live top-50 list + BTC.
- Each 5-minute candle close was scored by the **live bot's own code** (imported from war_room_bot.py) on the exact candles the live bot would have seen (309 closed 5m + 259 closed 1h). Order book = 0 (no history), AI review skipped (it cannot be replayed: the model's answers are not reproducible and the free key's rate limit cannot serve tens of thousands of setups).
- Entry = next 5m candle open + 0.05% slippage against you. Same 1 h cooldown, one trade per coin, stop-loss-first rule, half at TP1 then stop to breakeven, fees LONG 0.20% / SHORT 0.10% round trip.
- 'Total net %' = simple sum of per-trade % results (each trade the same size, no compounding). Max DD = largest drop of that running sum. PF = profit factor (gross wins ÷ gross losses). Exp R = average result divided by the stop-loss distance.
- Look-ahead check: 584208 simulated trades checked, **0 used a candle or derivatives value that was not yet available**.

## Results

**A-GRADE vs B-GRADE (live rules)** (full 3 months)

| Variant | Trades | Win % | Avg net % | Total net % | PF | Max DD % | Exp R | Avg hold h |
|---|---|---|---|---|---|---|---|---|
| A-GRADE | 6743 | 44.2 | -0.204 | -1377.93 | 0.59 | 1382.52 | -0.308 | 0.92 |
| B-GRADE | 24930 | 43.2 | -0.215 | -5355.18 | 0.59 | 5363.35 | -0.31 | 0.96 |

**LONG vs SHORT (live rules)** (full 3 months)

| Variant | Trades | Win % | Avg net % | Total net % | PF | Max DD % | Exp R | Avg hold h |
|---|---|---|---|---|---|---|---|---|
| LONG | 10035 | 43.0 | -0.314 | -3149.89 | 0.57 | 3152.73 | -0.348 | 0.91 |
| SHORT | 21638 | 43.7 | -0.166 | -3583.23 | 0.61 | 3592.17 | -0.292 | 0.97 |

**Max hold time — A-GRADE trades** (full 3 months)

| Variant | Trades | Win % | Avg net % | Total net % | PF | Max DD % | Exp R | Avg hold h |
|---|---|---|---|---|---|---|---|---|
| 1 h | 8152 | 41.4 | -0.203 | -1657.21 | 0.54 | 1662.65 | -0.31 | 0.62 |
| 2 h | 7165 | 43.4 | -0.2 | -1430.7 | 0.58 | 1432.17 | -0.307 | 0.79 |
| 4 h | 6772 | 44.1 | -0.2 | -1356.5 | 0.59 | 1364.76 | -0.308 | 0.88 |
| 8 h | 6678 | 44.0 | -0.208 | -1386.68 | 0.58 | 1390.49 | -0.313 | 0.91 |
| 12 h | 6651 | 44.1 | -0.207 | -1375.17 | 0.59 | 1378.97 | -0.311 | 0.91 |
| 24 h | 6626 | 44.1 | -0.208 | -1374.9 | 0.59 | 1378.71 | -0.312 | 0.92 |
| 48 h | 6623 | 44.1 | -0.208 | -1374.48 | 0.59 | 1378.29 | -0.311 | 0.92 |

**Max hold time — B-GRADE trades** (full 3 months)

| Variant | Trades | Win % | Avg net % | Total net % | PF | Max DD % | Exp R | Avg hold h |
|---|---|---|---|---|---|---|---|---|
| 1 h | 32648 | 40.7 | -0.208 | -6793.55 | 0.55 | 6794.57 | -0.306 | 0.65 |
| 2 h | 26831 | 42.8 | -0.214 | -5729.57 | 0.58 | 5730.59 | -0.309 | 0.85 |
| 4 h | 25015 | 43.3 | -0.215 | -5373.58 | 0.59 | 5380.95 | -0.31 | 0.96 |
| 8 h | 24537 | 43.3 | -0.216 | -5303.45 | 0.6 | 5304.8 | -0.311 | 1.0 |
| 12 h | 24436 | 43.3 | -0.216 | -5278.11 | 0.6 | 5279.45 | -0.31 | 1.01 |
| 24 h | 24376 | 43.4 | -0.216 | -5257.49 | 0.6 | 5258.83 | -0.31 | 1.02 |
| 48 h | 24363 | 43.4 | -0.216 | -5256.43 | 0.6 | 5257.77 | -0.31 | 1.02 |

**Exit styles (live hold times A 24 h / B 4 h)** (full 3 months)

| Variant | Trades | Win % | Avg net % | Total net % | PF | Max DD % | Exp R | Avg hold h |
|---|---|---|---|---|---|---|---|---|
| (a) live: half at TP1, breakeven, rest to TP2 | 31673 | 43.5 | -0.213 | -6733.12 | 0.59 | 6744.0 | -0.31 | 0.95 |
| (b) full exit at TP1 | 35210 | 43.2 | -0.194 | -6845.87 | 0.63 | 6851.3 | -0.285 | 0.75 |
| (c) half at TP1 + 1-ATR trailing stop | 34483 | 43.2 | -0.201 | -6925.36 | 0.62 | 6929.14 | -0.29 | 0.83 |
| (d) half at TP1 + breakeven, hold to max time | 22894 | 43.5 | -0.204 | -4679.01 | 0.59 | 4688.28 | -0.309 | 1.66 |

**Fee floor MIN_TP1_FEE_MULT (skip setups whose TP1 is too small vs fees)** (full 3 months)

| Variant | Trades | Win % | Avg net % | Total net % | PF | Max DD % | Exp R | Avg hold h |
|---|---|---|---|---|---|---|---|---|
| TP1 ≥ 0× round-trip fee (off) | 39056 | 40.6 | -0.216 | -8441.09 | 0.55 | 8443.35 | -0.428 | 0.91 |
| TP1 ≥ 2× round-trip fee | 36075 | 43.0 | -0.216 | -7785.09 | 0.57 | 7788.28 | -0.354 | 0.92 |
| TP1 ≥ 3× round-trip fee (live default) | 31673 | 43.5 | -0.213 | -6733.12 | 0.59 | 6744.0 | -0.31 | 0.95 |
| TP1 ≥ 5× round-trip fee | 21458 | 44.3 | -0.215 | -4614.28 | 0.64 | 4633.49 | -0.251 | 1.01 |

**Derivatives variants** (full 3 months)

| Variant | Trades | Win % | Avg net % | Total net % | PF | Max DD % | Exp R | Avg hold h |
|---|---|---|---|---|---|---|---|---|
| Off (live default, all coins) | 31673 | 43.5 | -0.213 | -6733.12 | 0.59 | 6744.0 | -0.31 | 0.95 |
| BTC-regime filter (all coins) | 27685 | 43.4 | -0.21 | -5805.8 | 0.59 | 5812.42 | -0.311 | 0.95 |
| Off — coins with derivatives history only | 28227 | 43.4 | -0.212 | -5989.5 | 0.6 | 6004.05 | -0.311 | 0.96 |
| USE_DERIV_SCORE on — same coins | 28191 | 43.5 | -0.209 | -5891.26 | 0.6 | 5897.16 | -0.308 | 0.96 |

Coins excluded from the USE_DERIV_SCORE comparison (no full funding + open-interest history): PORTAL/USDT, HAEDAL/USDT, CFG/USDT, HEI/USDT, THE/USDT. They are still in all other results.

### What-if: order-book thresholds (NOT the main result)
The order book is worth 15 points live but has no history, so the top possible score here is 85. This what-if scales the grade lines to that maximum (A ≥ 68, B ≥ 55). Treat it as a sensitivity check only.

**What-if thresholds** (full 3 months)

| Variant | Trades | Win % | Avg net % | Total net % | PF | Max DD % | Exp R | Avg hold h |
|---|---|---|---|---|---|---|---|---|
| Live thresholds (80/65) | 31673 | 43.5 | -0.213 | -6733.12 | 0.59 | 6744.0 | -0.31 | 0.95 |
| What-if thresholds (68/55) | 39459 | 43.4 | -0.218 | -8613.01 | 0.58 | 8634.78 | -0.316 | 0.99 |

### Per-coin ranking (live rules, full period, best first)

| # | Coin | Trades | Total net % | Win % | Exp R | Train net % | Unseen net % | Flags |
|---|---|---|---|---|---|---|---|---|
| 1 | TRX/USDT | 26 | -6.47 | 42.3 | -0.208 | +0.34 | -6.81 |  |
| 2 | BTC/USDT | 278 | -48.64 | 41.0 | -0.43 | -20.56 | -28.08 |  |
| 3 | BNB/USDT | 276 | -52.02 | 39.5 | -0.446 | -24.16 | -27.86 |  |
| 4 | HYPE/USDT | 375 | -57.50 | 46.9 | -0.258 | -24.98 | -32.52 | partial history (from 2026-09-24 11:00) |
| 5 | HAEDAL/USDT | 562 | -69.98 | 47.7 | -0.214 | -61.85 | -8.13 |  |
| 6 | INIT/USDT | 697 | -75.08 | 45.1 | -0.241 | -116.05 | +40.97 |  |
| 7 | SSV/USDT | 571 | -78.93 | 44.3 | -0.325 | -84.34 | +5.41 |  |
| 8 | LTC/USDT | 556 | -81.14 | 45.5 | -0.31 | -54.39 | -26.75 |  |
| 9 | ETH/USDT | 386 | -83.39 | 39.9 | -0.47 | -49.87 | -33.52 |  |
| 10 | BIGTIME/USDT | 663 | -90.98 | 45.6 | -0.301 | -58.93 | -32.05 |  |
| 11 | SOL/USDT | 509 | -97.35 | 42.8 | -0.354 | -57.13 | -40.22 |  |
| 12 | DOGE/USDT | 523 | -99.64 | 41.1 | -0.387 | -68.84 | -30.80 |  |
| 13 | MMT/USDT | 669 | -100.75 | 45.7 | -0.262 | -83.16 | -17.58 |  |
| 14 | GRAM/USDT | 608 | -121.11 | 42.3 | -0.347 | -72.28 | -48.83 |  |
| 15 | AVAX/USDT | 588 | -122.73 | 41.3 | -0.355 | -80.65 | -42.08 |  |
| 16 | HBAR/USDT | 643 | -124.47 | 42.1 | -0.359 | -84.71 | -39.75 |  |
| 17 | XRP/USDT | 543 | -124.61 | 39.4 | -0.44 | -77.17 | -47.44 |  |
| 18 | BCH/USDT | 600 | -126.24 | 40.7 | -0.402 | -82.66 | -43.58 |  |
| 19 | SEI/USDT | 718 | -126.28 | 44.6 | -0.292 | -79.63 | -46.65 |  |
| 20 | SKY/USDT | 697 | -127.84 | 44.2 | -0.293 | -90.23 | -37.62 |  |
| 21 | LISTA/USDT | 582 | -130.71 | 42.4 | -0.354 | -90.53 | -40.18 |  |
| 22 | AAVE/USDT | 682 | -131.40 | 45.2 | -0.289 | -72.21 | -59.19 |  |
| 23 | XLM/USDT | 624 | -135.75 | 42.0 | -0.385 | -99.24 | -36.52 |  |
| 24 | LINK/USDT | 602 | -138.44 | 40.4 | -0.415 | -98.67 | -39.77 |  |
| 25 | UNI/USDT | 700 | -138.99 | 45.3 | -0.239 | -99.46 | -39.53 |  |
| 26 | FIL/USDT | 696 | -139.47 | 45.0 | -0.259 | -109.66 | -29.80 |  |
| 27 | BLUR/USDT | 719 | -140.52 | 47.1 | -0.23 | -71.06 | -69.46 |  |
| 28 | WLD/USDT | 798 | -143.10 | 47.5 | -0.203 | -84.48 | -58.62 |  |
| 29 | THE/USDT | 665 | -143.17 | 41.8 | -0.375 | -96.00 | -47.17 |  |
| 30 | ARB/USDT | 722 | -148.29 | 45.7 | -0.228 | -107.00 | -41.29 |  |
| 31 | ADA/USDT | 683 | -151.27 | 42.3 | -0.369 | -116.75 | -34.52 |  |
| 32 | TAO/USDT | 690 | -154.10 | 42.0 | -0.323 | -109.02 | -45.08 |  |
| 33 | QNT/USDT | 683 | -157.41 | 44.9 | -0.245 | -112.77 | -44.65 |  |
| 34 | SUI/USDT | 713 | -161.64 | 43.3 | -0.326 | -112.73 | -48.91 |  |
| 35 | ZEC/USDT | 696 | -162.87 | 43.8 | -0.275 | -89.74 | -73.13 |  |
| 36 | INJ/USDT | 710 | -168.23 | 43.8 | -0.284 | -114.30 | -53.93 |  |
| 37 | ONE/USDT | 801 | -169.91 | 44.6 | -0.218 | -105.14 | -64.77 |  |
| 38 | PORTAL/USDT | 683 | -170.64 | 43.0 | -0.353 | -125.73 | -44.90 |  |
| 39 | NIGHT/USDT | 751 | -170.67 | 45.0 | -0.264 | -129.95 | -40.72 |  |
| 40 | ENA/USDT | 765 | -171.09 | 45.9 | -0.238 | -116.89 | -54.21 |  |
| 41 | CFG/USDT | 832 | -174.72 | 45.7 | -0.237 | -125.20 | -49.52 |  |
| 42 | ONDO/USDT | 708 | -179.38 | 41.5 | -0.359 | -112.66 | -66.71 |  |
| 43 | WLFI/USDT | 559 | -182.44 | 33.5 | -0.599 | -95.55 | -86.89 |  |
| 44 | NEAR/USDT | 745 | -184.27 | 43.1 | -0.275 | -129.96 | -54.31 |  |
| 45 | HEI/USDT | 704 | -185.12 | 42.9 | -0.325 | -143.62 | -41.51 |  |
| 46 | FET/USDT | 794 | -185.79 | 41.6 | -0.306 | -143.98 | -41.81 |  |
| 47 | TRUMP/USDT | 719 | -190.27 | 41.9 | -0.348 | -133.70 | -56.57 |  |
| 48 | ZRO/USDT | 755 | -200.02 | 43.6 | -0.268 | -125.55 | -74.47 |  |
| 49 | PUMP/USDT | 790 | -203.01 | 43.7 | -0.25 | -127.81 | -75.19 |  |
| 50 | PEPE/USDT | 614 | -205.27 | 41.9 | -0.301 | -133.68 | -71.59 |  |

## Walk-forward check (choose on months 1–2, test on month 3)


**Live rules vs settings chosen on training data** (training months)

| Variant | Trades | Win % | Avg net % | Total net % | PF | Max DD % | Exp R | Avg hold h |
|---|---|---|---|---|---|---|---|---|
| Live rules | 20417 | 42.9 | -0.226 | -4604.28 | 0.57 | 4604.28 | -0.329 | 0.94 |
| Chosen settings | 4817 | 43.4 | -0.161 | -774.31 | 0.66 | 774.53 | -0.231 | 0.79 |

**Same settings on the unseen month** (unseen month)

| Variant | Trades | Win % | Avg net % | Total net % | PF | Max DD % | Exp R | Avg hold h |
|---|---|---|---|---|---|---|---|---|
| Live rules | 11256 | 44.4 | -0.189 | -2128.83 | 0.64 | 2139.72 | -0.275 | 0.96 |
| Chosen settings | 4107 | 45.6 | -0.13 | -534.37 | 0.73 | 560.53 | -0.179 | 0.8 |
Unseen-month expectancy is at least half of training.

## Recommended settings (each with training → unseen-month numbers)

- **No grade/side combination was profitable on the training months**, so no real-money setting is recommended. The 'chosen settings' below are the least-bad choices, shown only so you can see them.
- Grades to trade: A — A: train: 4471 trades, -963.0% total, -0.323 R/trade, win 43.9% → unseen: 2272 trades, -414.9% total, -0.278 R/trade, win 44.9%; B: train: 15946 trades, -3641.3% total, -0.331 R/trade, win 42.7% → unseen: 8984 trades, -1713.9% total, -0.274 R/trade, win 44.3%
- Sides: SHORT — LONG: train: 7880 trades, -2410.1% total, -0.334 R/trade, win 43.7% → unseen: 2155 trades, -739.8% total, -0.400 R/trade, win 40.3%; SHORT: train: 12537 trades, -2194.2% total, -0.326 R/trade, win 42.4% → unseen: 9101 trades, -1389.1% total, -0.245 R/trade, win 45.4%
- Max hold A-GRADE: 4 h — train: 4488 trades, -967.7% total, -0.325 R/trade, win 43.7% → unseen: 2284 trades, -388.8% total, -0.274 R/trade, win 45.0%
- Max hold B-GRADE: 1 h — train: 20836 trades, -4599.9% total, -0.326 R/trade, win 39.8% → unseen: 11812 trades, -2193.6% total, -0.272 R/trade, win 42.3%
- Fee floor MIN_TP1_FEE_MULT: 5 — train: 13200 trades, -3103.7% total, -0.273 R/trade, win 43.6% → unseen: 8258 trades, -1510.6% total, -0.216 R/trade, win 45.3%
- Exit style: (b) full exit at TP1 — train: 22650 trades, -4700.3% total, -0.305 R/trade, win 42.6% → unseen: 12560 trades, -2145.6% total, -0.251 R/trade, win 44.1%
- BTC-regime filter: ON — on: train: 17579 trades, -3885.6% total, -0.328 R/trade, win 42.9% → unseen: 10106 trades, -1920.2% total, -0.280 R/trade, win 44.2%; off: train: 20417 trades, -4604.3% total, -0.329 R/trade, win 42.9% → unseen: 11256 trades, -2128.8% total, -0.275 R/trade, win 44.4%
- USE_DERIV_SCORE: ON — on: train: 18193 trades, -3940.8% total, -0.322 R/trade, win 43.1% → unseen: 9998 trades, -1950.4% total, -0.283 R/trade, win 44.2%; off (same coins): train: 18212 trades, -4051.9% total, -0.327 R/trade, win 43.0% → unseen: 10015 trades, -1937.6% total, -0.282 R/trade, win 44.1%
- Coins to drop (≥10 training trades and losing on training): none — almost every coin lost money in training, so dropping them would leave too few to trade; no coin filter applied.
- **All of the above combined:** train: 4817 trades, -774.3% total, -0.231 R/trade, win 43.4% → unseen: 4107 trades, -534.4% total, -0.179 R/trade, win 45.6%
- Variants positive on the unseen month: none (with ≥30 unseen trades: none).

## Known biases (read before trusting any number)

- **Survivorship bias:** coins are today's top-50 by volume; coins that crashed out of the list during the 3 months are missing, and coins that pumped into it are over-represented.
- **Live filters applied:** coin exclusions (stablecoins, tokenized stocks, gold), the stable-like/peg filter and the fee floor (3× by default). The order-book penalty can't do anything here because there's no order-book history.
- **No order book:** the 15 order-book points are always 0, so fewer setups reach the live grade lines than live (see the what-if).
- **No AI review:** live alerts the AI rejects are included here.
- **Slippage/fills:** a flat 0.05% adverse slippage on entry; stops and targets assumed filled exactly at their price (real stops can slip further in fast moves). Fees: Binance standard, no BNB discount.
- **Candle resolution:** trades are checked on 5m candles; when one candle touches both the stop and a target the stop is assumed first (pessimistic, same as live).
- **Derivatives data:** funding from Hyperliquid (OKX if missing); open interest from Binance futures metrics (the live bot uses Hyperliquid OI — same idea, different exchange).
- **One period:** 3 months of one market regime; 1 unseen month is a small sample.

### Derivatives coverage per coin

| Coin | Funding source | OI source | Funding cov. | OI cov. |
|---|---|---|---|---|
| BTC/USDT | Hyperliquid | Binance futures metrics (BTCUSDT) | 100% | 100% |
| ETH/USDT | Hyperliquid | Binance futures metrics (ETHUSDT) | 100% | 100% |
| ZEC/USDT | Hyperliquid | Binance futures metrics (ZECUSDT) | 100% | 100% |
| SOL/USDT | Hyperliquid | Binance futures metrics (SOLUSDT) | 100% | 100% |
| XRP/USDT | Hyperliquid | Binance futures metrics (XRPUSDT) | 100% | 100% |
| BNB/USDT | Hyperliquid | Binance futures metrics (BNBUSDT) | 100% | 100% |
| NEAR/USDT | Hyperliquid | Binance futures metrics (NEARUSDT) | 100% | 100% |
| PORTAL/USDT | none | Binance futures metrics (PORTALUSDT) | 0% | 100% |
| HYPE/USDT | Hyperliquid | Binance futures metrics (HYPEUSDT) | 100% | 100% |
| UNI/USDT | Hyperliquid | Binance futures metrics (UNIUSDT) | 100% | 100% |
| PEPE/USDT | Hyperliquid | Binance futures metrics (1000PEPEUSDT) | 100% | 100% |
| TRX/USDT | Hyperliquid | Binance futures metrics (TRXUSDT) | 100% | 100% |
| DOGE/USDT | Hyperliquid | Binance futures metrics (DOGEUSDT) | 100% | 100% |
| HAEDAL/USDT | none | Binance futures metrics (HAEDALUSDT) | 0% | 100% |
| SUI/USDT | Hyperliquid | Binance futures metrics (SUIUSDT) | 100% | 100% |
| LINK/USDT | Hyperliquid | Binance futures metrics (LINKUSDT) | 100% | 100% |
| MMT/USDT | OKX | Binance futures metrics (MMTUSDT) | 100% | 100% |
| ENA/USDT | Hyperliquid | Binance futures metrics (ENAUSDT) | 100% | 100% |
| LTC/USDT | Hyperliquid | Binance futures metrics (LTCUSDT) | 100% | 100% |
| ADA/USDT | Hyperliquid | Binance futures metrics (ADAUSDT) | 100% | 100% |
| INIT/USDT | Hyperliquid | Binance futures metrics (INITUSDT) | 100% | 100% |
| FET/USDT | Hyperliquid | Binance futures metrics (FETUSDT) | 100% | 100% |
| BLUR/USDT | Hyperliquid | Binance futures metrics (BLURUSDT) | 100% | 100% |
| WLFI/USDT | Hyperliquid | Binance futures metrics (WLFIUSDT) | 100% | 100% |
| TAO/USDT | Hyperliquid | Binance futures metrics (TAOUSDT) | 100% | 100% |
| WLD/USDT | Hyperliquid | Binance futures metrics (WLDUSDT) | 100% | 100% |
| PUMP/USDT | Hyperliquid | Binance futures metrics (PUMPUSDT) | 100% | 100% |
| AAVE/USDT | Hyperliquid | Binance futures metrics (AAVEUSDT) | 100% | 100% |
| INJ/USDT | Hyperliquid | Binance futures metrics (INJUSDT) | 100% | 100% |
| BCH/USDT | Hyperliquid | Binance futures metrics (BCHUSDT) | 100% | 100% |
| ZRO/USDT | Hyperliquid | Binance futures metrics (ZROUSDT) | 100% | 100% |
| AVAX/USDT | Hyperliquid | Binance futures metrics (AVAXUSDT) | 100% | 100% |
| CFG/USDT | none | Binance futures metrics (CFGUSDT) | 0% | 100% |
| HEI/USDT | none | Binance futures metrics (HEIUSDT) | 0% | 100% |
| XLM/USDT | Hyperliquid | Binance futures metrics (XLMUSDT) | 100% | 100% |
| SSV/USDT | OKX | Binance futures metrics (SSVUSDT) | 100% | 100% |
| LISTA/USDT | Hyperliquid | Binance futures metrics (LISTAUSDT) | 100% | 100% |
| GRAM/USDT | Hyperliquid | Binance futures metrics (GRAMUSDT) | 100% | 100% |
| SEI/USDT | Hyperliquid | Binance futures metrics (SEIUSDT) | 100% | 100% |
| SKY/USDT | Hyperliquid | Binance futures metrics (SKYUSDT) | 100% | 100% |
| HBAR/USDT | Hyperliquid | Binance futures metrics (HBARUSDT) | 100% | 100% |
| NIGHT/USDT | OKX | Binance futures metrics (NIGHTUSDT) | 100% | 100% |
| ONE/USDT | OKX | Binance futures metrics (ONEUSDT) | 100% | 100% |
| QNT/USDT | OKX | Binance futures metrics (QNTUSDT) | 100% | 100% |
| BIGTIME/USDT | Hyperliquid | Binance futures metrics (BIGTIMEUSDT) | 100% | 100% |
| ONDO/USDT | Hyperliquid | Binance futures metrics (ONDOUSDT) | 100% | 100% |
| FIL/USDT | Hyperliquid | Binance futures metrics (FILUSDT) | 100% | 100% |
| ARB/USDT | Hyperliquid | Binance futures metrics (ARBUSDT) | 100% | 100% |
| TRUMP/USDT | Hyperliquid | Binance futures metrics (TRUMPUSDT) | 100% | 100% |
| THE/USDT | none | Binance futures metrics (THEUSDT) | 0% | 100% |

## Appendix — hand-checked trades and signals

Recomputed with separate plain code straight from the cached raw candles.

| Case | Coin | Dir | Signal (UTC) | Entry candle open | Entry bt / hand | Exit bt / hand | Reason bt / hand | Net % bt / hand | Match |
|---|---|---|---|---|---|---|---|---|---|
| STOP LOSS | BTC/USDT | LONG B | 2026-09-03 15:55 | 81270.63 | 81311.265 / 81311.265 | 80846.264 / 80846.264 | STOP LOSS / STOP LOSS | -0.7719 / -0.7719 | ✅ |
| TP1 → TP2 | BTC/USDT | SHORT B | 2026-09-01 16:10 | 77802 | 77763.099 / 77763.099 | 77281.283 / 77281.283 | TP2 HIT / TP2 HIT | +0.3819 / +0.3819 | ✅ |
| TP1 → breakeven | BTC/USDT | SHORT A | 2026-09-01 13:05 | 77850.43 | 77811.505 / 77811.505 | 77811.505 / 77811.505 | BREAKEVEN after TP1 / BREAKEVEN after TP1 | +0.0533 / +0.0533 | ✅ |
| SHORT | BTC/USDT | SHORT B | 2026-08-28 18:25 | 77525 | 77486.238 / 77486.238 | 77457.25 / 77457.25 | TIME EXIT / TIME EXIT | -0.0626 / -0.0626 | ✅ |
| TIME EXIT | BTC/USDT | SHORT B | 2026-09-09 16:00 | 78596.7 | 78557.402 / 78557.402 | 78234.94 / 78234.94 | TIME EXIT / TIME EXIT | +0.3105 / +0.3105 | ✅ |

| Signal | Coin | Signal time | Last 5m candle | Last 1h candle | RSI5 bt/hand | RSI1h bt/hand | ATR bt/hand | 1h close vs EMA21/EMA50 (hand) |
|---|---|---|---|---|---|---|---|---|
| A-GRADE | BTC/USDT | 2026-09-01 13:05 | 2026-09-01 13:00 | 2026-09-01 12:00 | 40.3/40.3 | 42.2/42.2 | 119.291/119.291 | 78004 vs 78390.1/78409.4 |
| B-GRADE | BTC/USDT | 2026-08-28 18:25 | 2026-08-28 18:20 | 2026-08-28 17:00 | 34.7/34.7 | 29.9/29.9 | 181.104/181.104 | 77904 vs 79248.9/79316.8 |

Run time 43.1 s · code hash ac8794cf02db · generated 2026-11-27T22:18:43+00:00
