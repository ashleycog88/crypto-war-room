import numpy as np
import pandas as pd

import edge_data as ed
import edge_v2 as ev

DAY, H = ed.DAY, ed.H


def test_clean_drops_invalid_candles_without_inventing():
    a = np.array([[0, 10, 11, 9, 10.5, 1], [H, 10, 9, 11, 10, 1], [2 * H, 10, 12, 9, 11, 1], [2 * H, 10, 12, 9, 11, 1],
                  [3 * H + 5, 10, 12, 9, 11, 1], [4 * H, -1, 12, 9, 11, 1], [5 * H, 10, 12, 9, 11, np.nan]], float)
    c = ed.clean(a, H)
    assert c[:, 0].tolist() == [0, 2 * H]  # bad high/low, duplicate, off-grid, negative, NaN rows dropped


def test_segments_split_reused_ticker():
    t = np.concatenate([np.arange(100) * H, 100 * H + 40 * DAY + np.arange(50) * H])
    a = np.column_stack([t, np.ones((150, 5))])
    s = ed.segments(a, "LUNA")
    assert [n for n, _ in s] == ["LUNA", "LUNA#2"] and [len(x) for _, x in s] == [100, 50]


def test_point_in_time_sets_rank_trailing_volume_and_keep_delisted():
    t0 = ed.month_starts()[30]
    days = np.arange(t0 - 400 * DAY, t0 + 100 * DAY, DAY)
    qv = {"OLD": (days[days < t0], np.full((days < t0).sum(), 5.0), int(days[0])),  # delisted at t0
          "BIG": (days, np.full(len(days), 9.0), int(days[0])),
          "NEW": (days[days >= t0 - 10 * DAY], np.full((days >= t0 - 10 * DAY).sum(), 99.0), int(t0 - 10 * DAY))}
    sets = ed.build_sets(qv)
    assert sets[t0]["top50"] == ["BIG", "OLD"]  # NEW traded < 30 days: not selectable yet
    assert "OLD" not in sets[ed.month_starts()[31]]["top50"]  # gone once it stopped trading
    assert ed.pit_violations(sets, {"OLD": int(days[0]), "BIG": int(days[0]), "NEW": int(t0 - 10 * DAY)}) == 0


def test_walk_forward_windows():
    w = ev.windows()
    assert 9 <= len(w) <= 11 and w[-1][2] == ev.END
    for (ls, s, e), nxt in zip(w, w[1:] + [None]):
        assert ev.add_months(ls, 18) == s and s < e
        if nxt:
            assert nxt[1] == e  # unseen windows are back to back, never overlapping


def test_selection_sees_only_finished_trades():
    s = ev.bt.ms("2022-01-01")
    good = [{"entry_t": s - 10 * DAY, "exit_t": s - DAY, "gross": 1.0, "fund": 0, "side": "LONG"}] * 30
    late = [{"entry_t": s - 10 * DAY, "exit_t": s + DAY, "gross": 50.0, "fund": 0, "side": "LONG"}] * 30
    bad = [{"entry_t": s - 10 * DAY, "exit_t": s - DAY, "gross": -1.0, "fund": 0, "side": "LONG"}] * 30
    assert ev.pick_trades([bad + late, good], "F", s - 500 * DAY, s) == 1  # the +50% trades end after the window starts


def synth_g(n=400, seed=1):
    rng = np.random.default_rng(seed)
    C = 100 * np.exp(np.cumsum(rng.normal(0.001, 0.02, n)))
    O = np.concatenate([[C[0]], C[:-1]])
    Hh, Lw = np.maximum(O, C) * 1.01, np.minimum(O, C) * 0.99
    A = ev.rs.atr(np.column_stack([np.arange(n), O, Hh, Lw, C, np.ones(n)]), 14)
    R = np.full(n, np.nan)
    R[:-1] = O[1:] / O[:-1] - 1
    return {"G": np.arange(n) * 6 * H, "O": O[None], "H": Hh[None], "L": Lw[None], "C": C[None], "A": A[None],
            "R": R[None], "F": np.full((1, n), 0.01), "names": ["X"]}


def test_s9_positions_use_only_past_closes():
    g = synth_g()
    p = {"theta": 0.02, "L": 28}
    for side in ("LONG", "SHORT"):
        full, _ = ev.s9_path(g, 0, p, side, 60, 390)
        assert np.abs(full).sum() > 10  # the rule actually trades
        for k in (150, 250, 330):
            g2 = {x: (v.copy() if isinstance(v, np.ndarray) else v) for x, v in g.items()}
            for key in ("O", "C", "A", "R"):
                g2[key][0, k:] *= 1.7  # change the future from bar k on
            cut, _ = ev.s9_path(g2, 0, p, side, 60, 390)
            assert np.array_equal(full[:k - 60], cut[:k - 60])  # positions before bar k (decided at close k-1) unchanged


def test_s9_bar_net_costs_and_funding_signs():
    g = synth_g()
    pos = np.array([0, 1, 1, 0, -1, -1, 0], float)
    nb = ev.bar_net(g, 0, pos, 100)
    r = g["R"][0, 100:107] * 100
    want = pos * r - pos * 0.01 - np.abs(np.diff(np.concatenate([[0], pos]))) * 0.10
    assert np.allclose(nb, want)  # longs pay funding, shorts receive; 0.05% fee + 0.05% slippage per change


def bars5(n=6000, seed=2):
    rng = np.random.default_rng(seed)
    c = 50 * np.exp(np.cumsum(rng.normal(0, 0.002, n) * np.where(rng.random(n) < 0.01, 8, 1)))
    o = np.concatenate([[c[0]], c[:-1]])
    t = ev.D0 + 400 * DAY + np.arange(n) * 300_000
    return np.column_stack([t, o, np.maximum(o, c) * 1.001, np.minimum(o, c) * 0.999, c, np.ones(n)])


def test_s10_signal_no_lookahead_and_trade_rules():
    a = bars5()
    p = {"m": 2, "w": 288}
    s = ev.s10_signal(a, p)
    for k in (1500, 3000, 4500):
        b = a.copy()
        b[k:, 1:5] *= np.linspace(1, 3, len(a) - k)[:, None]
        assert np.array_equal(s[:k], ev.s10_signal(b, p)[:k])
    tr = ev.s10_coin_trades("X", a, np.zeros(len(a)), np.ones(len(a), bool), p)
    assert len(tr) >= 3
    for t, nxt in zip(tr, tr[1:] + [None]):
        assert t["exit_t"] - t["entry_t"] == 12 * H and t["entry_t"] == t["sig_close"]
        assert t["exit"] == a[np.searchsorted(a[:, 0], t["exit_t"]), 1]  # sold at that bar's open
        if nxt:
            assert nxt["entry_t"] >= t["exit_t"]  # one open trade per coin


def test_s11_carry_arithmetic_and_liquidation():
    t = np.arange(10) * H
    S = np.column_stack([t, np.full((10, 5), 100.0)])
    P = S.copy()
    S[9, 1], P[9, 1], P[0, 1] = 110.0, 110.2, 100.5
    c = {"name": "X", "t": t, "S": S, "P": P, "ft": t[1::8], "fr": np.full(2, 0.0045), "fn": np.full(2, 0.0045)}
    tr = ev.carry_trade(c, 0, 9)
    n1, liq = ev.carry_net(tr, 1)
    spot, perp = 10.0, -(110.2 / 100.5 - 1) * 100
    assert not liq and abs(n1 - (spot + perp + 0.9 - 0.30 - 0.20) / 2) < 1e-9  # 4 taker fills, capital = 2×
    P[4, 2], S[4, 4] = 151.0, 149.0  # perp spikes to +50%: a 2× short is liquidated in hour 4
    tr = ev.carry_trade(c, 0, 9)
    n2, liq2 = ev.carry_net(tr, 2)
    assert liq2 and tr["liq"][2][0] == 4 * H
    assert abs(n2 - ((149.0 / 100 - 1) * 100 - 50 + 0.45 - 0.5) / 1.5) < 1e-9  # margin lost, spot sold that hour
    assert not ev.carry_net(tr, 1)[1]


def test_s11_signal_uses_settled_rates_and_enters_next_hour():
    t = np.arange(0, 60 * DAY, H) + ev.bt.ms("2023-03-01")
    ft = t[::8]
    fn = np.where((ft >= t[0] + 10 * DAY) & (ft < t[0] + 20 * DAY), 0.0005, 0.00001)
    S = np.column_stack([t, np.full((len(t), 5), 10.0)])
    c = {"name": "X", "t": t, "S": S, "P": S.copy(), "ft": ft, "fr": fn, "fn": fn}

    class U:
        @staticmethod
        def members(kind, tt):
            return {"X"}
    tr = ev.s11_trades(c, {"entry": 0.02, "exit": 0.005}, U)
    assert len(tr) == 1
    k = int(np.searchsorted(ft, t[0] + 10 * DAY)) + 1  # 2nd high settlement: (0.05+0.05+0.001)/3 > 0.02%
    assert tr[0]["sig_t"] == ft[k] and tr[0]["entry_t"] == ft[k] + H
    assert tr[0]["exit_t"] > t[0] + 20 * DAY and tr[0]["fund"] > 0


def test_s12_weights_use_only_past_weeks():
    wk0 = ev.fx.ANCHOR + DAY + 200 * 7 * DAY
    weeks = [wk0 + i * 7 * DAY for i in range(30)]
    rng = np.random.default_rng(3)
    streams = {k: {w: float(rng.normal(0, s)) for w in weeks} for k, s in (("A", 1), ("B", 3))}
    b1 = ev.blend(streams, weeks[0] - DAY, weeks[-1] + 7 * DAY)
    streams["B"][weeks[20]] = 500.0
    b2 = ev.blend(streams, weeks[0] - DAY, weeks[-1] + 7 * DAY)
    assert [x["w"] for x in b1[:21]] == [x["w"] for x in b2[:21]]  # a future shock never changes earlier weights
    assert b1[10]["w"]["A"] > b1[10]["w"]["B"]  # calmer stream gets more weight


def test_pass_v2_rules():
    u = {"n": 120, "avg": 0.3, "pf": 1.5, "win_pos": 80, "last12": 4.0, "dd": 20}
    assert ev.pass_v2(u, {"n": 1, "avg": 0.1}, 97, False) == ("PASS v2", [])
    v, f = ev.pass_v2(dict(u, dd=40, win_pos=60), {"n": 1, "avg": -0.1}, 90, True)
    assert v == "REJECTED" and len(f) == 4


def test_pit_mask_month_of_decision():
    class U:
        sets = {ev.bt.ms("2022-03-01"): {"majors": ["A"]}, ev.bt.ms("2022-04-01"): {"majors": ["B"]}}

        def members(self, kind, t):
            s = self.sets.get(ev.mstart(t))
            return set(s[kind]) if s else set()
    t = np.array([ev.bt.ms("2022-03-31"), ev.bt.ms("2022-04-01")])
    E = ev.pit_mask(U(), "majors", ["A", "B"], t)
    assert E.tolist() == [[True, False], [False, True]]
    assert pd.Timestamp(ev.mstart(ev.bt.ms("2022-04-17")), unit="ms").day == 1


def test_segments_split_relaunch_and_redenomination_with_short_gaps():
    t = np.concatenate([np.arange(100) * H, 100 * H + 18 * DAY + np.arange(50) * H, 150 * H + 22 * DAY + np.arange(50) * H])
    px = np.concatenate([np.full(100, 0.0003), np.full(50, 8.6), np.full(50, 8.0)])  # ×28,000 jump; then a quiet gap
    a = np.column_stack([t, px, px, px, px, np.ones(200)])
    s = ed.segments(a, "LUNA")
    assert [n for n, _ in s] == ["LUNA", "LUNA#2"] and [len(x) for _, x in s] == [100, 100]


def test_random_holds_skip_data_gaps():
    a = np.column_stack([np.concatenate([np.arange(50) * H, 50 * H + 10 * DAY + np.arange(50) * H]),
                         np.ones((100, 5)) * 10.0])
    a[50:, 1:5] = 1000.0  # a jump across the gap would be a +9,900% "trade"
    t = {"key": ("X", "1h"), "hb": 10, "hb_ms": 10 * H, "side": "LONG", "entry_t": int(a[5, 0]), "gross": 0.0,
         "fund": 0.0}
    sims = ev.rand_trades([t], {("X", "1h"): (a, np.zeros(100), np.ones(100, bool))}, "F", [(0, 0, int(a[-1, 0]))],
                          np.random.default_rng(0), n=300)
    assert np.abs(sims).max() < 1.0  # only fees: no random hold crosses the gap


def test_funding_times_snap_to_the_hour_so_s11_sees_every_settlement():
    assert ed.hour_round([8 * H + 4, 16 * H - 3]).tolist() == [8 * H, 16 * H]
