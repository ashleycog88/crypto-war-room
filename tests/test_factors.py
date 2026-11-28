"""Phase 4 Part C factor engine checks: no look-ahead (truncation), costs, matched random portfolios."""
import types

import numpy as np

import factors as fx
import research as rs


def synthetic(n_coins=12, n_days=640, seed=5):
    rng = np.random.default_rng(seed)
    days = rs.START - 300 * fx.DAY + np.arange(n_days) * fx.DAY
    C = 100 * np.exp(np.cumsum(rng.normal(0.001, 0.09, (n_coins, n_days)), axis=1))  # S8 scale must bind
    O = np.concatenate([C[:, :1], C[:, :-1]], axis=1)
    V = rng.uniform(1e6, 5e6, (n_coins, n_days))
    coins = []
    for i in range(n_coins):
        ft = (days[0] + np.arange(n_days * 3) * 8 * 3_600_000).astype(np.int64)
        fn = rng.normal(0.0001 * (i - 6), 0.0005, len(ft))  # S6 ranks must move
        coins.append(types.SimpleNamespace(base=f"C{i}", ft=ft, fr=fn, fn=fn))
    btc = np.column_stack([days, O[0], C[0] * 1.01, C[0] * 0.99, C[0], V[0]])
    sma50 = np.convolve(C[0], np.ones(50) / 50, "full")[:n_days]
    sma50[:49] = np.nan
    return {"coins": coins, "days": days, "btc": btc, "sma50": sma50, "O": O, "Of": O.copy(), "C": C,
            "FC": np.cumsum(np.full_like(C, 0.01), axis=1), "V": V}


def cut(mx, d):
    """Everything known at the daily close d, plus the next open (the fill price)."""
    m = {k: (v[:, :d + 2] if isinstance(v, np.ndarray) and v.ndim == 2 else v) for k, v in mx.items()}
    m["days"], m["btc"], m["sma50"] = mx["days"][:d + 2], mx["btc"][:d + 2], mx["sma50"][:d + 2]
    m["coins"] = [types.SimpleNamespace(base=c.base, ft=c.ft[c.ft <= mx["days"][d] + fx.DAY],
                                        fr=c.fr[c.ft <= mx["days"][d] + fx.DAY], fn=c.fn[c.ft <= mx["days"][d] + fx.DAY])
                  for c in mx["coins"]]
    m["C"] = mx["C"][:, :d + 2].copy()
    m["C"][:, d + 1] = np.nan  # tomorrow's close is unknown
    return m


def decisions(mx, fam, p, d):
    if fam == "S6":
        dec = fx.s6_decide(mx, p)[0]
    elif fam == "S7":
        dec = fx.s7_decide(mx, p, fx.trend_signals(mx))[0]
    else:
        dec = fx.s8_decide(mx, p)[0]
    L, S, scale, _ = dec(d)
    return sorted(L), sorted(S), round(scale, 12)


def test_no_lookahead_all_families_by_truncation():
    mx = synthetic()
    for fam, p in (("S6", {"look": 7, "q": 3}), ("S7", {"combine": "rank", "reb": 7, "port": "ls"}),
                   ("S7", {"combine": "regress", "reb": 7, "port": "long"}), ("S8", {"variant": "ls", "win": 28, "reb": 7}),
                   ("S8", {"variant": "long", "win": 84, "reb": 1})):
        made = 0
        for d in (480, 500, 530, 560, 600):
            full = decisions(mx, fam, p, d)
            made += bool(full[0])
            assert full == decisions(cut(mx, d), fam, p, d), (fam, p, d)
        assert made >= 2, (fam, p, "too few real decisions: test would be trivial")


def test_period_costs_and_funding():
    mx = synthetic()
    d = 300
    sched = [(d, [0], [1], 1.0, {0, 1, 2}), (d + 7, [0], [2], 1.0, {0, 1, 2})]
    ps = fx.evaluate(mx, sched, 7, "F")
    O = mx["Of"]
    gross = 0.5 * (O[0, d + 8] / O[0, d + 1] - 1) * 100 - 0.5 * (O[1, d + 8] / O[1, d + 1] - 1) * 100
    fund = 0.5 * 0.07 - 0.5 * 0.07  # long pays, short receives the same 0.01 %/day here
    assert abs(ps[0]["net"] - (gross - fund - 1.0 * (0.05 + 0.05))) < 1e-9  # turnover 1.0 at the first rebalance
    assert abs(ps[1]["turn"] - 1.0) < 1e-12  # short leg swapped: 0.5 out + 0.5 in
    s = fx.evaluate(mx, sched[:1], 7, "S", stress=True)[0]
    assert abs(s["net"] - (gross - 1.0 * (0.10 * 1.5 + 0.15))) < 1e-9  # spot model: no funding


def test_matched_random_keeps_turnover_and_leg_sizes():
    rng = np.random.default_rng(3)
    m = fx.Matched(rng)
    valid = set(range(12))
    prev_s, prev_r = (set(), set()), (set(), set())
    for L, S in (([0, 1, 2], [9, 10, 11]), ([0, 1, 3], [9, 10, 11]), ([4, 5, 6], [7, 8, 9])):
        rl, rs_ = m(L, S, valid, prev_s, prev_r)
        assert len(rl) == len(L) and len(rs_) == len(S) and not set(rl) & set(rs_)
        if prev_r[0]:
            assert len(set(rl) - prev_r[0]) == len(set(L) - prev_s[0])  # same number of coins changed
        prev_s, prev_r = (set(L), set(S)), (set(rl), set(rs_))


def test_grids_are_pre_registered_and_small():
    assert all(len(g) <= 12 for g in fx.GRID.values()) and set(fx.GRID) == {"S6", "S7", "S8"}
