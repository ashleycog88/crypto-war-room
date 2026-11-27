"""Market data, scoring, grades, coin universe, labels (JRN-1..9, 48, 49, 51)."""
import math

import numpy as np
import pandas as pd
import pytest

import war_room_bot as wr
from fakes import FakeSpot, install_scores, make_bot, mk_rows, TGRecorder

NOW_MS = 1_800_000_000_000 // 3_600_000 * 3_600_000 + 1_234_567


def row(**kw):
    base = {"close": 100, "ema9": 100, "ema21": 100, "ema50": 100, "vwap": 100, "rsi": 50, "vol_r": 1.0, "ts": 0}
    base.update(kw)
    return base


def test_jrn1_orderbook_imbalance_and_limit20(cfg, tg):
    ob = {"bids": [[100 - i, 15.0] for i in range(20)], "asks": [[101 + i, 5.0] for i in range(20)]}
    assert wr.ob_imbalance(ob) == pytest.approx(0.5)
    ls, ss, rl, rs = wr.score_sides(row(), row(), 0.5)
    assert ls == 15 and "Bid Wall +50%" in rl
    spot = FakeSpot(pairs=["BTC/USDT"])
    bot = make_bot(cfg, spot)
    bot._fetch_one(spot, "BTC/USDT", int(spot.now * 1000))
    assert ("ob", "BTC/USDT", 20) in spot.calls


def test_jrn2_orderbook_failure_logged_scan_continues(cfg, tg, capsys):
    spot = FakeSpot(pairs=["BTC/USDT", "ETH/USDT"])
    spot.ob_fail.add("ETH/USDT")
    bot = make_bot(cfg, spot)
    res = bot.fetch_market(spot, spot.pairs, int(spot.now * 1000))
    assert res["ETH/USDT"]["ob_imb"] == 0.0 and "df5m" in res["ETH/USDT"]
    assert "df5m" in res["BTC/USDT"]
    assert "ETH/USDT: order book error" in capsys.readouterr().out


def test_jrn3_closed_5m_candle_used_not_forming():
    rows = mk_rows(320, "5m", NOW_MS, vol=100.0)
    v_closed = 1900 * 2 / 18  # makes vol / rolling-20 mean exactly 2.0
    rows[-2][5] = v_closed
    rows[-1][5] = 0.001  # forming candle, tiny volume
    rows[-1][4] = 50.0  # forming candle crash must not affect RSI/EMA
    closed, reason = wr.validate_candles(rows, "5m", NOW_MS)
    assert reason is None and closed[-1][0] == rows[-2][0]
    df = wr.add_indicators(wr.to_df(closed))
    assert df.iloc[-1]["vol_r"] == pytest.approx(2.0, abs=0.01)
    assert df.iloc[-1]["rsi"] > 40 and df.iloc[-1]["ema9"] == pytest.approx(100, abs=0.01)


def test_jrn4_closed_1h_candle_scores_trend():
    rows = mk_rows(260, "1h", NOW_MS, start=100, step=0.2)
    rows[-1][4] = 10.0  # forming candle not bullish
    closed, reason = wr.validate_candles(rows, "1h", NOW_MS)
    df1 = wr.add_indicators(wr.to_df(closed))
    r1 = df1.iloc[-1]
    assert r1["close"] > r1["ema21"] > r1["ema50"]
    ls, ss, rl, rs = wr.score_sides(row(), r1, 0)
    assert "1H Bull EMA" in rl


def test_jrn5_candle_limits(cfg, tg):
    assert wr.LIMIT_5M >= 301 and wr.LIMIT_1H >= 251 and wr.MIN_CLOSED >= 200
    spot = FakeSpot(pairs=["BTC/USDT"])
    bot = make_bot(cfg, spot)
    out = bot._fetch_one(spot, "BTC/USDT", int(spot.now * 1000))
    lims = {c[2]: c[3] for c in spot.calls if c[0] == "ohlcv"}
    assert lims["5m"] >= 300 and lims["1h"] >= 250
    assert len(out["df5m"]) >= 300 and len(out["df1h"]) >= 250


def test_jrn6_bad_candles_skipped_with_reason(cfg, tg, capsys):
    short = mk_rows(150, "5m", NOW_MS)
    assert "insufficient candles" in wr.validate_candles(short, "5m", NOW_MS)[1]
    stale = mk_rows(320, "5m", NOW_MS - 30 * 60_000)
    assert "stale" in wr.validate_candles(stale, "5m", NOW_MS)[1]
    nan = mk_rows(320, "5m", NOW_MS)
    nan[-5][4] = float("nan")
    assert "bad data" in wr.validate_candles(nan, "5m", NOW_MS)[1]
    dup = mk_rows(320, "5m", NOW_MS)
    dup[-3][0] = dup[-4][0]
    assert "duplicate" in wr.validate_candles(dup, "5m", NOW_MS)[1]
    zero = mk_rows(320, "5m", NOW_MS)
    zero[10][3] = 0
    assert "bad data" in wr.validate_candles(zero, "5m", NOW_MS)[1]
    spot = FakeSpot(pairs=["BTC/USDT", "ETH/USDT", "SOL/USDT"])
    spot.override[("ETH/USDT", "5m")] = lambda now: mk_rows(150, "5m", int(now * 1000))
    spot.override[("SOL/USDT", "5m")] = lambda now: mk_rows(320, "5m", int(now * 1000) - 1_800_000)
    bot = make_bot(cfg, spot)
    res = bot.fetch_market(spot, spot.pairs, int(spot.now * 1000))
    out = capsys.readouterr().out
    assert "ETH/USDT: skipped this scan: 5m insufficient candles" in out
    assert "SOL/USDT: skipped this scan: 5m stale candles" in out
    assert "df5m" in res["BTC/USDT"]


@pytest.mark.parametrize("ls,ss,grade,first", [(85, 10, "A", "A-GRADE 85/100"), (10, 70, "B", "B-GRADE 70/100"),
                                               (80, 0, "A", "A-GRADE 80/100"), (65, 0, "B", "B-GRADE 65/100")])
def test_jrn7_8_9_grades_in_first_line(cfg, tg, monkeypatch, ls, ss, grade, first):
    spot = FakeSpot(pairs=["BTC/USDT"])
    install_scores(monkeypatch, spot, {"BTC/USDT": (ls, ss)})
    bot = make_bot(cfg, spot)
    bot.cycle(now=spot.now)
    alerts = tg.texts("-GRADE")
    assert len(alerts) == 1
    assert first in alerts[0].split("\n")[0]
    assert bot.state["active_trades"][0]["grade"] == grade


def test_jrn9_60_is_near_miss_not_alert(cfg, tg, monkeypatch):
    spot = FakeSpot(pairs=["BTC/USDT"])
    install_scores(monkeypatch, spot, {"BTC/USDT": (60, 20)})
    bot = make_bot(cfg, spot)
    bot.state["last_heartbeat"] = spot.now
    bot.cycle(now=spot.now)
    assert tg.texts("-GRADE") == []
    assert bot.state["near_miss"]["BTC/USDT"][:2] == ["LONG", 60]
    assert wr.grade_for(79) == "B" and wr.grade_for(64) is None


def test_fallback_exchange_ids_exist_in_ccxt():
    import ccxt
    assert all(hasattr(ccxt, x) for x in wr.FALLBACK_EXCHANGES)
    assert [n for n, _ in wr.default_spot_factories()] == ["BINANCE", "KUCOIN", "OKX", "KRAKEN", "GATE", "BYBIT"]


def test_tie_sends_nothing(cfg, tg, monkeypatch):
    spot = FakeSpot(pairs=["BTC/USDT"])
    install_scores(monkeypatch, spot, {"BTC/USDT": (70, 70)})
    bot = make_bot(cfg, spot)
    bot.cycle(now=spot.now)
    assert tg.texts("-GRADE") == []


def test_levels_from_atr():
    sl, tp1, tp2 = wr.make_levels("LONG", 100, 1)
    assert (sl, tp1, tp2) == (98.2, 102.0, 103.6)
    assert wr.make_levels("SHORT", 100, 1) == (101.8, 98.0, 96.4)
    assert wr.make_levels("LONG", 0.00001234, 0.0000001)[0] == pytest.approx(0.00001216)


def test_jrn48_coin_list_filtering():
    tickers = [{"symbol": s, "quoteVolume": str(v)} for s, v in [
        ("BTCUSDT", 9e9), ("ETHUSDT", 8e9), ("USDCUSDT", 7e9), ("FDUSDUSDT", 6e9), ("EURUSDT", 5e9),
        ("BTCUPUSDT", 4e9), ("WBTCUSDT", 3e9), ("ETHBTC", 9e10), ("JUPUSDT", 1), ("NVDABUSDT", 2e9),
        ("XAUTUSDT", 2e9), ("UUSDT", 2e9), ("ZECUSDT", 1.5e3)]]
    tickers += [{"symbol": f"C{i:02d}USDT", "quoteVolume": str(1e9 - i * 1e6)} for i in range(60)]
    coins = wr.build_coin_list(tickers, 50, exclude_symbols={"NVDABUSDT"}, extra_bases={"C05"})
    assert len(coins) == 50
    assert coins[:3] == ["BTC/USDT", "ETH/USDT", "C00/USDT"]
    for bad in ["USDC/USDT", "FDUSD/USDT", "EUR/USDT", "BTCUP/USDT", "WBTC/USDT", "NVDAB/USDT", "XAUT/USDT",
                "U/USDT", "C05/USDT"]:
        assert bad not in coins
    assert not wr.is_excluded_base("JUP")


def test_jrn49_coin_list_fallbacks(cfg, tg):
    class BrokenBinance(FakeSpot):
        def tickers_24h(self):
            raise ConnectionError("ticker down")
    spot = BrokenBinance(pairs=["BTC/USDT"])
    bot = make_bot(cfg, spot)
    bot.state["coin_list"] = ["BTC/USDT", "PEPE/USDT"]
    bot.refresh_coins()
    assert bot.coins == ["BTC/USDT", "PEPE/USDT"] and bot.coin_source == "saved list"
    bot.state["coin_list"] = []
    bot.refresh_coins()
    assert bot.coin_source == "built-in list" and all(c in bot.coins for c in wr.ORIGINAL_8)
    assert len(wr.BUILTIN_COINS) == 50 and len(set(wr.BUILTIN_COINS)) == 50


def test_top_n_env(monkeypatch, tmp_path):
    monkeypatch.setenv("TOP_N_COINS", "20")
    assert wr.Config().top_n == 20


def test_jrn51_market_labels_and_no_perp(cfg, tg, monkeypatch):
    spot = FakeSpot(pairs=["BTC/USDT", "ETH/USDT", "SOL/USDT"])
    install_scores(monkeypatch, spot, {"BTC/USDT": (85, 0), "ETH/USDT": (0, 85), "SOL/USDT": (0, 85)})
    srcs = [("S1", lambda s, c: {"BTC": {"funding8h": 0.0001, "oi": 1.0}, "ETH": {"funding8h": 0.0001, "oi": 1.0}}),
            ("S2", lambda s, c: {"BTC": {"funding8h": 0.0001, "oi": 1.0}, "ETH": {"funding8h": 0.0001, "oi": 2.0}})]
    bot = make_bot(cfg, spot, deriv_sources=srcs)
    bot.cycle(now=spot.now)
    alerts = tg.texts("-GRADE")
    long_ = [a for a in alerts if "BTC/USDT LONG" in a]
    short = [a for a in alerts if "ETH/USDT SHORT" in a]
    assert long_ and "Spot or Futures" in long_[0]
    assert short and "Futures only" in short[0]
    assert not [a for a in alerts if "SOL/USDT" in a]


def test_short_allowed_when_perp_unknown(cfg, tg, monkeypatch):
    spot = FakeSpot(pairs=["SOL/USDT"])
    install_scores(monkeypatch, spot, {"SOL/USDT": (0, 85)})
    bot = make_bot(cfg, spot)
    bot.cycle(now=spot.now)
    a = tg.texts("-GRADE")
    assert len(a) == 1 and "Futures only (perp listing unverified)" in a[0]


def test_deriv_adjustment_bounds_and_switch():
    assert wr.deriv_adjustment("LONG", -0.0001, 3.0, 1.0, "up") == 10
    assert wr.deriv_adjustment("LONG", 0.0001, None, None, "down") == -10
    assert wr.deriv_adjustment("SHORT", 0.0003, 2.5, -0.5, "down") == 10
    assert wr.deriv_adjustment("LONG", None, None, None, "n/a") == 0
    assert wr.norm_base("1000PEPE") == "PEPE" and wr.norm_base("XBT") == "BTC" and wr.norm_base("1INCH") == "1INCH"
