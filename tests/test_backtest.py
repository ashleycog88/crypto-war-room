"""Phase 2 backtest: parity with the live code, no look-ahead, exit styles, metrics, simulation rules (JRN-54..67)."""
import inspect
import time

import numpy as np
import pytest

import backtest as bt
import war_room_bot as wr
from fakes import FakeSpot, make_bot

H, M = 3_600_000, 300_000
T0 = 1_790_000_000_000 // H * H


def walk(n, step_ms, start, seed, vol=0.004):
    rng = np.random.default_rng(seed)
    c = start * np.exp(np.cumsum(rng.normal(0, vol, n)))
    o = np.concatenate([[start], c[:-1]])
    hi = np.maximum(o, c) * (1 + rng.uniform(0, vol, n))
    lo = np.minimum(o, c) * (1 - rng.uniform(0, vol, n))
    v = rng.uniform(50, 150, n)
    ts = T0 + np.arange(n) * step_ms
    return np.column_stack([ts, o, hi, lo, c, v]).astype(float)


@pytest.fixture(scope="module")
def series():
    r1 = walk(340, H, 100.0, 1, 0.01)
    start5 = T0 + 270 * H
    r5 = walk(700, M, 100.0, 2)
    r5[:, 0] = start5 + np.arange(700) * M
    return r5, r1


def run_eval(r5, r1, start_ms, end_ms):
    e = np.array([], dtype=float)
    _, out = bt.eval_coin(("X/USDT", r5, r1, start_ms, end_ms, e, e, e, e, e, []))
    return out


def test_jrn56_scores_identical_to_live_path(cfg, tg, series):
    r5, r1 = series
    start, end = int(r5[320, 0]), int(r5[-1, 0])
    out = run_eval(r5, r1, start, end)
    assert len(out["t"]) > 300
    spot = FakeSpot(pairs=["X/USDT"])
    bot = make_bot(cfg, spot)
    for n in range(0, len(out["t"]), 25):
        t = out["t"][n]
        spot.override[("X/USDT", "5m")] = lambda now, t=t: r5[r5[:, 0] <= t][-wr.LIMIT_5M:].tolist()
        spot.override[("X/USDT", "1h")] = lambda now, t=t: r1[r1[:, 0] <= t][-wr.LIMIT_1H:].tolist()
        bot.cache.clear()
        live = bot._fetch_one(spot, "X/USDT", t + 20_000)  # live bot scanning 20 s after the candle closed
        assert "err" not in live, live.get("err")
        res = wr.evaluate_pair(live["df5m"], live["df1h"], 0.0, float(r5[out["i"][n], 4]))
        assert (bt.DIR_CODE[res["dir"]], res["score"]) == (out["d_off"][n], out["s_off"][n])
        assert res["atr"] == out["atr"][n] and res["candle_ts"] == out["last5"][n]


def test_jrn56_no_copied_logic():
    src = inspect.getsource(bt)
    for name in ("add_indicators", "score_sides", "evaluate_pair", "resolve_trade", "net_pnl", "make_levels",
                 "validate_candles", "grade_for", "btc_regime"):
        assert f"def {name}(" not in src
    assert bt.evaluate_pair is wr.evaluate_pair and bt.resolve_trade is wr.resolve_trade


def test_jrn55_no_lookahead(series):
    r5, r1 = series
    out = run_eval(r5, r1, int(r5[320, 0]), int(r5[-1, 0]))
    for t, l5, l1, i in zip(out["t"], out["last5"], out["last1h"], out["i"]):
        assert l5 + M <= t and l1 + H <= t and r5[i + 1, 0] == t
    ok = {"signal_time": 10 * H, "last5": 10 * H - M, "last1h": 9 * H, "entry_time": 10 * H, "f_time": None,
          "oi_time": 10 * H}
    assert bt.lookahead_violations([ok]) == 0
    assert bt.lookahead_violations([dict(ok, last5=10 * H)]) == 1
    assert bt.lookahead_violations([dict(ok, last1h=9 * H + 1)]) == 1
    assert bt.lookahead_violations([dict(ok, oi_time=10 * H + 1)]) == 1


def candles(*bars, t0=T0):
    return [[float(t0 + k * M), 100.0, h, l, c, 1.0] for k, (h, l, c) in enumerate(bars)]


def tr(d="LONG"):
    e = 100.0
    s = 1 if d == "LONG" else -1
    return {"symbol": "X/USDT", "dir": d, "grade": "A", "entry": e, "sl": e - s * 1.8, "tp1": e + s * 2.0,
            "tp2": e + s * 3.6, "entry_time": T0 / 1000, "tp1_hit": False, "checked_until": 0}


def test_exit_styles():
    ev, t = bt.resolve_live_style(tr(), candles((100.5, 99.5, 100), (100.2, 98.0, 98.5)), 0, 24)
    assert ev["reason"] == "STOP LOSS" and round(ev["net"], 2) == -2.0 and t == T0 + 2 * M
    ev, _ = bt.resolve_variant(tr(), candles((102.1, 100.2, 102)), 0, 24, "b", 1.0)
    assert ev["reason"] == "TP1 FULL" and round(ev["net"], 2) == 1.8
    bars = [(102.1, 100.2, 102)] + [(101.5, 100.5, 101)] * 11
    ev, t = bt.resolve_variant(tr(), candles(*bars), 0, 1, "d", 1.0)
    assert ev["reason"] == "TIME EXIT" and round(ev["net"], 2) == 1.3 and t == T0 + H
    ev, _ = bt.resolve_variant(tr(), candles((102.1, 100.2, 102), (104.0, 101.5, 103.5), (103.5, 102.9, 103)),
                               0, 24, "c", 1.0)
    assert ev["reason"] == "TRAIL STOP" and round(ev["net"], 2) == 2.3
    ev, _ = bt.resolve_variant(tr(), candles((102.5, 98.0, 100)), 0, 24, "c", 1.0)
    assert ev["reason"] == "STOP LOSS"
    ev, _ = bt.resolve_variant(tr("SHORT"), candles((99.8, 97.9, 98), (100.1, 99.0, 100)), 0, 24, "d", 1.0)
    assert ev["reason"].startswith("BREAKEVEN") and round(ev["net"], 2) == 0.9


def test_metrics():
    ts = [{"net": n, "R": n / 2, "hold_h": 1.0, "exit_time": i} for i, n in enumerate([1.0, -2.0, 3.0])]
    m = bt.metrics(ts)
    assert m["total_net"] == 2.0 and m["pf"] == 2.0 and m["max_dd"] == 2.0 and m["win_rate"] == 66.7
    assert m["exp_r"] == pytest.approx(1 / 3, abs=1e-3) and m["note"] == "too few trades to trust"
    assert bt.metrics([])["trades"] == 0


def synth(n_bars=40, signals=(0, 1, 13), atr=1.0, peg=False):
    rows = candles(*[(100.3, 99.7, 100.0)] * n_bars)
    ev = {k: [] for k in ("i", "t", "d_off", "s_off", "d_on", "s_on", "adj", "atr", "last5", "last1h", "f8", "f_time",
                          "oic", "oi_time", "regime", "peg")}
    for i in signals:
        for k, v in (("i", i), ("t", int(rows[i][0] + M)), ("d_off", 1), ("s_off", 85), ("d_on", 1), ("s_on", 85),
                     ("adj", 0), ("atr", atr), ("last5", int(rows[i][0])), ("last1h", int(rows[i][0]) - H),
                     ("f8", None), ("f_time", None), ("oic", None), ("oi_time", None), ("regime", "flat"),
                     ("peg", peg)):
            ev[k].append(v)
    return ev, rows


def test_simulation_rules_cooldown_one_trade_fee_floor_peg_and_deriv_exclusion():
    ev, rows = synth()
    meta = {"_train_end": 0, "X/USDT": {"has_perp": True}}
    base = {"name": "t", "hold": {"A": 1, "B": 1}, "fee_mult": 3}
    trades, _ = bt.simulate(base, {"X/USDT": ev}, {"X/USDT": rows}, meta)
    assert len(trades) == 2  # bar 1 blocked (open trade); bar 13 free again 65 min after the first alert
    t0 = trades[0]
    assert t0["entry"] == pytest.approx(100.0 * 1.0005) and t0["reason"] == "TIME EXIT"
    assert trades[1]["signal_time"] - t0["signal_time"] >= wr.COOLDOWN_S * 1000
    ev2, rows2 = synth(atr=0.2)  # TP1 0.4% < 3 x 0.20% fees
    assert bt.simulate(base, {"X/USDT": ev2}, {"X/USDT": rows2}, meta)[0] == []
    assert len(bt.simulate(dict(base, fee_mult=0), {"X/USDT": ev2}, {"X/USDT": rows2}, meta)[0]) == 2
    ev3, rows3 = synth(peg=True)
    assert bt.simulate(base, {"X/USDT": ev3}, {"X/USDT": rows3}, meta)[0] == []
    assert bt.simulate(dict(base, coins=set()), {"X/USDT": ev}, {"X/USDT": rows}, meta)[0] == []


def test_series_at_never_looks_ahead():
    times, vals = np.array([1000.0, 2000.0]), [1, 2]
    assert bt.series_at(times, vals, 1999, 5000) == (1, 1000)
    assert bt.series_at(times, vals, 999, 5000) == (None, None)
    assert bt.series_at(times, vals, 9000, 5000) == (None, None)
