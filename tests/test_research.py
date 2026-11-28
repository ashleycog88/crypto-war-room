"""Phase 3 research engine checks (JRN-81..91 core rules) on synthetic candles."""
import numpy as np

import research as rs

H = rs.H


def bars(prices, t0=rs.START, tf=H):
    rows = []
    for k, (o, h, l, c) in enumerate(prices):
        rows.append([t0 + k * tf, o, h, l, c, 1.0])
    return np.array(rows, float)


def test_stop_first_and_fill_at_stop():
    a = bars([(100, 101, 99, 100), (100, 110, 90, 100)])
    j, px, g, why = rs.run_trade(a, 1, "LONG", stop=95, tgt=105)
    assert (j, px, why) == (1, 95, "stop") and round(g, 6) == -5.0


def test_partial_tp1_then_breakeven():
    a = bars([(100, 100, 100, 100), (100, 103, 99.5, 102), (102, 102.5, 99, 100)])
    j, px, g, why = rs.run_trade(a, 1, "LONG", stop=98, partial=(102, 106))
    assert why == "stop" and px == 100 and round(g, 6) == 1.0  # half +2% then rest at breakeven


def test_costs_models_and_stress():
    t = {"side": "LONG", "gross": 2.0, "fund": 0.03}
    assert round(rs.net(t, "F"), 6) == round(2 - 0.1 - 0.1 - 0.03, 6)
    assert round(rs.net(t, "S"), 6) == round(2 - 0.2 - 0.1, 6)  # spot buy: no funding
    s = dict(t, side="SHORT")
    assert round(rs.net(s, "S"), 6) == round(2 - 0.1 - 0.1 + 0.03, 6)  # short receives positive funding
    assert round(rs.net(t, "F", stress=True), 6) == round(2 - 0.15 - 0.3 - 0.03, 6)


def test_resample_only_complete_groups():
    a = bars([(1, 2, 0.5, 1.5)] * 9)
    r = rs.resample(a, 4 * H)
    assert len(r) == 2 and r[0, 2] == 2 and r[0, 0] % (4 * H) == 0


def test_slow_index_uses_closed_candles_only():
    fast = bars([(1, 1, 1, 1)] * 8)
    slow = rs.resample(fast, 4 * H)
    k = rs.slow_index(fast, slow, H, 4 * H)
    assert list(k) == [-1, -1, -1, 0, 0, 0, 0, 1]  # 4h candle usable only after its last 1h candle closed


def test_verdict_rules():
    good = {"n": 60, "expR": 0.2, "pf": 1.5, "ret_pct": 5}
    assert rs.verdict(good, good, good, 97, good, 50) == ("PASSED", [])
    v, fails = rs.verdict(dict(good, pf=1.1), good, dict(good, ret_pct=-1), 90, dict(good, expR=-0.1), 50)
    assert v == "REJECTED" and len(fails) == 4


def test_grids_are_small_and_listed():
    assert all(len(g) <= 12 for g in rs.GRID.values())
    assert sum(len(g) for g in rs.GRID.values()) == 8 + 6 + 4 + 4 + 4


def test_channel_exit_uses_prior_candles():
    a = bars([(100, 100, 100, 100)] * 3 + [(100, 101, 97, 98)] + [(98, 98, 98, 98)])
    lo = np.array([np.nan, 100, 100, 100, 97])
    hi = np.array([np.nan, 100, 100, 100, 101])
    j, px, g, why = rs.run_trade(a, 1, "LONG", stop=90, chan=(lo, hi))
    assert why == "channel" and j == 4 and px == 98  # close 98 < prior low 100 → exit next open


def test_s5_period_return_is_open_to_open():
    class C:
        def cum_funding(self, tf):
            return np.zeros(len(self.bars[tf]))
    days = 120
    t0 = rs.START - 40 * 24 * H
    coins = []
    for b, g in (("BTC", 1.01), ("AAA", 1.02), ("BBB", 0.99)):
        c = C()
        c.base = b
        px = 100 * g ** np.arange(days + 1)
        c.bars = {"1d": np.array([[t0 + k * 24 * H, px[k], px[k], px[k], px[k], 1] for k in range(days)], float)}
        coins.append(c)
    ps = rs.s5(coins, {"k": 1, "reb": "daily"})
    held = [x for x in ps if x["in"]][5]
    assert held["coins"] == ["AAA"] and held["t_end"] - held["t"] == 24 * H
    assert abs(held["net"] - 2.0) < 1e-6  # +2% a day, no turnover once held


def test_s1_signals_have_no_lookahead_by_truncation(monkeypatch):
    rng = np.random.default_rng(3)
    n = 24 * 400
    t0 = rs.START - 60 * 24 * rs.H
    px = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    rows = np.column_stack([t0 + np.arange(n) * rs.H, px, px * 1.004, px * 0.996, px, np.ones(n)])
    empty = (np.array([], np.int64), np.array([]), np.array([]))
    full = rs.Coin("BTC", rows, empty, "none")
    got = {}
    monkeypatch.setattr(rs, "walk", lambda L, S, a, make: got.setdefault("sig", (L, S)) and [])
    p = {"n": 20, "exit": "channel", "btc": True}
    rs.s1(full, p, full.bars["4h"])
    L, S = got.pop("sig")
    for i in list(np.nonzero(L | S)[0][:15]):
        cut = rs.Coin("BTC", rows[rows[:, 0] < full.bars["4h"][i, 0] + rs.TF["4h"]], empty, "none")
        rs.s1(cut, p, cut.bars["4h"])
        L2, S2 = got.pop("sig")
        assert L2[i] == L[i] and S2[i] == S[i]
