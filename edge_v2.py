"""Phase 5 "Edge Hunt v2" research (pod only, private outputs): 6-year rolling walk-forward on point-in-time coin
sets, re-judging S1/S5/S6/S8 and testing S9–S12. Reuses research.py / factors.py costs, engines and baselines.
Run: python edge_v2.py  → private_reports/edge_v2/edge_v2_report.md (gitignored)."""
import contextlib
import json
import os
import time
import zlib

import numpy as np
import pandas as pd

import backtest as bt
import edge_data as ed
import factors as fx
import research as rs

OUT = os.path.join(rs.ROOT, "private_reports", "edge_v2")
H, DAY = rs.H, fx.DAY
D0, END = bt.ms("2020-01-01"), ed.END
N_RAND, SEED = 500, 20261129
SLOTS = 20  # trade families: each trade uses 1/20 of capital (one slot per majors coin)
BENCH_APR = 5.0  # Binance USDT flexible earn, see SOURCES["bench"]
OLD = (bt.ms("2024-11-27"), bt.ms("2026-03-27"), bt.ms("2026-07-27"), END)  # Phase 3 rule: train, split, half, end
PAPER, POST = (bt.ms("2022-01-01"), bt.ms("2025-01-01")), (bt.ms("2025-01-01"), END)
T0 = time.time()

# Pre-registered settings (REQ-514). Every combination is listed in the appendix; total ≤ 100 (Q-52).
S9P = dict(alpha=2.5, k=14, KL=15, KS=15, gL=1.3, gS=1.7, wL=0.7, wS=0.3)
GRID = {
    "S1": rs.GRID["S1"], "S5": rs.GRID["S5"], "S6": fx.GRID["S6"], "S8": fx.GRID["S8"],
    "S9": [dict(theta=th, L=L) for th in (0.02, 0.05) for L in (28, 56)],  # paper's monthly per-coin search
    "S10": [dict(m=m, w=w) for m in (2, 3) for w in (288, 2016)],
    "S10-rare": [dict(m=m, w=2016) for m in (5, 8)],  # post-hoc (after the tester's firing-rate finding), labelled
    "S11": [dict(entry=en, exit=ex) for en in (0.01, 0.02, 0.03) for ex in (0.0, 0.005)],
    "S12": [dict(vol_days=60, reb="weekly")],
}
NAMES = {"S1": "Trend breakout LONG (4h Donchian)", "S5": "Top-3 momentum rotation", "S6": "Funding factor",
         "S8": "Vol-managed momentum", "S9": "AdaptiveTrend (6h trend + monthly Sharpe selection)",
         "S10": "Volatility-spike bounce (5m Garman-Klass, 12 h hold)", "S11": "Selective funding carry",
         "S12": "Near-miss blend (S1/S5/S6/S8, inverse-vol)"}
SOURCES = {
    "S9": "Nguyen, *Systematic Trend-Following with Adaptive Portfolio Construction* (AdaptiveTrend), arXiv 2602.11708 (Feb 2026), https://arxiv.org/html/2602.11708",
    "S10": "kquant.app indicator study and indicator labels (676 Binance perps 2020–2026), https://kquant.app/en/research/indicator-study/ , https://kquant.app/en/research/indicators/",
    "S11": "Background: thetapesays.com/research/funding-rate-carry-is-mostly-gone/ ; simplemarkets.io/strategies/research-log/funding-harvest-honest-numbers",
    "S12": "Our own Phase 3 / Part C near misses; inverse-volatility weighting (risk parity, e.g. Moreira & Muir 2017 for vol scaling)",
    "bench": "Binance Simple Earn USDT Flexible: 2024 average ≈ 6.2% (Binance Earn blog), Feb–May 2025 real-time ≈ 2.5–2.7% "
             "plus a 5% bonus only on the first 200–500 USDT (Binance Earn Wednesday announcements), Aug 2026 ≈ 3% real-time "
             "+ 4% bonus tier, Sep 2026 snapshot 6.59% (feeotter.com Binance Earn guide). We use a flat 5.0% APR for the whole span.",
}
DIFFS = {
    "S9": ["The paper does not publish θ_entry, the momentum look-back L, the ATR period k or its grid; we pre-registered "
           "θ ∈ {2%, 5%}, L ∈ {28, 56} six-hour bars (7/14 days), k = 14 and the paper's default α = 2.5, searched per "
           "coin on the previous month exactly as the paper describes (no other tuning).",
           "The paper ranks by CoinGecko market cap; we rank by trailing 30-day Binance quote volume (point-in-time): "
           "long candidates = top 15, short candidates = ranks 36–50 of the top-50 set (the paper's 'bottom-15 by market cap').",
           "Entries and exits fill at the next 6h open (the paper fills at the signal close). Fees 0.05%/side + 0.05% "
           "slippage + real funding (paper: 4 bps + size-based slippage). Weights are fixed at the month start; "
           "an empty short leg leaves its 30% in cash. Sharpe for selection uses rf = 0.",
           "Our 6h bars are built from Binance USDⓈ-M 1h archives (UTC-aligned 00/06/12/18)."],
    "S10": ["kquant does not publish its Laboratory's default threshold. Our pre-registered reading: per-bar Garman-Klass "
            "volatility; a spike = the closed 5m bar's GK vol crosses above m × the median GK vol of the previous w bars "
            "(m ∈ {2, 3}, w ∈ {1 day, 7 days}); long at the next 5m open, exit at the open 144 bars (12 h) later, "
            "no stop or target, one open trade per coin.",
            "kquant used 676 point-in-time perpetuals; we use 5m SPOT candles for the point-in-time majors (top 20), "
            "with model F costs (futures fees + that coin's perp funding) and model S costs (spot fees)."],
    "S11": ["Entry when the mean of the last 3 funding settlements (8h-equivalent) exceeds the entry threshold, exit when it "
            "falls below the exit threshold; both legs at the next 1h open after the settlement. Fees: spot 0.10% × 2 + "
            "perp 0.05% × 2 + 0.05% slippage on each of the 4 fills; the real spot and perp prices give the gap at "
            "entry/exit. Capital = spot notional + short-leg margin; up to 10 positions with equal capital; idle "
            "capital earns nothing (stricter than parking it in Earn)."],
    "S12": ["Streams are the walk-forward (unseen) S1-long (model F), S5, S6 and S8 results; weekly weights ∝ 1/σ of each "
            "stream's weekly returns over the previous 9 weeks (≈ 60 days); re-weighting costs are not charged extra."],
}
PARKED = [("Cascade-fade 1–3-minute scalper", "needs automatic order execution; the user trades by hand"),
          ("21–23 UTC intraday seasonality", "published 2025–26 tests lose money after fees"),
          ("Pairs trading", "2026 literature is negative after two-leg costs"),
          ("ETF-flow next-day signal", "vanishes once timestamps are aligned (flows are published after the close)")]


def log(*a):
    print(f"[{time.time() - T0:7.1f}s]", *a, flush=True)


def rng_for(*key):
    return np.random.default_rng([SEED, zlib.crc32(json.dumps(key, default=str).encode())])


# ---------------------------------------------------------------- calendar: walk-forward windows (REQ-510)
def add_months(t, k):
    return int((pd.Timestamp(t, unit="ms") + pd.DateOffset(months=k)).value // 1_000_000)


def mstart(t):
    ts = pd.Timestamp(int(t), unit="ms")
    return int(pd.Timestamp(year=ts.year, month=ts.month, day=1).value // 1_000_000)


def windows(first_unseen=bt.ms("2021-07-01"), end=END, look=18, step=6):
    """[(look-back start, unseen start, unseen end)]: settings picked on [ls, s), traded unseen on [s, e)."""
    out, s = [], first_unseen
    while s < end:
        out.append((add_months(s, -look), s, min(add_months(s, step), end)))
        s = add_months(s, step)
    return out


WINDOWS = windows()
OLDW = [(OLD[0], OLD[1], OLD[3])]


@contextlib.contextmanager
def span(start=D0, end=END):
    """research.py's engines filter entries to [START, END); widen them to the 6-year span for this run only."""
    old = rs.START, rs.END
    rs.START, rs.END = start, end
    try:
        yield
    finally:
        rs.START, rs.END = old


# ---------------------------------------------------------------- data: point-in-time sets + coins
class Universe:
    def __init__(self):
        self.sets, self.first, self.last = ed.load_sets()
        self.perp = {ed.base_of(s) for s in ed.symbols("perp")}

    def members(self, kind, t):
        s = self.sets.get(mstart(t))
        return set(s[kind]) if s else set()

    def ever(self, kind):
        return sorted({b for s in self.sets.values() for b in s[kind]})

    def perp_of(self, name):
        """(perp symbol, price multiplier) for a spot coin name, or None."""
        b = name.split("#")[0]
        if "#" in name:
            return None  # a re-used ticker's later life: perp history would belong to the old coin
        if b in self.perp:
            return b + "USDT", 1.0
        if "1000" + b in self.perp:
            return "1000" + b + "USDT", 1000.0
        return None


def funding_for(U, name, lo, hi):
    pm = U.perp_of(name)
    f = ed.load_funding(pm[0]) if pm else None
    if f is None:
        return (np.zeros(0, np.int64), np.zeros(0), np.zeros(0)), "none"
    m = (f[0] >= lo) & (f[0] < hi + DAY)
    return (f[0][m], f[1][m], f[2][m]), "binance"


def spot_coin(U, name):
    seg = dict(ed.spot_segments(name.split("#")[0])).get(name)
    if seg is None or len(seg) < 24 * 40:
        return None
    fund, src = funding_for(U, name, seg[0, 0], seg[-1, 0])
    return rs.Coin(name, seg, fund, src)


def pit_mask(U, kind, names, times):
    """E[c, j] = coin c is in the point-in-time set of the month containing times[j] (the decision time)."""
    months = np.array([mstart(t) for t in times])
    E = np.zeros((len(names), len(times)), bool)
    for m in np.unique(months):
        mem = U.members(kind, m)
        cols = months == m
        for ci, n in enumerate(names):
            if n in mem:
                E[ci, cols] = True
    return E


# ---------------------------------------------------------------- statistics + PASS v2 (REQ-511)
def eq_dd(r, compound):
    eq = np.cumprod(1 + np.asarray(r) / 100) if compound else 1 + np.cumsum(r) / 100
    eq = np.concatenate([[1.0], eq])
    return float(np.max(1 - eq / np.maximum.accumulate(eq)) * 100), float((eq[-1] - 1) * 100)


def tstats(tr, model, wins, stress=False):
    """Trade family: expectancy = avg net % per trade; returns use 1/SLOTS of capital per trade."""
    if not tr:
        return {"n": 0}
    tr = sorted(tr, key=lambda t: t["exit_t"])
    nets = np.array([rs.net(t, model, stress) for t in tr])
    dd, ret = eq_dd(nets / SLOTS, False)
    pos, neg = nets[nets > 0].sum(), -nets[nets <= 0].sum()
    per_w = [float(sum(rs.net(t, model, stress) for t in tr if s <= t["entry_t"] < e) / SLOTS) for _, s, e in wins]
    last = [t for t in tr if t["entry_t"] >= END - 365 * DAY]
    return {"n": len(tr), "avg": float(nets.mean()), "pf": float(pos / neg) if neg > 0 else float("inf"),
            "win": float((nets > 0).mean() * 100), "ret": ret, "dd": dd, "per_window": per_w,
            "win_pos": float(np.mean([x > 0 for x in per_w]) * 100),
            "last12": float(sum(rs.net(t, model, stress) for t in last) / SLOTS)}


def pstats(ps, wins):
    """Portfolio family (periods with net % of the portfolio)."""
    if not ps:
        return {"n": 0}
    r = np.array([x["net"] for x in ps])
    dd, ret = eq_dd(r, True)
    pos, neg = r[r > 0].sum(), -r[r < 0].sum()
    per_w = [float((np.prod([1 + x["net"] / 100 for x in ps if s <= x["t"] < e]) - 1) * 100) for _, s, e in wins]
    last = [x["net"] for x in ps if x["t"] >= END - 365 * DAY]
    return {"n": len(ps), "avg": float(r.mean()), "pf": float(pos / neg) if neg > 0 else float("inf"),
            "win": float((r > 0).mean() * 100), "ret": ret, "dd": dd, "per_window": per_w,
            "win_pos": float(np.mean([x > 0 for x in per_w]) * 100),
            "last12": float((np.prod([1 + x / 100 for x in last]) - 1) * 100) if last else 0.0,
            "in_mkt": float(np.mean([x["in"] for x in ps]) * 100)}


def pass_v2(u, stress, pct, portfolio):
    f = []
    if not u.get("n") or u["avg"] <= 0:
        f.append("expectancy ≤ 0")
    if u.get("n") and u["pf"] < 1.2:
        f.append("profit factor < 1.2")
    if u.get("n", 0) < (40 if portfolio else 100):
        f.append(f"< {40 if portfolio else 100} {'rebalances' if portfolio else 'trades'}")
    if not u.get("n") or u["win_pos"] < 70:
        f.append("positive in < 70% of unseen windows")
    if not u.get("n") or u["last12"] <= 0:
        f.append("not positive over the latest 12 months")
    if pct is None or pct < 95:
        f.append("does not beat 95% of random baselines")
    if not stress.get("n") or stress["avg"] <= 0:
        f.append("not positive at 1.5× costs")
    if not u.get("n") or u["dd"] > 35:
        f.append("max drawdown > 35%")
    return ("PASS v2" if not f else "REJECTED"), f


def old_verdict(u, h1, h2, stress, pct, portfolio):
    """Phase 3 / Part C rule (training 2024-11→2026-03, unseen 2026-03→2026-11, both 4-month halves)."""
    f = []
    if u.get("n", 0) < (20 if portfolio else 30):
        f.append(f"< {20 if portfolio else 30} unseen {'rebalances' if portfolio else 'trades'}")
    if not u.get("n") or u["avg"] <= 0:
        f.append("expectancy ≤ 0")
    if u.get("n") and u["pf"] < 1.2:
        f.append("profit factor < 1.2")
    if not h1.get("n") or h1["ret"] <= 0 or not h2.get("n") or h2["ret"] <= 0:
        f.append("not positive in both halves")
    if pct is None or pct < 95:
        f.append("does not beat 95% of random")
    if not stress.get("n") or stress["avg"] <= 0:
        f.append("not positive in the stress case")
    return ("PASSED" if not f else "REJECTED"), f


# ---------------------------------------------------------------- walk-forward selection
def pick_trades(by_combo, model, ls, s, min_n=30):
    """Fixed rule: highest look-back expectancy among settings with ≥ min_n look-back trades (fewest-index tie)."""
    best, bi = None, None
    for ci, tr in enumerate(by_combo):
        lb = [t for t in tr if ls <= t["entry_t"] and t["exit_t"] <= s]
        assert all(t["exit_t"] <= s for t in lb)  # selection sees only finished trades before the window
        if len(lb) >= min_n:
            v = np.mean([rs.net(t, model) for t in lb])
            if best is None or v > best:
                best, bi = v, ci
    return bi


def pick_periods(by_combo, ls, s, min_n=20):
    if len(by_combo) == 1:
        return 0  # one pre-registered setting (S9, S12): nothing to pick
    best, bi = None, None
    for ci, ps in enumerate(by_combo):
        lb = [x for x in ps if ls <= x["t"] and x["t_end"] <= s]
        if len(lb) >= min_n:
            v = np.prod([1 + x["net"] / 100 for x in lb])
            if best is None or v > best:
                best, bi = v, ci
    return bi


def join_trades(by_combo, chosen, wins):
    return [t for (ls, s, e), ci in zip(wins, chosen) if ci is not None for t in by_combo[ci] if s <= t["entry_t"] < e]


# ---------------------------------------------------------------- random baselines
def rand_trades(tr, bars, model, wins, rng, n=N_RAND):
    """Matched random entries: same coin, side, holding bars and window; entry bar drawn among bars whose signal bar
    was in the coin's point-in-time set. Returns n mean nets."""
    sims, cache = np.zeros((n, len(tr))), {}
    for k, t in enumerate(tr):
        a, cf, ok = bars[t["key"]]
        w = next((s, e) for _, s, e in wins if s <= t["entry_t"] < e)
        hb = t["hb"]
        ck = (t["key"], w, hb)
        if ck not in cache:
            lo, hi = np.searchsorted(a[:, 0], w[0]), np.searchsorted(a[:, 0], w[1])
            idx = np.nonzero(ok[lo:hi])[0] + lo + 1  # entry bar i: signal bar i-1 eligible
            idx = idx[(idx < hi) & (idx + hb < len(a))]
            tf = t["hb_ms"] // max(hb, 1)
            cache[ck] = idx[a[idx + hb - 1, 0] - a[idx, 0] == (hb - 1) * tf]  # no data gap inside the random hold
        idx = cache[ck]
        if not len(idx):
            sims[:, k] = rs.net(t, model)
            continue
        ii = rng.choice(idx, n)
        jj = ii + hb - 1
        s = 1 if t["side"] == "LONG" else -1
        gross = s * (a[jj, 4] / a[ii, 1] - 1) * 100
        fund = cf[np.minimum(jj + 1, len(cf) - 1)] - cf[ii]
        side = "long" if s == 1 else "short"
        fund = fund * s if (side == "short" or rs.COST[model]["fund_long"]) else 0 * fund
        sims[:, k] = gross - 2 * rs.COST[model][side] - 2 * rs.SLIP - fund
    return sims


def pct_of(real, sims):
    return float((np.asarray(sims) < real).mean() * 100)


# ---------------------------------------------------------------- trade families: S1-long and S10
def s1_trades(coins, U, p):
    btc = next(c for c in coins if c.base == "BTC").bars["4h"]
    out, bars = [], {}
    with span():
        for c in coins:
            a = c.bars["4h"]
            ok = pit_mask(U, "majors", [c.base], a[:, 0] + rs.TF["4h"])[0]
            L, _, make = rs.s1_setup(c, p, btc)
            for t in rs.walk(L & ok, np.zeros(len(a), bool), a, make):
                t["key"], t["hb"] = (c.base, "4h"), (t["j"] - t["i"] if t["why"] == "channel" else t["j"] + 1 - t["i"])
                t["hb_ms"] = t["hb"] * rs.TF["4h"]
                out.append(t)
            bars[(c.base, "4h")] = (a, c.cum_funding("4h"), ok)
    return out, bars


def gk_vol(a):
    o, h, l, c = a[:, 1], a[:, 2], a[:, 3], a[:, 4]
    v = 0.5 * np.log(h / l) ** 2 - (2 * np.log(2) - 1) * np.log(c / o) ** 2
    return np.sqrt(np.clip(v, 0, None))


def s10_signal(a, p):
    """Spike at closed bar i: GK vol crosses above m × median of the previous w bars (known at bar i's close)."""
    v = gk_vol(a)
    thr = p["m"] * pd.Series(v).rolling(p["w"], min_periods=p["w"]).median().shift(1).to_numpy()
    above = v > thr
    return above & ~np.concatenate([[True], above[:-1]]) & np.isfinite(thr)


def s10_coin_trades(name, a, cf, ok, p, hold=144):
    sig = s10_signal(a, p) & ok
    out, free = [], 0
    for i in np.nonzero(sig)[0]:
        if i < free or i + 1 + hold >= len(a) or a[i + 1, 0] < D0 or a[i + 1, 0] >= END:
            continue
        e, x = i + 1, i + 1 + hold
        if a[x, 0] - a[e, 0] != hold * 300_000:  # a gap inside the hold: skip rather than invent prices
            continue
        out.append({"fam": "S10", "combo": p, "coin": name, "side": "LONG", "i": e, "j": x - 1, "hb": hold,
                    "sig_close": int(a[i, 0] + 300_000), "used_t": int(a[i, 0] + 300_000), "entry_t": int(a[e, 0]),
                    "entry": float(a[e, 1]), "exit_t": int(a[x, 0]), "exit": float(a[x, 1]),
                    "gross": (a[x, 1] / a[e, 1] - 1) * 100, "fund": float(cf[x] - cf[e]), "why": "time",
                    "hold_h": 12.0, "key": (name, "5m"), "risk": 1.0,
                    "hb_ms": hold * 300_000})
        free = x
    return out


def cum_fund_at(c, t):
    cs = np.concatenate([[0.0], np.cumsum(c.fr)])
    return cs[np.searchsorted(c.ft, t, side="right")] * 100


# ---------------------------------------------------------------- portfolio families on factors.py: S5, S6, S8
def pit_mx(mx, U, kind):
    """Masks only O (what decide() looks at); fx.period prices with Of/FC, so pricing stays unmasked."""
    assert "mx['O']" not in __import__("inspect").getsource(fx.period).replace('mx["O"]', "mx['O']")
    days = mx["days"]
    E = pit_mask(U, kind, [c.base for c in mx["coins"]], days + DAY)  # decision at close d (time days[d] + 1 day)
    O = np.full_like(mx["O"], np.nan)
    O[:, 1:] = np.where(E[:, :-1], mx["O"][:, 1:], np.nan)  # decide() checks O[:, d+1]
    return dict(mx, O=O)


def s5_decide(mx, p):
    def decide(d):
        top, valid = rs.s5_top(mx["C"], d, np.isfinite(mx["O"][:, d + 1]), mx["btc"], mx["sma50"], p["k"])
        return sorted(top), [], 1.0, set(valid.tolist())
    return decide, 7 if p["reb"] == "weekly" else 1, "S"


def fam_scheds(fam, mx):
    out = []
    for p in GRID[fam]:
        decide, step, model = {"S5": s5_decide, "S6": fx.s6_decide, "S8": fx.s8_decide}[fam](mx, p)
        out.append((schedule6y(mx, decide, step), step, model))
    return out


def schedule6y(mx, decide, step):
    """factors.schedule's calendar (same Tuesday anchor, same warm-up) without its 2024-11 start."""
    days = mx["days"]
    return [(d, *decide(d)) for d in range(len(days) - step - 1)
            if d >= fx.WARMUP and (days[d] - fx.ANCHOR) % (step * DAY) == 0]


def join_periods(mx, scheds, chosen, wins, stress=False, rand=None):
    days = mx["days"]
    out, w_old, prev_s, prev_r, last_end = [], {}, (set(), set()), (set(), set()), 0
    for (ls, s, e), ci in zip(wins, chosen):
        if ci is None:
            continue
        sched, step, model = scheds[ci]
        for d, L, S, scale, valid in sched:
            if not (s <= days[d + 1] < e) or days[d + 1] < last_end:
                continue
            if rand is not None:
                L, S = rand(L, S, valid, prev_s, prev_r)
                prev_s, prev_r = (set(rand.last_s[0]), set(rand.last_s[1])), (set(L), set(S))
            w = fx.weights(L, S, scale)
            net, turn = fx.period(mx, d, d + step, w, w_old, model, stress)
            out.append({"t": int(days[d + 1]), "t_end": int(days[d + step + 1]), "net": net, "in": bool(w),
                        "turn": turn, "coins": sorted(mx["coins"][x].base for x in w)})
            w_old, last_end = w, int(days[d + step + 1])
    return out


def per_combo_periods(mx, scheds):
    return [join_periods(mx, scheds, [ci], [(D0, D0, END)]) for ci in range(len(scheds))]


# ---------------------------------------------------------------- S9 AdaptiveTrend (6h, perps)
def grid6(U, names):
    """Common 6h grid: opens/highs/lows/closes, ATR, open-to-open funding (%) per perp coin (NaN where missing)."""
    G = np.arange(D0, END, 6 * H)
    n = len(names)
    O, Hh, Lw, C, A, F = (np.full((n, len(G)), np.nan) for _ in range(6))
    for ci, nm in enumerate(names):
        pm = U.perp_of(nm)
        a = ed.load("perp", pm[0]) if pm else None
        if a is None or len(a) < 200:
            continue
        b = rs.resample(a, 6 * H)
        ix = np.searchsorted(G, b[:, 0])
        m = (ix < len(G)) & (G[np.clip(ix, 0, len(G) - 1)] == b[:, 0])
        O[ci, ix[m]], Hh[ci, ix[m]], Lw[ci, ix[m]], C[ci, ix[m]] = b[m, 1], b[m, 2], b[m, 3], b[m, 4]
        A[ci, ix[m]] = rs.atr(b, S9P["k"])[m]
        f = ed.load_funding(pm[0])
        if f is not None and len(f[0]):
            cs = np.concatenate([[0.0], np.cumsum(f[1])]) * 100
            cf = cs[np.searchsorted(f[0], G, side="right")]
            F[ci, :-1] = cf[1:] - cf[:-1]
    R = np.full_like(O, np.nan)
    R[:, :-1] = O[:, 1:] / O[:, :-1] - 1
    return {"G": G, "O": O, "H": Hh, "L": Lw, "C": C, "A": A, "F": np.nan_to_num(F), "R": R, "names": names}


def s9_path(g, ci, p, side, j0, j1, state=None):
    """Positions held during bars j0..j1-1 (from open j to open j+1). Signal at close j-1 (MOM over L bars), entry at
    open j; trailing stop from closes; exit at the next open after a close beyond the stop. Returns (pos, state)."""
    C, A = g["C"][ci], g["A"][ci]
    s = 1 if side == "LONG" else -1
    pos = np.zeros(j1 - j0)
    held, stop = state if state else (0, np.nan)
    for j in range(j0, j1):
        c, prev = C[j - 1], C[j - 1 - p["L"]] if j - 1 - p["L"] >= 0 else np.nan
        if not np.isfinite(g["O"][ci, j]) or not np.isfinite(c) or not np.isfinite(g["R"][ci, j]):
            held, stop = 0, np.nan  # no data: flat (a coin that stops trading exits)
            continue
        if held:
            stop = max(stop, c - S9P["alpha"] * A[j - 1]) if s == 1 else min(stop, c + S9P["alpha"] * A[j - 1])
            if (s == 1 and c < stop) or (s == -1 and c > stop):
                held, stop = 0, np.nan
        if not held and np.isfinite(prev):
            mom = c / prev - 1
            if (s == 1 and mom > p["theta"]) or (s == -1 and mom < -p["theta"]):
                held, stop = 1, c - s * S9P["alpha"] * A[j - 1]
        pos[j - j0] = s * held
    return pos, (held, stop)


def bar_net(g, ci, pos, j0, stress=False, prev=0.0):
    """Net % per bar for a position path: price move, funding (longs pay), 0.05% fee + slippage per side changed."""
    n = len(pos)
    r = np.nan_to_num(g["R"][ci, j0:j0 + n]) * 100
    f = g["F"][ci, j0:j0 + n]
    fee = rs.COST["F"]["long"] * (rs.STRESS_FEE if stress else 1) + (rs.STRESS_SLIP if stress else rs.SLIP)
    ch = np.abs(np.diff(np.concatenate([[prev], pos])))
    return pos * r - pos * f - ch * fee


def sharpe(x):
    sd = np.std(x)
    return float(np.mean(x) / sd * np.sqrt(4 * 365)) if sd > 0 else 0.0


def s9_months(U):
    months = [m for m in sorted(U.sets) if m >= bt.ms("2020-03-01") and m < END]
    return months


def s9_plan(U, g):
    """Monthly (paper's procedure, no tuning by us): candidates from the point-in-time top-50 (long = ranks 1–15,
    short = ranks 36–50); per coin pick (θ, L) by the previous month's Sharpe; keep coins with Sharpe ≥ γ; 70/30 equal
    weights; positions carry over while a coin stays selected. Uses only bars before the month (asserted)."""
    G, idx = g["G"], {n: i for i, n in enumerate(g["names"])}
    plan, states = [], {}
    for m in s9_months(U):
        nxt = min(add_months(m, 1), END)
        j0, j1, p0 = int(np.searchsorted(G, m)), int(np.searchsorted(G, nxt)), int(np.searchsorted(G, add_months(m, -1)))
        if j1 - j0 < 4 or p0 < 60:
            continue
        assert G[j0 - 1] < m  # selection bars end before the month starts
        top = U.sets[m]["top50"]
        legs = {"LONG": [b for b in top[:S9P["KL"]] if b in idx],
                "SHORT": [b for b in top[-S9P["KS"]:] if b in idx] if len(top) >= 50 else []}
        sel = {}
        for side, cands in legs.items():
            for b in cands:
                best = None
                for p in GRID["S9"]:
                    pos, _ = s9_path(g, idx[b], p, side, p0, j0)  # previous month only (fresh from flat)
                    sr = sharpe(bar_net(g, idx[b], pos, p0))
                    if best is None or sr > best[0]:
                        best = (sr, p)
                if best and best[0] >= (S9P["gL"] if side == "LONG" else S9P["gS"]):
                    sel[(b, side)] = best[1]
        nL = sum(1 for (_, sd) in sel if sd == "LONG")
        nS = len(sel) - nL
        items, new_states = [], {}
        for (b, side), p in sel.items():
            st = states.get((b, side))
            pos, new_states[(b, side)] = s9_path(g, idx[b], p, side, j0, j1, st)
            prev = (1 if side == "LONG" else -1) * st[0] if st else 0.0
            items.append({"b": b, "side": side, "p": p, "w": S9P["wL"] / nL if side == "LONG" else S9P["wS"] / nS,
                          "pos": pos, "prev": prev})
        dropped = [(k, S9P["wL"] / max(nL, 1) if k[1] == "LONG" else S9P["wS"] / max(nS, 1))
                   for k, st in states.items() if k not in sel and st[0]]
        states = new_states
        plan.append({"m": int(m), "end": int(nxt), "j0": j0, "j1": j1, "items": items, "dropped": dropped, "legs": legs})
    return plan


def s9_eval(g, plan, stress=False, rng=None):
    """Monthly periods. rng: matched random — each selected coin replaced by a random coin of the same candidate leg,
    its real position path circularly shifted (same side, exposure and holding times)."""
    idx = {n: i for i, n in enumerate(g["names"])}
    fee = rs.COST["F"]["long"] * (rs.STRESS_FEE if stress else 1) + (rs.STRESS_SLIP if stress else rs.SLIP)
    out = []
    for mo in plan:
        tot = np.zeros(mo["j1"] - mo["j0"])
        for it in mo["items"]:
            ci, pos, prev = idx[it["b"]], it["pos"], it["prev"]
            if rng is not None:
                pool = mo["legs"][it["side"]]
                ci, pos, prev = idx[pool[rng.integers(len(pool))]], np.roll(pos, rng.integers(len(pos))), 0.0
            tot += it["w"] * bar_net(g, ci, pos, mo["j0"], stress, prev)
        if len(tot):
            tot[0] -= sum(w * fee for _, w in mo["dropped"])
        out.append({"t": mo["m"], "t_end": mo["end"], "net": float(tot.sum()), "in": bool(mo["items"]),
                    "coins": sorted(f"{it['b']}:{it['side'][0]}" for it in mo["items"])})
    return out


# ---------------------------------------------------------------- S11 selective funding carry
def s11_coin(U, name):
    pm = U.perp_of(name)
    if not pm:
        return None
    sp = dict(ed.spot_segments(name)).get(name)
    pp, f = ed.load("perp", pm[0]), ed.load_funding(pm[0])
    if sp is None or pp is None or f is None or len(f[0]) < 30:
        return None
    t = np.intersect1d(sp[:, 0], pp[:, 0])
    if len(t) < 24 * 30:
        return None
    S = sp[np.searchsorted(sp[:, 0], t)]
    P = pp[np.searchsorted(pp[:, 0], t)].copy()
    P[:, 1:5] /= pm[1]  # 1000-unit perps → per-coin prices
    return {"name": name, "t": t, "S": S, "P": P, "ft": f[0], "fr": f[1], "fn": f[2]}


def s11_trades(c, p, U):
    """Signals at each funding settlement from the mean of the last 3 (8h-equivalent); legs at the next 1h open."""
    ft, fn, t = c["ft"], c["fn"], c["t"]
    mean3 = pd.Series(fn).rolling(3).mean().to_numpy() * 100
    out, k, n = [], 2, len(ft)
    while k < n:
        if not (mean3[k] > p["entry"] and c["name"] in U.members("top50", ft[k])):
            k += 1
            continue
        i = int(np.searchsorted(t, ft[k] + H))
        if i >= len(t) or t[i] != ft[k] + H:
            k += 1
            continue
        x = k + 1
        while x < n and mean3[x] >= p["exit"]:
            x += 1
        if x >= n:
            break
        j = int(np.searchsorted(t, ft[x] + H))
        if j >= len(t) or t[j] >= END:
            break
        tr = carry_trade(c, i, j)  # short perp receives positive funding
        tr.update(sig_t=int(ft[k]))
        out.append(tr)
        k = x + 1
    return out


def carry_trade(c, i, j):
    """Both legs from open i to open j; for 1× and 2× the first hour whose perp high reaches the liquidation level
    (loss = margin − 0.5% maintenance) and the spot close of that hour (the hedge is gone, so spot is sold there)."""
    t, P, S = c["t"], c["P"], c["S"]
    hi = P[i:max(j, i + 1), 2]  # highs while the short is open (the exit is at open j)
    liq = {}
    for lev in (1, 2):
        k = np.nonzero(hi >= P[i, 1] * (1 + 1 / lev - 0.005))[0]
        if len(k):
            b = i + int(k[0])
            liq[lev] = (int(t[b]), float(S[b, 4]), float(c["fr"][(c["ft"] > t[i]) & (c["ft"] <= t[b])].sum() * 100))
    return {"coin": c["name"], "entry_t": int(t[i]), "exit_t": int(t[j]), "i": i, "j": j, "s_in": float(S[i, 1]),
            "s_out": float(S[j, 1]), "p_in": float(P[i, 1]), "p_out": float(P[j, 1]),
            "fund": float(c["fr"][(c["ft"] > t[i]) & (c["ft"] <= t[j])].sum() * 100),
            "max_up": (float(np.max(hi)) / P[i, 1] - 1) * 100, "liq": liq}


def carry_net(tr, lev, stress=False):
    """% return on capital (spot notional + margin) after 4 taker fills. Liquidation: the short leg's loss reaches its
    margin minus 0.5% maintenance → the margin is lost, funding stops, and spot is sold at that hour's close."""
    fee = (2 * 0.10 + 2 * 0.05) * (rs.STRESS_FEE if stress else 1) + 4 * (rs.STRESS_SLIP if stress else rs.SLIP)
    lq = tr.get("liq", {}).get(lev)
    liq = lq is not None
    spot = ((lq[1] if liq else tr["s_out"]) / tr["s_in"] - 1) * 100
    perp = -100 / lev if liq else -(tr["p_out"] / tr["p_in"] - 1) * 100
    pnl = spot + perp + (lq[2] if liq else tr["fund"]) - fee
    return pnl / (1 + 1 / lev), liq


def carry_book(trs, lev, wins, stress=False, slots=10):
    """Up to `slots` equal-capital positions, first come first served; APR per window (idle capital earns 0)."""
    taken, busy = [], []
    for tr in sorted(trs, key=lambda x: x["entry_t"]):
        busy = [b for b in busy if b[0] > tr["entry_t"]]
        if len(busy) < slots and all(b[1] != tr["coin"] for b in busy):
            taken.append(tr)
            busy.append((tr["exit_t"], tr["coin"]))
    res = [carry_net(t, lev, stress) for t in taken]
    per_w, liqs = [], sum(1 for _, lq in res if lq)
    for _, s, e in wins:
        tot = sum(r for t, (r, _) in zip(taken, res) if s <= t["entry_t"] < e) / slots
        per_w.append(tot / ((e - s) / (365 * DAY)))
    yrs = sum((e - s) for _, s, e in wins) / (365 * DAY)
    tot = sum(r for t, (r, _) in zip(taken, res) if wins[0][1] <= t["entry_t"] < wins[-1][2]) / slots
    return {"n": len(taken), "apr": tot / yrs if yrs else 0.0, "per_window": per_w, "liq": liqs,
            "above": float(np.mean([x > BENCH_APR for x in per_w]) * 100) if per_w else 0.0, "taken": taken}


def carry_random(trs, coins, lev, wins, rng, n=N_RAND):
    """Same coins and durations, random entry hour inside the same window."""
    by = {c["name"]: c for c in coins}
    out, cache = [], {}
    for _ in range(n):
        sim = []
        for tr in trs:
            c = by[tr["coin"]]
            w = next((s, e) for _, s, e in wins if s <= tr["entry_t"] < e)
            hb = tr["j"] - tr["i"]
            key = (tr["coin"], w, hb)
            if key not in cache:
                lo, hi = np.searchsorted(c["t"], w[0]), np.searchsorted(c["t"], w[1]) - hb - 1
                ii = np.arange(lo, max(lo, hi))
                cache[key] = ii[c["t"][ii + hb] - c["t"][ii] == hb * H]  # gap-free holds only
            ii = cache[key]
            if not len(ii):
                sim.append(tr)
                continue
            i = int(rng.choice(ii))
            sim.append(carry_trade(c, i, i + hb))
        out.append(carry_book(sim, lev, wins)["apr"])
    return out


# ---------------------------------------------------------------- S12 blend
def weekly(stream, kind):
    """{week start: net %}; weeks anchored on factors.py's calendar (Tuesday closes)."""
    out = {}
    for x in stream:
        t = x["exit_t"] if kind == "trades" else x["t"]
        wk = fx.ANCHOR + DAY + ((t - fx.ANCHOR - DAY) // (7 * DAY)) * 7 * DAY
        r = x["net"] / SLOTS if kind == "trades" else x["net"]
        out[wk] = (1 + out.get(wk, 0) / 100) * (1 + r / 100) * 100 - 100 if kind != "trades" else out.get(wk, 0) + r
    return out


def blend(streams, start, end, vol_weeks=9):
    """Weights at week w ∝ 1/σ of each stream's weekly returns over the previous vol_weeks weeks (past only)."""
    weeks = list(range(fx.ANCHOR + DAY + ((start - fx.ANCHOR - DAY) // (7 * DAY) + 1) * 7 * DAY, end - 7 * DAY + 1,
                       7 * DAY))
    hist = {k: [] for k in streams}
    out = []
    for wk in weeks:
        sd = {k: np.std(h[-vol_weeks:]) if len(h) >= 4 else np.nan for k, h in hist.items()}
        inv = {k: 1 / v for k, v in sd.items() if np.isfinite(v) and v > 0}
        w = {k: v / sum(inv.values()) for k, v in inv.items()} if inv else {k: 1 / len(streams) for k in streams}
        r = sum(w.get(k, 0) * streams[k].get(wk, 0.0) for k in streams)
        out.append({"t": int(wk), "t_end": int(wk + 7 * DAY), "net": float(r), "in": True, "w": w})
        for k in streams:
            hist[k].append(streams[k].get(wk, 0.0))
    return out


# ---------------------------------------------------------------- audits
def survivorship_audit(U):
    """Q-51: independent pandas recomputation of each month's top-50 for later-delisted coins; count coins that
    qualified but are missing. REQ-541: count coins selected before they traded 30 days."""
    vols, delisted = {}, []
    for s in ed.symbols("spot"):
        for name, a in ed.spot_segments(ed.base_of(s)):
            df = pd.DataFrame({"t": pd.to_datetime(a[:, 0], unit="ms"), "qv": a[:, 5] * a[:, 4]}).set_index("t")
            vols[name] = (df["qv"].resample("1D").sum(), int(a[0, 0]), int(a[-1, 0]),
                          df["qv"].resample("1D").count())
            if a[-1, 0] < END - 7 * DAY:
                delisted.append(name)
    missing, in_sets, viol = 0, set(), 0
    for t0, st in U.sets.items():
        ts = pd.Timestamp(t0, unit="ms")
        sc = []
        for nm, (qv, first, last, cnt) in vols.items():
            if first > t0 - 30 * DAY:
                continue
            w = (qv.index >= ts - pd.Timedelta(days=30)) & (qv.index < ts)
            if (cnt[w] > 0).sum() >= 20:
                sc.append((qv[w].sum(), nm))
        top = {nm for _, nm in sorted(sc, reverse=True)[:50]}
        for nm in delisted:
            if nm in top:
                in_sets.add(nm)
                missing += nm not in st["top50"]
        viol += sum(1 for b in st["top50"] if U.first[b] > t0 - 30 * DAY)
    return {"delisted": len(delisted), "delisted_in_sets": len(in_sets), "missing": missing, "pit_violations": viol}


def lookahead_count(trades):
    return sum(1 for t in trades if not (t["used_t"] <= t["entry_t"] and t["sig_close"] <= t["entry_t"]))


# ---------------------------------------------------------------- family runners
def judge_trades(fam, by_combo, bars, model):
    chosen = [pick_trades(by_combo, model, ls, s) for ls, s, _ in WINDOWS]
    tr = join_trades(by_combo, chosen, WINDOWS)
    u, st = tstats(tr, model, WINDOWS), tstats(tr, model, WINDOWS, stress=True)
    sims = rand_trades(tr, bars, model, WINDOWS, rng_for(fam, model, "wf")) if tr else np.zeros((N_RAND, 0))
    pct = pct_of(u["avg"], sims.mean(axis=1)) if tr else None
    v, fails = pass_v2(u, st, pct, False)
    oc = pick_trades(by_combo, model, OLD[0], OLD[1])
    otr = [t for t in by_combo[oc] if OLD[1] <= t["entry_t"] < OLD[3]] if oc is not None else []
    ou = tstats(otr, model, OLDW)
    h = [tstats([t for t in otr if a <= t["entry_t"] < b], model, [(0, a, b)]) for a, b in ((OLD[1], OLD[2]), (OLD[2], OLD[3]))]
    h = [dict(x, ret=x.get("ret", 0)) for x in h]
    osims = rand_trades(otr, bars, model, OLDW, rng_for(fam, model, "old")) if otr else None
    opct = pct_of(ou["avg"], osims.mean(axis=1)) if otr else None
    ov = old_verdict(ou, h[0], h[1], tstats(otr, model, OLDW, True), opct, False)
    return {"fam": fam, "model": model, "kind": "trades", "chosen": chosen, "unseen": u, "stress": st, "pct": pct,
            "verdict": v, "fails": fails, "trades": tr, "sims": sims, "la": lookahead_count(tr),
            "old": {"combo": oc, "unseen": ou, "h": h, "pct": opct, "verdict": ov}}


def judge_periods(fam, by_combo, run, n_rand=N_RAND):
    """run(chosen, wins, stress, rng) → joined periods (rng: matched random)."""
    chosen = [pick_periods(by_combo, ls, s) for ls, s, _ in WINDOWS]
    ps = run(chosen, WINDOWS, False, None)
    u, st = pstats(ps, WINDOWS), pstats(run(chosen, WINDOWS, True, None), WINDOWS)
    rng = rng_for(fam, "wf")
    rand = [run(chosen, WINDOWS, False, rng) for _ in range(n_rand)]
    pct = pct_of(u.get("ret", 0), [pstats(r, WINDOWS).get("ret", 0) for r in rand]) if ps else None
    v, fails = pass_v2(u, st, pct, True)
    oc = pick_periods(by_combo, OLD[0], OLD[1])
    ow = [(OLD[0], OLD[1], OLD[3])]
    ops = run([oc], ow, False, None) if oc is not None else []
    ou = pstats(ops, ow)
    halves = [(0, OLD[1], OLD[2]), (0, OLD[2], OLD[3])]
    h = [{"n": 1, "ret": r} for r in (pstats(ops, halves).get("per_window") or [0, 0])]
    orng = rng_for(fam, "old")
    opct = pct_of(ou.get("ret", 0), [pstats(run([oc], ow, False, orng), ow).get("ret", 0) for _ in range(n_rand)]) if ops else None
    ov = old_verdict(ou, h[0], h[1], pstats(run([oc], ow, True, None), ow) if ops else {}, opct, True)
    return {"fam": fam, "kind": "periods", "chosen": chosen, "unseen": u, "stress": st, "pct": pct, "verdict": v,
            "fails": fails, "periods": ps, "rand": rand, "old": {"combo": oc, "unseen": ou, "h": h, "pct": opct, "verdict": ov}}


def fx_runner(mx, scheds):
    def run(chosen, wins, stress, rng):
        return join_periods(mx, scheds, chosen, wins, stress, fx.Matched(rng) if rng is not None else None)
    return run


def coin_set_tag(fam):
    return {"S1": "majors (PIT top 20)", "S5": "majors (PIT top 20)", "S6": "top50 (PIT)", "S8": "top50 (PIT)",
            "S9": "top50 (PIT; long 1–15, short 36–50)", "S10": "majors (PIT top 20, 5m)", "S11": "top50 (PIT, with perp)",
            "S12": "blend of the four"}[fam]


def run_s11(U):
    names = U.ever("top50")
    coins = [c for c in (s11_coin(U, n) for n in names) if c is not None]
    by = [[t for c in coins for t in s11_trades(c, p, U)] for p in GRID["S11"]]

    def pick(ls, s):
        best, bi = None, None
        for ci, trs in enumerate(by):
            lb = [t for t in trs if ls <= t["entry_t"] and t["exit_t"] <= s]
            if len(lb) >= 10:
                v = carry_book(lb, 1, [(ls, ls, s)])["apr"]
                if best is None or v > best:
                    best, bi = v, ci
        return bi
    chosen = [pick(ls, s) for ls, s, _ in WINDOWS]
    tr = [t for (ls, s, e), ci in zip(WINDOWS, chosen) if ci is not None for t in by[ci] if s <= t["entry_t"] < e]
    b1, b2, bs = carry_book(tr, 1, WINDOWS), carry_book(tr, 2, WINDOWS), carry_book(tr, 1, WINDOWS, stress=True)
    la = sum(1 for t in tr if not t["sig_t"] < t["entry_t"])  # signal settlement strictly before both legs fill
    rnd = carry_random(b1["taken"], coins, 1, WINDOWS, rng_for("S11", "wf"))
    pct = pct_of(b1["apr"], rnd)
    f = []
    if b1["apr"] < BENCH_APR + 3:
        f.append(f"net APR {b1['apr']:.1f}% < benchmark {BENCH_APR:.1f}% + 3 pp")
    if b1["above"] < 70:
        f.append("above benchmark in < 70% of windows")
    if b1["liq"]:
        f.append(f"{b1['liq']} liquidations at 1×")
    if pct < 95:
        f.append("does not beat 95% of random entry times")
    oc = pick(OLD[0], OLD[1])
    otr = [t for t in by[oc] if OLD[1] <= t["entry_t"] < OLD[3]] if oc is not None else []
    ob = carry_book(otr, 1, OLDW)
    return {"fam": "S11", "kind": "carry", "chosen": chosen, "b1": b1, "b2": b2, "stress": bs, "pct": pct,
            "verdict": "PASS v2" if not f else "REJECTED", "fails": f, "coins": len(coins), "la": la,
            "old": {"combo": oc, "apr": ob["apr"], "n": ob["n"],
                    "verdict": ("PASSED" if ob["apr"] >= BENCH_APR + 3 and ob["n"] >= 10 else "REJECTED")}}


def hand_s10(t, bars):
    a, cf, ok = bars[t["key"]]
    i = t["i"] - 1
    v = gk_vol(a[max(0, i - t["combo"]["w"]):i + 1])
    thr = t["combo"]["m"] * np.median(v[:-1])
    return (f"{t['coin']} {bt.iso(int(a[i, 0]))} 5m bar O {a[i, 1]:.6g} H {a[i, 2]:.6g} L {a[i, 3]:.6g} C {a[i, 4]:.6g}: "
            f"GK vol {v[-1]:.5f} > {thr:.5f} (= {t['combo']['m']} × median of previous {t['combo']['w']} bars) → buy at "
            f"next open {bt.iso(t['entry_t'])} {t['entry']:.6g}; sell 12 h later at the {bt.iso(t['exit_t'])} open "
            f"{t['exit']:.6g}: gross {t['gross']:+.3f}%, net F {rs.net(t, 'F'):+.3f}% (fees 2×0.05 + slippage 2×0.05 + "
            f"funding {t['fund']:+.4f}), net S {rs.net(t, 'S'):+.3f}%. Recomputed gross: {(t['exit'] / t['entry'] - 1) * 100:+.3f}%")


def hand_s9(g, mo, it):
    ci = g["names"].index(it["b"])
    pos = it["pos"]
    nz = np.nonzero(pos)[0]
    if not len(nz):
        return None
    k0 = int(nz[0])
    k1 = k0 + int(np.argmax(pos[k0:] == 0)) if (pos[k0:] == 0).any() else len(pos)
    j0, G, p = mo["j0"] + k0, g["G"], it["p"]
    c, prev = g["C"][ci, j0 - 1], g["C"][ci, j0 - 1 - p["L"]]
    e = g["O"][ci, j0]
    x = g["O"][ci, mo["j0"] + k1] if mo["j0"] + k1 < len(G) else np.nan
    s = 1 if it["side"] == "LONG" else -1
    return (f"{it['b']} {it['side']} {p}: close {bt.iso(int(G[j0 - 1]))} {c:.6g} vs {p['L']} bars earlier {prev:.6g} → "
            f"momentum {(c / prev - 1) * 100:+.2f}% (threshold {s * p['theta'] * 100:+.0f}%); entry at the "
            f"{bt.iso(int(G[j0]))} open {e:.6g}, first stop {c - s * S9P['alpha'] * g['A'][ci, j0 - 1]:.6g} "
            f"(close ∓ 2.5 × ATR {g['A'][ci, j0 - 1]:.6g}); exit at the {bt.iso(int(G[min(mo['j0'] + k1, len(G) - 1)]))} open "
            f"{x:.6g} ({'stop hit on a close' if k1 < len(pos) else 'still open at month end, carried'}); price move "
            f"{s * (x / e - 1) * 100:+.2f}% before costs")


def main():
    os.makedirs(OUT, exist_ok=True)
    U = Universe()
    R = {"audit": survivorship_audit(U)}
    log("audit", R["audit"])
    names50 = U.ever("top50")
    if "BTC" not in names50:
        names50 = ["BTC"] + names50
    coins = [c for c in (spot_coin(U, n) for n in names50) if c is not None]
    log("spot coins", len(coins))
    maj = set(U.ever("majors")) | {"BTC"}
    mcoins = [c for c in coins if c.base in maj]
    # S1-long (majors, futures model F and spot model S)
    by, bars = [], {}
    for p in GRID["S1"]:
        tr, bars = s1_trades(mcoins, U, p)
        by.append(tr)
    R["S1"] = {m: judge_trades("S1", by, bars, m) for m in ("F", "S")}
    log("S1", {m: (r["verdict"], r["unseen"].get("n")) for m, r in R["S1"].items()})
    # S5 / S6 / S8 on factors.py with point-in-time masks
    mx = fx.matrices(coins)
    for fam, kind in (("S5", "majors"), ("S6", "top50"), ("S8", "top50")):
        m = pit_mx(mx, U, kind)
        sc = fam_scheds(fam, m)
        R[fam] = judge_periods(fam, per_combo_periods(m, sc), fx_runner(m, sc))
        R[fam]["combos_steps"] = [(x[1], x[2]) for x in sc]
        log(fam, R[fam]["verdict"], R[fam]["unseen"].get("n"), R[fam]["unseen"].get("ret"))
    # S9 AdaptiveTrend
    g = grid6(U, [n for n in names50 if U.perp_of(n)])
    plan = s9_plan(U, g)
    R["S9"] = judge_periods("S9", [s9_eval(g, plan)], lambda ch, w, st, rng: [x for x in s9_eval(g, plan, st, rng)
                                                                        if any(s <= x["t"] < e for _, s, e in w)])
    allp = s9_eval(g, plan)
    R["S9"]["paper"] = pstats([x for x in allp if PAPER[0] <= x["t"] < PAPER[1]], [(0, *PAPER)])
    R["S9"]["post"] = pstats([x for x in allp if POST[0] <= x["t"] < POST[1]], [(0, *POST)])
    R["S9"]["hand"] = [h for mo in plan[30::25] for it in mo["items"][:1] if (h := hand_s9(g, mo, it))][:2]
    log("S9", R["S9"]["verdict"], R["S9"]["paper"].get("ret"), R["S9"]["post"].get("ret"))
    # S10 volatility-spike bounce (5m, majors)
    nb = len(GRID["S10"]) + len(GRID["S10-rare"])
    by10, bars10, fire = [[] for _ in range(nb)], {}, [[0, 0.0, 0.0] for _ in range(nb)]
    for b in sorted({n.split("#")[0] for n in U.ever("majors")}):
        a = ed.load("spot", b + "USDT", "5m")
        if a is None:
            continue
        (ft, fr, _), _ = funding_for(U, b, a[0, 0], a[-1, 0])
        cs = np.concatenate([[0.0], np.cumsum(fr)]) * 100
        cf = cs[np.searchsorted(ft, a[:, 0], side="right")]
        ok = pit_mask(U, "majors", [b], a[:, 0] + 300_000)[0]
        bars10[(b, "5m")] = (a, cf, ok)
        for ci, p in enumerate(GRID["S10"] + GRID["S10-rare"]):
            tr = s10_coin_trades(b, a, cf, ok, p)
            by10[ci] += tr
            elig_days = ok.sum() * 300_000 / DAY
            fire[ci][0] += int((s10_signal(a, p) & ok).sum())
            fire[ci][1] += elig_days
            fire[ci][2] += len(tr) * 12 / 24
    R["S10"] = {m: judge_trades("S10", by10[:len(GRID["S10"])], bars10, m) for m in ("F", "S")}
    R["S10"]["rare"] = {m: judge_trades("S10-rare", by10[len(GRID["S10"]):], bars10, m) for m in ("F", "S")}
    R["S10"]["fire"] = [{"combo": c, "per_coin_day": x[0] / max(x[1], 1), "long_share": x[2] / max(x[1], 1) * 100}
                        for c, x in zip(GRID["S10"] + GRID["S10-rare"], fire)]
    for m, r in ((m, R["S10"][m]) for m in ("F", "S")):
        days = pd.Series([rs.net(t, m) / SLOTS for t in r["trades"]],
                         index=[t["exit_t"] // DAY for t in r["trades"]]).groupby(level=0).sum()
        r["top10"] = (float(days.nlargest(10).sum()), float(days.sum()))
    R["S10"]["hand"] = [hand_s10(t, bars10) for t in R["S10"]["F"]["trades"][::max(1, len(R["S10"]["F"]["trades"]) // 2)][:2]]
    log("S10", {m: (R["S10"][m]["verdict"], R["S10"][m]["unseen"].get("n")) for m in ("F", "S")})
    # S11 selective carry
    R["S11"] = run_s11(U)
    log("S11", R["S11"]["verdict"], R["S11"]["b1"]["apr"])
    # S12 blend of walk-forward streams
    streams = {"S1": weekly(R["S1"]["F"]["trades"] and [dict(t, net=rs.net(t, "F")) for t in R["S1"]["F"]["trades"]], "trades"),
               "S5": weekly(R["S5"]["periods"], "p"), "S6": weekly(R["S6"]["periods"], "p"), "S8": weekly(R["S8"]["periods"], "p")}
    R["S12"] = judge_s12(R, streams)
    log("S12", R["S12"]["verdict"], R["S12"]["unseen"].get("ret"))
    R["secs"] = time.time() - T0
    rep = report(R, U)
    open(os.path.join(OUT, "edge_v2_report.md"), "w").write(rep)
    json.dump(slim(R), open(os.path.join(OUT, "edge_v2_results.json"), "w"), default=str, indent=1)
    log("report written")
    return R


def judge_s12(R, streams):
    s0 = WINDOWS[0][1]
    ps = blend(streams, s0, END)
    u = pstats(ps, WINDOWS)
    st_streams = {"S1": weekly([dict(t, net=rs.net(t, "F", True)) for t in R["S1"]["F"]["trades"]], "trades")}
    rand, orand = [], []
    sims = R["S1"]["F"]["sims"]
    tr = R["S1"]["F"]["trades"]
    for r in range(N_RAND):
        rs1 = weekly([dict(t, net=float(sims[r, k])) for k, t in enumerate(tr)], "trades")
        rb = blend({"S1": rs1, "S5": weekly(R["S5"]["rand"][r], "p"), "S6": weekly(R["S6"]["rand"][r], "p"),
                    "S8": weekly(R["S8"]["rand"][r], "p")}, s0, END)
        rand.append(pstats(rb, WINDOWS).get("ret", 0))
        orand.append(pstats([x for x in rb if OLD[1] <= x["t"] < OLD[3]], [(0, OLD[1], OLD[3])]).get("ret", 0))
    pct = pct_of(u.get("ret", 0), rand)
    # stress: S1 at stress costs; S5/S6/S8 stress streams are their stress joins (approximated by subtracting the
    # extra 0.5× fee + 0.10% slippage on each period's turnover)
    for fam in ("S5", "S6", "S8"):
        st_streams[fam] = weekly([dict(x, net=x["net"] - x["turn"] * ((rs.COST["S"]["long"] if fam != "S6" else 0.05) * 0.5
                                                                      + rs.STRESS_SLIP - rs.SLIP)) for x in R[fam]["periods"]], "p")
    st = pstats(blend(st_streams, s0, END), WINDOWS)
    v, fails = pass_v2(u, st, pct, True)
    cor = pd.DataFrame({k: pd.Series(v) for k, v in streams.items()}).fillna(0)
    cor = cor[cor.index >= s0].corr().round(2)
    old = [x for x in ps if OLD[1] <= x["t"] < OLD[3]]
    ou = pstats(old, [(0, OLD[1], OLD[3])])
    h = [{"n": 1, "ret": r} for r in pstats(old, [(0, OLD[1], OLD[2]), (0, OLD[2], OLD[3])]).get("per_window", [0, 0])]
    ov = old_verdict(ou, h[0], h[1], pstats([x for x in blend(st_streams, s0, END) if x["t"] >= OLD[1]], [(0, OLD[1], OLD[3])]),
                     pct_of(ou.get("ret", 0), orand) if old else None, True)
    hand = [f"week {bt.iso(x['t'])[:10]}: weights {', '.join(f'{k} {w:.2f}' for k, w in x['w'].items())}; stream returns "
            f"{', '.join(k + ' ' + format(streams[k].get(x['t'], 0), '+.2f') + '%' for k in streams)} → blend "
            f"{sum(w * streams[k].get(x['t'], 0) for k, w in x['w'].items()):+.3f}% (reported {x['net']:+.3f}%)"
            for x in (ps[len(ps) // 3], ps[2 * len(ps) // 3])] if ps else []
    return {"fam": "S12", "kind": "periods", "unseen": u, "stress": st, "pct": pct, "verdict": v, "fails": fails,
            "periods": ps, "corr": cor, "hand": hand, "old": {"unseen": ou, "h": h, "pct": pct_of(ou.get("ret", 0), orand) if old else None, "verdict": ov}}


def slim(R):
    out = {}
    for k, v in R.items():
        if isinstance(v, dict) and k in ("S1", "S10"):
            out[k] = {m: {kk: vv for kk, vv in r.items() if kk not in ("trades", "sims")} for m, r in v.items() if m in ("F", "S")}
            if k == "S10":
                out[k]["fire"] = v["fire"]
                out[k]["rare"] = {m: {kk: vv for kk, vv in r.items() if kk not in ("trades", "sims")} for m, r in v["rare"].items()}
        elif isinstance(v, dict) and k.startswith("S"):
            out[k] = {kk: vv for kk, vv in v.items() if kk not in ("rand", "periods", "corr")}
            if k == "S11":
                out[k]["b1"] = {kk: vv for kk, vv in v["b1"].items() if kk != "taken"}
                out[k]["b2"] = {kk: vv for kk, vv in v["b2"].items() if kk != "taken"}
                out[k]["stress"] = {kk: vv for kk, vv in v["stress"].items() if kk != "taken"}
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------- report
def f(x, d=2):
    return "—" if x is None else ("∞" if x == float("inf") else f"{x:.{d}f}")


def rows(R):
    """(family label, coin set, verdict, fails, joined-unseen text, % windows positive, random pct, result dict)."""
    out = []
    for fam in ("S1", "S5", "S6", "S8", "S9", "S10", "S11", "S12"):
        r = R[fam]
        if fam in ("S1", "S10"):
            for m in ("F", "S"):
                x = r[m]
                u = x["unseen"]
                out.append((f"{fam} {NAMES[fam]} — model {m}", coin_set_tag(fam), x["verdict"], x["fails"],
                            f"{u.get('n', 0)} trades, avg {f(u.get('avg'), 3)}%, PF {f(u.get('pf'))}, +{f(u.get('ret'), 1)}% on capital"
                            .replace("+-", "−"), f(u.get("win_pos"), 0), f(x["pct"], 1), x))
        elif fam == "S11":
            b = r["b1"]
            out.append((f"S11 {NAMES[fam]}", coin_set_tag(fam), r["verdict"], r["fails"],
                        f"{b['n']} trades, net APR {f(b['apr'], 1)}% at 1× (2×: {f(r['b2']['apr'], 1)}%)",
                        f(b["above"], 0) + " (above benchmark)", f(r["pct"], 1), r))
        else:
            u = r["unseen"]
            out.append((f"{fam} {NAMES[fam]}", coin_set_tag(fam), r["verdict"], r["fails"],
                        f"{u.get('n', 0)} rebalances, avg {f(u.get('avg'), 3)}%/period, PF {f(u.get('pf'))}, total {f(u.get('ret'), 1)}%",
                        f(u.get("win_pos"), 0), f(r["pct"], 1), r))
    return out


def report(R, U):
    rws = rows(R)
    passed = [x for x in rws if x[2] == "PASS v2"]
    best = min(rws, key=lambda x: (len(x[3]), -(x[7].get("pct") or 0)))  # fewest failing rules, then random pct
    L = ["# Phase 5 — Edge Hunt v2 (private)", ""]
    if passed:
        L += [f"1. PASSED the 6-year walk-forward test: {', '.join(x[0] for x in passed)}.",
              "2. It still needs live paper-watch confirmation before any real money.",
              f"3. Best joined-unseen result: {best[0]} — {best[4]}.",
              "4. Recommendation: paper-watch it quietly; consider a small real-money trial only after the readiness line turns green.",
              "5. Past results don't guarantee future results."]
    else:
        L += ["1. Nothing passed the 6-year walk-forward test (PASS v2) — no strategy is ready for real money.",
              f"2. Closest: {best[0]} — {best[4]}; it fails: {'; '.join(best[3]) or '—'}.",
              f"3. S9 AdaptiveTrend: paper period {f(R['S9']['paper'].get('ret'), 1)}%, after the paper {f(R['S9']['post'].get('ret'), 1)}%.",
              f"4. S11 carry earns {f(R['S11']['b1']['apr'], 1)}% a year vs {BENCH_APR:.1f}% in Binance Flexible Earn.",
              "5. Recommendation: keep watching on paper only; do not trade real money. Past results don't guarantee future results."]
    L += ["", f"_Run time {R['secs'] / 60:.1f} min; {len(WINDOWS)} unseen windows "
          f"({bt.iso(WINDOWS[0][1])[:10]} → {bt.iso(END)[:10]}); {N_RAND} matched random runs per test._", "",
          "## Verdicts (PASS v2, joined unseen windows after costs)", "",
          "| family | coin set | verdict | failing rules | joined-unseen result | % windows positive | random pct |",
          "|---|---|---|---|---|---|---|"]
    L += [f"| {a} | {b} | **{c}** | {'; '.join(d) or '—'} | {e} | {w} | {p} |" for a, b, c, d, e, w, p, _ in rws]
    L += ["", "PASS v2 needs all of: expectancy > 0, profit factor ≥ 1.2, ≥ 100 trades (portfolios ≥ 40 rebalances), "
          "positive in ≥ 70% of unseen windows, positive over the latest 12 months, beats ≥ 95% of matched random "
          "baselines, positive at 1.5× costs, max drawdown ≤ 35%. S11 uses REQ-525 instead.", "",
          "## Old Phase 3 rule side by side (REQ-512)", "",
          "Training 2024-11-27→2026-03-27 picks the setting; unseen 2026-03-27→2026-11-28; both 4-month halves.", "",
          "| family | old-rule verdict | failing rules | unseen | halves |", "|---|---|---|---|---|"]
    for a, *_, x in rws:
        o = x.get("old", {})
        if "verdict" in o and isinstance(o["verdict"], tuple):
            u = o["unseen"]
            L.append(f"| {a} | {o['verdict'][0]} | {'; '.join(o['verdict'][1]) or '—'} | {u.get('n', 0)} n, avg "
                     f"{f(u.get('avg'), 3)}, total {f(u.get('ret'), 1)}% | {f(o['h'][0].get('ret'), 1)}% / {f(o['h'][1].get('ret'), 1)}% |")
        elif o:
            L.append(f"| {a} | {o['verdict']} | — | {o.get('n', 0)} trades, APR {f(o.get('apr'), 1)}% | — |")
    L += ["", "## Per-window detail", ""]
    for a, *_, x in rws:
        u = x.get("unseen") or {}
        pw = u.get("per_window") if u else x.get("b1", {}).get("per_window")
        st = x.get("stress", {})
        L.append(f"- **{a}** — per window: {', '.join(f(v, 1) for v in (pw or []))}; latest 12 months "
                 f"{f(u.get('last12'), 1) if u else '—'}%; max drawdown {f(u.get('dd'), 1) if u else '—'}%; 1.5× costs: "
                 f"{('avg ' + f(st.get('avg'), 3)) if 'avg' in st else ('APR ' + f(st.get('apr'), 1) + '%')}; chosen setting per "
                 f"window: {', '.join('—' if c is None else str(c) for c in x.get('chosen', []))}")
    s9 = R["S9"]
    L += ["", "## S9 AdaptiveTrend", "", f"Source: {SOURCES['S9']}", "",
          f"- Paper period 2022-01→2024-12 (the paper reports +40.5%/year, Sharpe 2.41): our build {f(s9['paper'].get('ret'), 1)}% "
          f"in total over {s9['paper'].get('n', 0)} months (max drawdown {f(s9['paper'].get('dd'), 1)}%).",
          f"- After the paper 2025-01→2026-11 (unseen to the authors): {f(s9['post'].get('ret'), 1)}% over {s9['post'].get('n', 0)} months.",
          "- Differences from the paper:"] + [f"  - {d}" for d in DIFFS["S9"]] + ["- Hand-checked trades:"] + \
         [f"  - {h}" for h in s9["hand"]]
    s10 = R["S10"]
    L += ["", "## S10 Volatility-spike bounce", "", f"Source: {SOURCES['S10']} (kquant: +73 bp per trade before costs, ≈ +51 bp after).", ""]
    L += [f"- Model {m}: {s10[m]['unseen'].get('n', 0)} unseen trades, avg {f(s10[m]['unseen'].get('avg'), 3)}% per trade; "
          f"the 10 best days added {f(s10[m]['top10'][0], 1)}% of capital against a total of {f(s10[m]['top10'][1], 1)}% "
          f"(without them: {f(s10[m]['top10'][1] - s10[m]['top10'][0], 1)}%)." for m in ("F", "S")]
    L += ["- How often our spike fires (per coin per eligible day) and share of time the book is long: " +
          "; ".join(f"{x['combo']}: {x['per_coin_day']:.1f} signals/coin-day, long {x['long_share']:.0f}% of the time"
                    for x in s10["fire"]) + ". So the pre-registered thresholds fire many times a day and the book is "
          "long most of the time: as built, S10 is close to 'always long in 12 h blocks', NOT a test of a rare spike. "
          "This rejects our reading of kquant's rule, not necessarily kquant's own (unpublished) threshold.",
          "- Post-hoc, labelled check (added after review, NOT eligible for PASS v2): rarer spikes m ∈ {5, 8}, 7-day median — " +
          "; ".join(f"model {m}: {s10['rare'][m]['unseen'].get('n', 0)} trades, avg {f(s10['rare'][m]['unseen'].get('avg'), 3)}%, "
                    f"PF {f(s10['rare'][m]['unseen'].get('pf'))}, random pct {f(s10['rare'][m]['pct'], 1)}, would be "
                    f"{s10['rare'][m]['verdict']} ({'; '.join(s10['rare'][m]['fails']) or 'no failing rule'})" for m in ("F", "S")) + "."]
    L += ["- Differences:"] + [f"  - {d}" for d in DIFFS["S10"]] + ["- Hand-checked trades:"] + [f"  - {h}" for h in s10["hand"]]
    s11 = R["S11"]
    L += ["", "## S11 Selective funding carry", "", f"Source: {SOURCES['S11']}", "",
          f"- Benchmark: {BENCH_APR:.1f}% APR. {SOURCES['bench']}",
          f"- Net APR on unseen windows: {f(s11['b1']['apr'], 1)}% at 1× short leg, {f(s11['b2']['apr'], 1)}% at 2×; "
          f"1.5× costs: {f(s11['stress']['apr'], 1)}%; above benchmark in {f(s11['b1']['above'], 0)}% of windows.",
          f"- Liquidations: {s11['b1']['liq']} at 1×, {s11['b2']['liq']} at 2×. Random entry-time percentile {f(s11['pct'], 1)}. "
          f"Coins with spot + perp + funding: {s11['coins']}.",
          "- Differences:"] + [f"  - {d}" for d in DIFFS["S11"]] + ["- Hand-checked trades:"]
    for t in s11["b1"]["taken"][::max(1, len(s11["b1"]["taken"]) // 2)][:2]:
        n1, _ = carry_net(t, 1)
        L.append(f"  - {t['coin']}: signal at settlement {bt.iso(t['sig_t'])}; buy spot {t['s_in']:.6g} and short perp "
                 f"{t['p_in']:.6g} at the {bt.iso(t['entry_t'])} open; close at the {bt.iso(t['exit_t'])} open (spot {t['s_out']:.6g}, "
                 f"perp {t['p_out']:.6g}); funding received {t['fund']:+.3f}%; spot {(t['s_out'] / t['s_in'] - 1) * 100:+.3f}%, "
                 f"perp {-(t['p_out'] / t['p_in'] - 1) * 100:+.3f}%, fees 0.30% + slippage 0.20% → {n1:+.3f}% on capital (÷2 at 1×)")
    s12 = R["S12"]
    L += ["", "## S12 Near-miss blend", "", f"Source: {SOURCES['S12']}", "", "Weekly correlation between the streams (unseen):", "",
          "| | " + " | ".join(s12["corr"].columns) + " |", "|---" * (len(s12["corr"].columns) + 1) + "|"]
    L += [f"| {i} | " + " | ".join(f"{v:.2f}" for v in row) + " |" for i, row in zip(s12["corr"].index, s12["corr"].to_numpy())]
    L += ["- Differences:"] + [f"  - {d}" for d in DIFFS["S12"]] + ["- Hand-checked weeks:"] + [f"  - {h}" for h in s12["hand"]]
    a = R["audit"]
    la = sum(R[k][m]["la"] for k in ("S1", "S10") for m in ("F", "S")) + R["S11"]["la"]
    L += ["", "## Data and honesty checks", "",
          f"- Spot 1h for {len(ed.symbols('spot'))} USDT pairs that ever had archives (later-delisted included; stablecoins, "
          f"leveraged tokens, tokenized stocks/gold excluded), USDⓈ-M perp 1h + funding for {len(ed.symbols('perp'))} perps, "
          f"5m spot for {len({n.split('#')[0] for n in U.ever('majors')})} ever-majors (only the months they were majors + one month "
          "before). 2020-01 (or first month) → 2026-11-27. 4h, 6h and 1d candles are built from the 1h archives "
          "(UTC-aligned, complete groups only). November 2026 comes from daily archives; November funding from Hyperliquid "
          "(monthly funding archive not yet published).",
          f"- Point-in-time sets: {len(U.sets)} months; coins later delisted: {a['delisted']}, of which {a['delisted_in_sets']} "
          f"appear in sets they qualified for; missing from sets they qualified for (independent recomputation): {a['missing']} "
          f"(Q-51 must be 0). Coins selected before they traded 30 days: {a['pit_violations']} (REQ-541).",
          f"- Re-used tickers and redenominations split into separate coins (gap > 30 days, or > 2 days with a > 3× price "
          f"jump): {', '.join(split_names(U)) or 'none'}. Their later lives get no perp/funding (S1 model F charges them no "
          "funding; S9/S11 skip them) and S10 skips their months as majors. Random baselines only use holds without a data gap. "
          f"Missing archive months are treated as no data: {missing_summary(U)}.",
          "- Trade families size each trade at 1/20 of capital (one slot per majors coin), summed without compounding; a "
          "drawdown above 100% means the account would have been wiped out.",
          f"- No-look-ahead: {la} trade violations (S1, S10, S11; Q-50 must be 0); S9 asserts each month's selection uses "
          "only earlier bars and its entries use the previous close; every look-back selection uses only trades "
          "finished before its window (asserted); S9's monthly selection uses only bars before the month (asserted).",
          "", "## Parked ideas (not built)", ""] + [f"- {a_}: {b_}." for a_, b_ in PARKED]
    combos = [(fam, c) for fam, g in GRID.items() for c in g]
    L += ["", f"## Appendix — every setting tried ({len(combos)} in total; Q-52 limit 100)", ""]
    L += [f"- {fam}: {c}" for fam, c in combos]
    L += ["", "## Recommendation", "",
          ("Paper-watch the passed candidate quietly and wait for the readiness line before any small real-money trial."
           if passed else "Nothing is ready for real money. Keep the four near misses on quiet paper-watch and do not trade them."),
          "Past results don't guarantee future results."]
    assert len(combos) <= 100
    return "\n".join(L) + "\n"


def split_names(U):
    return sorted({b for s in U.sets.values() for b in s["top50"] if "#" in b} |
                  {n for s in ed.symbols("spot") for n, _ in ed.spot_segments(ed.base_of(s)) if "#" in n})


def missing_summary(U):
    n, ex = 0, []
    for s in ed.symbols("spot")[:]:
        mm = ed.missing_months("spot", s)
        n += len(mm)
        if mm and len(ex) < 5:
            ex.append(f"{s} {', '.join(mm[:3])}")
    return f"{n} spot coin-months (e.g. {'; '.join(ex) or 'none'})"


if __name__ == "__main__":
    main()
