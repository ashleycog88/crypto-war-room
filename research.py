#!/usr/bin/env python3
"""Phase 3 strategy research on 1h/4h/1d candles (pre-registered grids, walk-forward, random baseline, stress).

One command:  python research.py      (outputs → private_reports/research/, gitignored; cache → backtest_data/)
"""
import argparse, io, json, os, sys, time, zipfile, zlib

import numpy as np
import pandas as pd

import backtest as bt
import war_room_bot as wr

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "private_reports", "research")
H = 3_600_000
TF = {"1h": H, "4h": 4 * H, "1d": 24 * H}
START, SPLIT, HALF, END = bt.ms("2024-11-27"), bt.ms("2026-03-27"), bt.ms("2026-07-27"), bt.ms("2026-11-27")
WARM = 250 * 24 * H  # 250 days of warm-up history before START (EMA200 on 1d needs it; also for 4h EMA200)
MAJORS = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "LINK", "AVAX", "LTC", "DOT", "TRX", "BCH", "NEAR",
          "ATOM", "UNI", "FIL", "ETC", "APT", "ARB"]
COST = {"F": {"long": 0.05, "short": 0.05, "fund_long": True}, "S": {"long": 0.10, "short": 0.05, "fund_long": False}}
SLIP, STRESS_FEE, STRESS_SLIP = 0.05, 1.5, 0.15
N_RANDOM, N_RANDOM_S5 = 200, 2000
SEED = 20261127


def rng_for(*key):
    """Separate, fixed random stream per test, so one result never depends on how many draws another used."""
    return np.random.default_rng([SEED, zlib.crc32(json.dumps(key).encode())])

# Pre-registered grids (REQ-158..162). Every combination is listed in the appendix.
GRID = {
    "S1": [dict(n=n, exit=e, btc=b) for n in (20, 55) for e in ("chandelier", "channel") for b in (False, True)],
    "S2": [dict(rsi=r, exit=e) for r in (35, 40) for e in ("2R", "3R", "trail")],
    "S3": [dict(tf=tf, grade=g) for tf in ("1h", "4h") for g in ("A", "B")],
    "S4": [dict(hold=h, tp=t) for h in (24, 48) for t in (1.5, 2.5)],
    "S5": [dict(k=k, reb=r) for k in (3, 5) for r in ("weekly", "daily")],
}
NAMES = {"S1": "Trend breakout (4h Donchian)", "S2": "Trend pullback (1h RSI, 4h trend)",
         "S3": "v10 score on bigger timeframes", "S4": "Funding extremes fade (1h, futures)",
         "S5": "Momentum rotation (daily, long-only)"}
S3_HOLD_BARS = 48  # pre-registered: max hold 48 signal bars (1h → 2 days, 4h → 8 days)


def log(*a):
    print(f"[{time.time() - T0:7.1f}s]", *a, flush=True)


T0 = time.time()


# ---------------------------------------------------------------- data
def candles(base):
    a = bt.load_klines(f"{base}/USDT", "1h", START - WARM, END)
    return a if len(a) else None


def resample(a, tf_ms):
    """1h rows → bigger UTC-aligned candles; only complete groups are kept."""
    g = (a[:, 0] // tf_ms).astype(np.int64)
    df = pd.DataFrame(a[:, :6], columns=["t", "o", "h", "l", "c", "v"])
    df["g"] = g
    agg = df.groupby("g").agg(t=("t", "first"), o=("o", "first"), h=("h", "max"), l=("l", "min"), c=("c", "last"),
                              v=("v", "sum"), n=("t", "size"))
    agg = agg[agg["n"] == tf_ms // H]
    agg["t"] = agg.index.to_numpy() * tf_ms
    return agg[["t", "o", "h", "l", "c", "v"]].to_numpy(float)


def funding(base):
    """[(settle_ms, rate)] from Binance futures archives; Hyperliquid (hourly) or OKX as fallbacks."""
    s, out = f"{base}USDT", []  # (settle_ms, rate paid, 8h-equivalent rate)
    for kind, p in bt.kline_periods(START - 40 * 24 * H, END):
        if kind != "monthly":
            continue
        raw = bt.fetch(f"{bt.VISION}/futures/um/monthly/fundingRate/{s}/{s}-fundingRate-{p}.zip",
                       os.path.join(bt.DATA, "funding", s, f"{p}.zip"))
        if raw:
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                df = pd.read_csv(io.BytesIO(z.read(z.namelist()[0])))
            r, hrs = df.iloc[:, -1].astype(float), df.iloc[:, 1].astype(float).clip(lower=1)
            out += list(zip(df.iloc[:, 0].astype(np.int64), r, r * 8 / hrs))
    src = "binance" if len(out) >= 100 else "none"
    out = out if len(out) >= 100 else []
    last = max((t for t, _, _ in out), default=START - 40 * 24 * H)
    if last < END - 9 * H:  # archives cover complete months only: fill the rest (or everything) from Hyperliquid/OKX
        uni = bt.hl_universe()
        extra = [(t, r / 8, r) for t, r in bt.hl_funding(uni[base], last + 1, END)] if base in uni else []
        if not extra:
            ok = bt.okx_funding(base, last + 1, END)
            dt = float(np.median(np.diff([t for t, _ in ok])) / H) if len(ok) >= 2 else 8.0
            extra = [(t, r * dt / 8, r) for t, r in ok]
        if extra:
            src = (src + "+" if src != "none" else "") + ("hyperliquid" if base in uni else "okx")
        out += [x for x in extra if x[0] > last]
    out = sorted(set(out))
    return (np.array([x[0] for x in out], np.int64), np.array([x[1] for x in out], float),
            np.array([x[2] for x in out], float)), src


class Coin:
    def __init__(self, base, rows1h, fund, fsrc):
        self.base, self.fsrc = base, fsrc
        self.ft, self.fr, self.fn = fund
        self.bars = {"1h": rows1h, "4h": resample(rows1h, TF["4h"]), "1d": resample(rows1h, TF["1d"])}
        self.first = int(rows1h[0, 0])

    def cum_funding(self, tf):
        """cf[i] = sum of rates settled at or before bar i's open (so a hold from open i to open j pays cf[j]-cf[i])."""
        t = self.bars[tf][:, 0]
        cs = np.concatenate([[0.0], np.cumsum(self.fr)])
        return cs[np.searchsorted(self.ft, t, side="right")] * 100  # in %


# ---------------------------------------------------------------- indicators (research-only helpers)
def ema(x, n):
    return pd.Series(x).ewm(span=n, adjust=False).mean().to_numpy()


def atr(a, n=14):
    h, l, c = a[:, 2], a[:, 3], a[:, 4]
    pc = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc)))
    return pd.Series(tr).ewm(alpha=1 / n, adjust=False).mean().to_numpy()


def rsi(c, n=14):
    d = np.diff(c, prepend=c[0])
    g = pd.Series(np.clip(d, 0, None)).ewm(alpha=1 / n, adjust=False).mean()
    lo = pd.Series(np.clip(-d, 0, None)).ewm(alpha=1 / n, adjust=False).mean().replace(0, np.nan)
    return (100 - 100 / (1 + g / lo)).fillna(100).to_numpy()


def slow_index(fast, slow, tf_fast, tf_slow):
    """For each fast bar: index of the last slow bar CLOSED at or before the fast bar's close (−1 if none)."""
    return np.searchsorted(slow[:, 0] + tf_slow, fast[:, 0] + tf_fast, side="right") - 1


# ---------------------------------------------------------------- trade engine
def run_trade(a, i, side, stop, tgt=None, trail=None, chan=None, max_bars=10**9, partial=None):
    """Enter at open of bar i (signal was bar i-1). Stop-first inside a bar; stops fill at the stop price.
    trail=(atr_array, mult) chandelier from the best price since entry, updated after each bar closes.
    chan=lowest-low/highest-high arrays: exit next open when close breaks it. partial=(tp1, tp2): half at tp1,
    stop to breakeven, rest at tp2. Returns (exit_index, exit_price, gross_pct, reason)."""
    s = 1 if side == "LONG" else -1
    e, n = a[i, 1], len(a)
    best, half, got = e, False, 0.0
    for j in range(i, min(n, i + max_bars)):
        o, h, l, c = a[j, 1:5]
        if (s == 1 and l <= stop) or (s == -1 and h >= stop):
            px = stop
            g = s * (px / e - 1) * 100
            return j, px, (got + g / 2) if half else g, "stop"
        if partial and not half and ((s == 1 and h >= partial[0]) or (s == -1 and l <= partial[0])):
            half, got, stop = True, s * (partial[0] / e - 1) * 100 / 2, e
            tgt = partial[1]
        if tgt is not None and ((s == 1 and h >= tgt) or (s == -1 and l <= tgt)):
            g = s * (tgt / e - 1) * 100
            return j, tgt, (got + g / 2) if half else g, "target"
        best = max(best, h) if s == 1 else min(best, l)
        if trail is not None:
            ts = best - s * trail[1] * trail[0][j]
            stop = max(stop, ts) if s == 1 else min(stop, ts)
        if chan is not None and j + 1 < n and ((s == 1 and c < chan[0][j]) or (s == -1 and c > chan[1][j])):
            px = a[j + 1, 1]
            return j + 1, px, s * (px / e - 1) * 100, "channel"
    j = min(n, i + max_bars) - 1
    g = s * (a[j, 4] / e - 1) * 100
    return j, a[j, 4], (got + g / 2) if half else g, "time"


def mk_trade(fam, combo, coin, tf, side, sig_i, used_t, a, stop, res, cf):
    j, px, gross, why = res
    i = sig_i + 1
    end_t, fj = (a[j, 0], j) if why == "channel" else (a[j, 0] + TF[tf], j + 1)  # channel exits at bar j's open
    return {"fam": fam, "combo": combo, "coin": coin.base, "tf": tf, "side": side, "sig_close": int(a[sig_i, 0] + TF[tf]),
            "used_t": int(used_t), "entry_t": int(a[i, 0]), "entry": float(a[i, 1]), "exit_t": int(end_t),
            "exit": float(px), "i": i, "j": j, "risk": abs(a[i, 1] - stop) / a[i, 1] * 100, "gross": gross,
            "fund": float(cf[min(fj, len(cf) - 1)] - cf[i]), "why": why, "hold_h": (end_t - a[i, 0]) / H}


def net(t, model, stress=False):
    c = COST[model]
    side = "long" if t["side"] == "LONG" else "short"
    fee = c[side] * (STRESS_FEE if stress else 1)
    slip = STRESS_SLIP if stress else SLIP
    fund = t["fund"] * (1 if t["side"] == "LONG" else -1) if (side == "short" or c["fund_long"]) else 0.0
    return t["gross"] - 2 * fee - 2 * slip - fund


def walk(sig_long, sig_short, a, make):
    """Scan signals in time order, one open trade per coin; make(i, side) → trade or None."""
    out, free = [], 0
    for i in np.nonzero(sig_long | sig_short)[0]:
        if i < free or i + 1 >= len(a) or a[i + 1, 0] < START or a[i + 1, 0] >= END:
            continue
        t = make(i, "LONG" if sig_long[i] else "SHORT")
        if t:
            out.append(t)
            free = t["j"] + 1
    return out


# ---------------------------------------------------------------- strategy families
def s1(coin, p, btc):
    L, S, make = s1_setup(coin, p, btc)
    return walk(L, S, coin.bars["4h"], make)


def s1_setup(coin, p, btc):
    """S1 signals and the trade maker (also used by the live paper-watch, so both decide identically)."""
    a = coin.bars["4h"]
    c, n = a[:, 4], p["n"]
    hh = pd.Series(a[:, 2]).rolling(n).max().shift(1).to_numpy()
    ll = pd.Series(a[:, 3]).rolling(n).min().shift(1).to_numpy()
    lo2 = pd.Series(a[:, 3]).rolling(n // 2).min().shift(1).to_numpy()  # prior N/2 candles (not the current one)
    hi2 = pd.Series(a[:, 2]).rolling(n // 2).max().shift(1).to_numpy()
    at, cf = atr(a), coin.cum_funding("4h")
    L, S = c > hh, c < ll
    if p["btc"]:
        bi = np.searchsorted(btc[:, 0], a[:, 0], side="right") - 1
        b_up = (btc[:, 4] > ema(btc[:, 4], 200))[np.clip(bi, 0, None)] & (bi >= 0)
        L, S = L & b_up, S & ~b_up & (bi >= 0)
    used = a[:, 0] + TF["4h"]
    if p["btc"]:
        used = np.maximum(used, btc[np.clip(bi, 0, None), 0] + TF["4h"])

    def make(i, side):
        s = 1 if side == "LONG" else -1
        stop = a[i + 1, 1] - s * 2 * at[i]
        kw = {"trail": (at, 3)} if p["exit"] == "chandelier" else {"chan": (lo2, hi2)}
        return mk_trade("S1", p, coin, "4h", side, i, used[i], a, stop,
                        run_trade(a, i + 1, side, stop, max_bars=S1_MAX_BARS, **kw), cf)
    return L, S, make


def s2(coin, p):
    a, a4 = coin.bars["1h"], coin.bars["4h"]
    e50, e200 = ema(a4[:, 4], 50), ema(a4[:, 4], 200)
    k = slow_index(a, a4, TF["1h"], TF["4h"])
    ok = k >= 200
    up = np.where(ok, e50[np.clip(k, 0, None)] > e200[np.clip(k, 0, None)], False)
    dn = np.where(ok, e50[np.clip(k, 0, None)] < e200[np.clip(k, 0, None)], False)
    r, at, cf = rsi(a[:, 4]), atr(a), coin.cum_funding("1h")
    rp = np.concatenate([[50], r[:-1]])
    thr = p["rsi"]
    L = up & (rp < thr) & (r >= thr)
    S = dn & (rp > 100 - thr) & (r <= 100 - thr)

    def make(i, side):
        s = 1 if side == "LONG" else -1
        e = a[i + 1, 1]
        stop = e - s * 1.5 * at[i]
        tgt = None if p["exit"] == "trail" else e + s * float(p["exit"][0]) * abs(e - stop)
        tr = (at, 2) if p["exit"] == "trail" else None
        return mk_trade("S2", p, coin, "1h", side, i, a[i, 0] + TF["1h"], a, stop,
                        run_trade(a, i + 1, side, stop, tgt=tgt, trail=tr, max_bars=168), cf)
    return walk(L, S, a, make)


def s3_scores(coin, tf):
    """Live wr.add_indicators + wr.score_sides on (signal tf, trend tf) = (1h, 4h) or (4h, 1d); OB = 0 (no history)."""
    slow_tf = {"1h": "4h", "4h": "1d"}[tf]
    cols = ["ts", "open", "high", "low", "close", "volume"]
    f = wr.add_indicators(pd.DataFrame(coin.bars[tf][:, :6], columns=cols))
    s = wr.add_indicators(pd.DataFrame(coin.bars[slow_tf][:, :6], columns=cols))
    k = slow_index(coin.bars[tf], coin.bars[slow_tf], TF[tf], TF[slow_tf])
    fr, sr = f.to_dict("records"), s.to_dict("records")
    ls, ss = np.zeros(len(fr), int), np.zeros(len(fr), int)
    for i in range(60, len(fr)):
        if k[i] < 60:
            continue
        ls[i], ss[i], _, _ = wr.score_sides(fr[i], sr[k[i]], 0.0)
    return ls, ss, f["atr"].to_numpy(), k, coin.bars[slow_tf]


def s3(coin, p, cache):
    key = (coin.base, p["tf"])
    if key not in cache:
        cache[key] = s3_scores(coin, p["tf"])
    ls, ss, at, k, slow = cache[key]
    a, tf, cf = coin.bars[p["tf"]], p["tf"], coin.cum_funding(p["tf"])
    best = np.maximum(ls, ss)
    g = np.array([wr.grade_for(x) for x in best], dtype=object)
    sel = g == p["grade"]
    L, S = sel & (ls > ss), sel & (ss > ls)

    def make(i, side):
        e = a[i + 1, 1]
        stop, tp1, tp2 = wr.make_levels(side, e, at[i])
        used = max(a[i, 0] + TF[tf], slow[k[i], 0] + TF[{"1h": "4h", "4h": "1d"}[tf]])
        return mk_trade("S3", p, coin, tf, side, i, used, a, stop,
                        run_trade(a, i + 1, side, stop, partial=(tp1, tp2), max_bars=S3_HOLD_BARS), cf)
    return walk(L, S, a, make)


def s4(coin, p):
    if coin.fsrc == "none":
        return []
    a = coin.bars["1h"]
    close_t = a[:, 0] + H
    fi = np.searchsorted(coin.ft, close_t, side="right") - 1  # last settled rate at the signal close
    norm = coin.fn
    at, cf = atr(a), coin.cum_funding("1h")
    hi24 = pd.Series(a[:, 2]).rolling(24).max().to_numpy()
    lo24 = pd.Series(a[:, 3]).rolling(24).min().to_numpy()
    L, S = np.zeros(len(a), bool), np.zeros(len(a), bool)
    lo_t = np.searchsorted(coin.ft, close_t - 30 * 24 * H, side="right")
    for i in np.nonzero(fi >= 0)[0]:
        if close_t[i] < START - H or i < 24:
            continue
        w = norm[lo_t[i]:fi[i] + 1]
        if len(w) < 60:
            continue
        f = norm[fi[i]]
        if f >= np.quantile(w, 0.95) and a[i, 4] >= hi24[i]:
            S[i] = True
        elif f <= np.quantile(w, 0.05) and a[i, 4] <= lo24[i]:
            L[i] = True

    def make(i, side):
        s = 1 if side == "LONG" else -1
        e = a[i + 1, 1]
        stop = e - s * 2 * at[i]
        tgt = e + s * p["tp"] * abs(e - stop)
        t = mk_trade("S4", p, coin, "1h", side, i, max(close_t[i], coin.ft[fi[i]]), a, stop,
                     run_trade(a, i + 1, side, stop, tgt=tgt, max_bars=p["hold"]), cf)
        return t
    return walk(L, S, a, make)


S1_MAX_BARS = 360


def s5_matrix(coins):
    """Daily opens/closes/cum-funding aligned on BTC's days (coins × days), BTC daily bars and its 50-day SMA."""
    btc = [c for c in coins if c.base == "BTC"][0].bars["1d"]
    days = btc[:, 0]
    sma50 = pd.Series(btc[:, 4]).rolling(50).mean().to_numpy()
    O = np.full((len(coins), len(days)), np.nan)
    C, FC = O.copy(), O.copy()
    for ci, c in enumerate(coins):
        d = c.bars["1d"]
        ix = np.searchsorted(days, d[:, 0])
        m = (ix < len(days)) & (days[np.clip(ix, 0, len(days) - 1)] == d[:, 0])
        O[ci, ix[m]], C[ci, ix[m]], FC[ci, ix[m]] = d[m, 1], d[m, 4], c.cum_funding("1d")[m]
    return days, btc, sma50, O, C, FC


def s5_top(C, d, ok, btc, sma50, k):
    """Coins to hold after daily close d: top-k by 30-day return when BTC closes above its 50-day SMA, else cash."""
    mom = C[:, d] / C[:, d - 30] - 1
    valid = np.nonzero(np.isfinite(mom) & ok)[0]
    if not btc[d, 4] > sma50[d] or len(valid) < k:
        return set(), valid
    return {int(x) for x in valid[np.argsort(-mom[valid])][:k]}, valid


def s5(coins, p, model="S", stress=False, pick=None):
    """Daily momentum rotation: periods between rebalances; returns list of period dicts (net % of the portfolio)."""
    days, btc, sma50, O, C, FC = s5_matrix(coins)
    pays = COST[model]["fund_long"]
    fee = COST[model]["long"] * (STRESS_FEE if stress else 1) + (STRESS_SLIP if stress else SLIP)
    step = 7 if p["reb"] == "weekly" else 1
    reb = [d for d in range(31, len(days) - step - 1) if START <= days[d + 1] < END and (d - 31) % step == 0]
    out, held, prev_top = [], {}, set()
    for d in reb:
        nxt = d + step  # held from the open after rebalance close d to the open after the next rebalance close
        top, valid = s5_top(C, d, np.isfinite(O[:, d + 1]) & np.isfinite(O[:, nxt + 1]), btc, sma50, p["k"])
        if pick is None or not top:
            new = {x: 1 / p["k"] for x in top}
        else:  # random picks change as many coins as the strategy does (same turnover)
            n_change = p["k"] if not held else len(top - prev_top)
            new = {int(x): 1 / p["k"] for x in pick(valid, p["k"], [x for x in held if x in valid], n_change)}
        prev_top = top
        turn = sum(abs(new.get(x, 0) - held.get(x, 0)) for x in set(new) | set(held))
        gross = sum(w * ((O[x, nxt + 1] / O[x, d + 1] - 1) * 100
                         - (np.nan_to_num(FC[x, nxt + 1] - FC[x, d + 1]) if pays else 0)) for x, w in new.items())
        out.append({"t": int(days[d + 1]), "t_end": int(days[nxt + 1]), "sig_close": int(days[d] + TF["1d"]),
                    "net": gross - turn * fee, "in": bool(new), "coins": [coins[x].base for x in new]})
        held = new
    return out


# ---------------------------------------------------------------- metrics
def stats(trades, model, stress=False, period=None):
    tr = [t for t in trades if period is None or (period[0] <= t["entry_t"] < period[1]
                                                  and (period[1] != SPLIT or t["exit_t"] <= SPLIT))]
    if not tr:
        return {"n": 0}
    nets = np.array([net(t, model, stress) for t in tr])
    R = nets / np.array([max(t["risk"], 1e-6) for t in tr])
    eq = np.cumsum(R)
    dd = float(np.max(np.maximum.accumulate(np.concatenate([[0], eq]))[1:] - eq)) if len(eq) else 0.0
    streak = cur = 0
    for x in nets:
        cur = cur + 1 if x <= 0 else 0
        streak = max(streak, cur)
    weeks = ((period[1] - period[0]) if period else (END - START)) / (7 * 24 * H)
    pos, neg = nets[nets > 0].sum(), -nets[nets <= 0].sum()
    return {"n": len(tr), "per_week": len(tr) / weeks, "win": float((nets > 0).mean() * 100), "avg": float(nets.mean()),
            "expR": float(R.mean()), "pf": float(pos / neg) if neg > 0 else float("inf"), "ddR": dd, "dd_pct": dd,
            "ret_pct": float(eq[-1]), "hold_h": float(np.mean([t["hold_h"] for t in tr])), "streak": streak}


def stats5(periods, lo, hi):
    ps = [x for x in periods if lo <= x["t"] < hi and (hi != SPLIT or x["t_end"] <= SPLIT)]
    if not ps:
        return {"n": 0}
    r = np.array([x["net"] for x in ps])
    eq = np.cumprod(1 + r / 100)
    dd = float(np.max(1 - eq / np.maximum.accumulate(eq)) * 100)
    pos, neg = r[r > 0].sum(), -r[r < 0].sum()
    streak = cur = 0
    for x in r:
        cur = cur + 1 if x < 0 else 0
        streak = max(streak, cur)
    return {"n": len(ps), "per_week": len(ps) / ((hi - lo) / (7 * 24 * H)), "win": float((r > 0).mean() * 100),
            "avg": float(r.mean()), "expR": None, "pf": float(pos / neg) if neg > 0 else float("inf"), "ddR": None,
            "dd_pct": dd, "ret_pct": float((eq[-1] - 1) * 100), "hold_h": float(np.mean([(x["t_end"] - x["t"]) / H for x in ps])),
            "streak": streak, "in_mkt": float(np.mean([x["in"] for x in ps]) * 100)}


def random_pct(trades, coins, model, rng):
    """Percentile of the strategy's avg net % among N_RANDOM random-entry runs (same coins, sides, holds, count)."""
    tr = [t for t in trades if SPLIT <= t["entry_t"] < END]
    if not tr:
        return None, []
    by = {c.base: c for c in coins}
    sims = np.zeros((N_RANDOM, len(tr)))
    for k, t in enumerate(tr):
        a = by[t["coin"]].bars[t["tf"]]
        cf = by[t["coin"]].cum_funding(t["tf"])
        hb = t["j"] - t["i"] if t["why"] == "channel" else t["j"] + 1 - t["i"]  # channel exits at bar j's open
        lo, hi = np.searchsorted(a[:, 0], SPLIT), np.searchsorted(a[:, 0], END) - hb
        if hi <= lo:
            sims[:, k] = net(t, model)
            continue
        ii = rng.integers(lo, hi, N_RANDOM)
        jj = ii + hb - 1
        s = 1 if t["side"] == "LONG" else -1
        gross = s * (a[jj, 4] / a[ii, 1] - 1) * 100
        fund = cf[np.minimum(jj + 1, len(cf) - 1)] - cf[ii]
        side = "long" if s == 1 else "short"
        fund = fund * s if (side == "short" or COST[model]["fund_long"]) else 0 * fund
        sims[:, k] = gross - 2 * COST[model][side] - 2 * SLIP - fund
    real = np.mean([net(t, model) for t in tr])
    means = sims.mean(axis=1)
    return float((means < real).mean() * 100), means.tolist()


def random_pct5(coins, p, real_ret, rng):
    def pick(valid, k, prev, n_change):  # drop random held coins; never re-buy one just held
        keep = list(rng.permutation(prev)[:max(0, k - n_change)]) if prev else []
        rest = [x for x in valid if x not in prev]
        if len(rest) < k - len(keep):
            rest = [x for x in valid if x not in keep]
        return keep + list(rng.choice(rest, k - len(keep), replace=False))
    rets = []
    for _ in range(N_RANDOM_S5):
        ps = s5(coins, p, pick=pick)
        rets.append(stats5(ps, SPLIT, END).get("ret_pct", 0))
    return float((np.array(rets) < real_ret).mean() * 100)


def lookahead(trades):
    return sum(1 for t in trades if not (t["used_t"] <= t["entry_t"] and t["sig_close"] <= t["entry_t"]))


# ---------------------------------------------------------------- hand checks (raw candles, explicit loops)
def hand_check(t, coin):
    a = coin.bars[t["tf"]]
    i, j = t["i"], t["j"]
    assert a[i, 0] == t["entry_t"] and a[i, 1] == t["entry"], "entry is not the next candle's open"
    assert a[i - 1, 0] + TF[t["tf"]] <= t["entry_t"], "signal candle not closed before entry"
    lo, hi = min(a[i:j + 1, 3].min(), a[j, 1]), max(a[i:j + 1, 2].max(), a[j, 1])
    assert lo - 1e-9 <= t["exit"] <= hi + 1e-9, "exit price outside the traded range"
    end = a[j, 0] if t["why"] == "channel" else a[min(j + 1, len(a) - 1), 0]
    paid = sum(r for ts, r in zip(coin.ft, coin.fr) if a[i, 0] < ts <= end) * 100
    assert abs(paid - t["fund"]) < 1e-6, "funding mismatch"
    s = 1 if t["side"] == "LONG" else -1
    f_net = t["gross"] - 2 * 0.05 - 2 * SLIP - s * paid
    assert abs(f_net - net(t, "F")) < 1e-9
    return {"coin": t["coin"], "side": t["side"], "entry_time": bt.iso(t["entry_t"]), "entry": t["entry"],
            "exit_time": bt.iso(t["exit_t"]), "exit": t["exit"], "why": t["why"], "gross_pct": round(t["gross"], 4),
            "fees_pct": 0.10, "slippage_pct": 0.10, "funding_pct": round(s * paid, 4), "net_F_pct": round(f_net, 4),
            "funding_settlements_held": int(sum(1 for ts in coin.ft if a[i, 0] < ts <= end))}


# ---------------------------------------------------------------- run
def load_coins(bases, label):
    out, cov = [], {}
    for b in bases:
        rows = candles(b)
        if rows is None or len(rows) < 24 * 200:
            cov[b] = "no candles"
            continue
        fund, src = funding(b)
        c = Coin(b, rows, fund, src)
        full = c.first <= START - 30 * 24 * H
        cov[b] = {"candles_from": bt.iso(c.first), "full_history": bool(full), "funding": src,
                  "funding_n": int(len(c.ft)), "funding_last": bt.iso(int(c.ft[-1])) if len(c.ft) else "—"}
        out.append(c)
    log(f"{label}: {len(out)} coins loaded")
    return out, cov


def family_trades(fam, p, coins, cache):
    btc = next((c.bars["4h"] for c in coins if c.base == "BTC"), None)
    out = []
    for c in coins:
        out += {"S1": lambda: s1(c, p, btc), "S2": lambda: s2(c, p), "S3": lambda: s3(c, p, cache),
                "S4": lambda: s4(c, p)}[fam]()
    return out


def evaluate_set(coins, label):
    TRAIN, UNSEEN, H1, H2 = (START, SPLIT), (SPLIT, END), (SPLIT, HALF), (HALF, END)
    res = {"families": {}, "appendix": [], "label": label}
    cache, hand = {}, {}
    for fam in ("S1", "S2", "S3", "S4"):
        allt = {}
        for p in GRID[fam]:
            tr = family_trades(fam, p, coins, cache)
            assert lookahead(tr) == 0, f"look-ahead in {fam} {p}"
            allt[json.dumps(p)] = tr
            for side in ("BOTH", "LONG", "SHORT"):
                st = [t for t in tr if side == "BOTH" or t["side"] == side]
                res["appendix"].append({"fam": fam, "combo": p, "side": side, "train": stats(st, "F", period=TRAIN),
                                        "unseen": stats(st, "F", period=UNSEEN)})
            log(f"{label} {fam} {p}: {len(tr)} trades")
        for side in ("BOTH", "LONG", "SHORT"):
            cands = [(stats([t for t in tr if side == "BOTH" or t["side"] == side], "F", period=TRAIN), k)
                     for k, tr in allt.items()]
            cands = [(s, k) for s, k in cands if s["n"] >= 30]
            if not cands:
                res["families"][f"{fam}-{side}"] = {"verdict": "REJECTED", "fails": ["< 30 training trades"]}
                continue
            _, k = max(cands, key=lambda x: x[0]["expR"])
            tr = [t for t in allt[k] if side == "BOTH" or t["side"] == side]
            r = {"combo": json.loads(k)}
            for m in ("F", "S"):
                r[m] = {nm: stats(tr, m, period=pr) for nm, pr in
                        (("train", TRAIN), ("unseen", UNSEEN), ("half1", H1), ("half2", H2))}
            r["stress"] = {nm: stats(tr, "F", True, pr) for nm, pr in (("train", TRAIN), ("unseen", UNSEEN))}
            r["random_pct"], _ = random_pct(tr, coins, "F", rng_for(label, fam, side, k))
            u, s_ = r["F"]["unseen"], r["stress"]["unseen"]
            r["verdict"], r["fails"] = verdict(u, r["F"]["half1"], r["F"]["half2"], r["random_pct"], s_, 50)
            res["families"][f"{fam}-{side}"] = r
        by = {c.base: c for c in coins}
        best = res["families"].get(f"{fam}-BOTH", {})
        if "combo" in best:
            tr = allt[json.dumps(best["combo"])]
            a_ = next((t for t in tr if t["fund"] != 0), tr[0])
            b_ = next((t for t in tr if t["side"] != a_["side"]), next((t for t in tr if t is not a_), a_))
            hand[fam] = [hand_check(t, by[t["coin"]]) for t in ([a_, b_] if b_ is not a_ else [a_])]
    allp = {}
    for p in GRID["S5"]:
        ps = s5(coins, p)
        allp[json.dumps(p)] = ps
        res["appendix"].append({"fam": "S5", "combo": p, "side": "LONG", "train": stats5(ps, *TRAIN),
                                "unseen": stats5(ps, *UNSEEN)})
    k = max(allp, key=lambda k: (stats5(allp[k], *TRAIN).get("n", 0) >= 10, stats5(allp[k], *TRAIN).get("ret_pct", -1e9)))
    p = json.loads(k)
    r = {"combo": p}
    for m in ("S", "F"):
        ps = s5(coins, p, model=m)
        r[m] = {nm: stats5(ps, *pr) for nm, pr in (("train", TRAIN), ("unseen", UNSEEN), ("half1", H1), ("half2", H2))}
    r["stress"] = {nm: stats5(s5(coins, p, "S", True), *pr) for nm, pr in (("train", TRAIN), ("unseen", UNSEEN))}
    r["random_pct"] = random_pct5(coins, p, r["S"]["unseen"]["ret_pct"], rng_for(label, "S5", k))
    assert all(x["sig_close"] <= x["t"] for x in allp[k])
    u = dict(r["S"]["unseen"], expR=r["S"]["unseen"]["avg"])
    st_ = dict(r["stress"]["unseen"], expR=r["stress"]["unseen"]["avg"])
    r["verdict"], r["fails"] = verdict(u, dict(r["S"]["half1"], ret_pct=r["S"]["half1"]["ret_pct"]),
                                       r["S"]["half2"], r["random_pct"], st_, 20)
    res["families"]["S5-LONG"] = r
    ex = allp[k]
    by = {c.base: c.bars["1d"] for c in coins}

    def opn(b, t):
        a = by[b]
        return float(a[np.searchsorted(a[:, 0], t), 1])
    hand["S5"] = []
    for x in [y for y in ex if y["in"]][:2]:
        g = np.mean([(opn(b, x["t_end"]) / opn(b, x["t"]) - 1) * 100 for b in x["coins"]])
        hand["S5"].append({"rebalance": bt.iso(x["t"]), "until": bt.iso(x["t_end"]), "coins": x["coins"],
                           "open_prices": {b: [opn(b, x["t"]), opn(b, x["t_end"])] for b in x["coins"]},
                           "gross_pct_from_raw_opens": round(float(g), 3), "net_S_pct": round(x["net"], 3)})
    res["hand"] = hand
    return res


def verdict(u, h1, h2, pct, stress, min_n):
    fails = []
    if u.get("n", 0) < min_n:
        fails.append(f"< {min_n} unseen trades")
    if not u.get("n") or u["expR"] is None or u["expR"] <= 0:
        fails.append("expectancy ≤ 0")
    if u.get("n") and u["pf"] < 1.2:
        fails.append("profit factor < 1.2")
    if not h1.get("n") or h1["ret_pct"] <= 0 or not h2.get("n") or h2["ret_pct"] <= 0:
        fails.append("not positive in both 4-month halves")
    if pct is None or pct < 95:
        fails.append("does not beat 95% of random runs")
    if not stress.get("n") or stress["expR"] is None or stress["expR"] <= 0:
        fails.append("not positive in the 1.5× cost stress case")
    return ("PASSED" if not fails else "REJECTED"), fails


# ---------------------------------------------------------------- report
def f(x, d=2):
    return "—" if x is None else ("∞" if x == float("inf") else f"{x:.{d}f}")


def row(name, s):
    if not s.get("n"):
        return f"| {name} | 0 | | | | | | | | | | |"
    return (f"| {name} | {s['n']} | {f(s['per_week'], 1)} | {f(s['win'], 1)} | {f(s['avg'], 3)} | {f(s['expR'], 3)} | "
            f"{f(s['pf'])} | {f(s['ddR'], 1)} | {f(s['dd_pct'], 1)} | {f(s['ret_pct'], 1)} | {f(s['hold_h'], 1)} | {s['streak']} |")


HDR = ("| period | trades | /week | win % | avg net % | exp R | PF | max DD R | max DD % (1% risk) | total % (1% risk) | "
       "avg hold h | longest losing streak |\n|---|---|---|---|---|---|---|---|---|---|---|---|")


def report(sets, cov, secs):
    passed = [(lbl, k, r) for lbl, s in sets.items() for k, r in s["families"].items() if r.get("verdict") == "PASSED"]
    L = ["# Phase 3 strategy research report (PRIVATE — gitignored)", ""]
    if passed:
        def unseen(k, r):
            return r["S"]["unseen"] if k.startswith("S5") else r["F"]["unseen"]
        lbl, k, r = best_pass = max(passed, key=lambda x: unseen(x[1], x[2])["ret_pct"])
        u = unseen(k, r)
        tr = r["S"]["train"] if k.startswith("S5") else r["F"]["train"]
        what = (f"{u['n']} daily rebalances, {u['ret_pct']:+.1f}% portfolio return after Spot costs (in the market "
                f"{u['in_mkt']:.0f}% of days), max drop {u['dd_pct']:.0f}%" if k.startswith("S5") else
                f"{u['n']} trades, {u['ret_pct']:+.1f}% at 1% risk per trade after Futures costs, win rate {u['win']:.0f}%")
        L += [f"**{len(passed)} setting passed the rules, but it is a weak, regime-dependent candidate.** "
              f"{NAMES[k[:2]]} on {lbl} ({json.dumps(r['combo'])}).",
              f"- It would alert about {u['per_week']:.1f} times per week.",
              f"- Unseen 8 months: {what}, profit factor {f(u['pf'])}.",
              f"- Warning: in the 16 training months the same rules lost {tr['ret_pct']:+.1f}% (max drop {tr['dd_pct']:.0f}%)"
              "; it only made money when the market rose. Paper-test only — no real money.",
              "- Past results don't guarantee future results."]
    else:
        L += ["**NO strategy passed.** None of the 5 pre-registered families made money on the unseen 8 months under all "
              "the honesty rules (after costs, both halves, beats random, survives 1.5× costs).",
              "- Best unseen result is listed below, but it failed at least one rule, so it should not be traded.",
              "- Recommendation: none passed — don't trade. Keep paper-tracking and keep recording data.",
              "- Past results don't guarantee future results."]
    L += ["", f"_Run time {secs / 60:.1f} min. Training 2024-11-27 → 2026-03-27, unseen 2026-03-27 → 2026-11-27 "
               "(halves split 2026-07-27). Selection on training only (best expectancy R, ≥30 trades; S5: best total "
               "training return, ≥10 rebalances; training trades must also exit before the split). Verdict on unseen with model F (S5: model S, spot). Look-ahead "
               "violations: 0 (asserted for every trade)._", "", "## Verdicts", "",
          "| coin set | family / side | chosen setting | verdict | failing rules | unseen trades | unseen exp R (F) | "
          "unseen total % | random pct | stress exp |", "|---|---|---|---|---|---|---|---|---|---|"]
    for lbl, s in sets.items():
        for k, r in s["families"].items():
            m = "S" if k.startswith("S5") else "F"
            u = r.get(m, {}).get("unseen", {})
            st = r.get("stress", {}).get("unseen", {})
            ex = u.get("expR") if not k.startswith("S5") else u.get("avg")
            sx = st.get("expR") if not k.startswith("S5") else st.get("avg")
            L.append(f"| {lbl} | {NAMES[k[:2]]} — {k[3:]} | {json.dumps(r.get('combo', '—'))} | **{r['verdict']}** | "
                     f"{'; '.join(r['fails'])} | {u.get('n', 0)} | {f(ex, 3)} | {f(u.get('ret_pct'), 1)} | "
                     f"{f(r.get('random_pct'), 0)} | {f(sx, 3)} |")
    for lbl, s in sets.items():
        L += ["", f"## Details — {lbl}"]
        for k, r in s["families"].items():
            if "combo" not in r:
                continue
            L += ["", f"### {NAMES[k[:2]]} — {k[3:]} — {json.dumps(r['combo'])} — {r['verdict']}"]
            if k.startswith("S5"):
                L.append("_S5 rows are rebalance periods; % columns are portfolio % (fully invested), exp R / DD R n/a._")
            for m in ("F", "S"):
                L += ["", f"Fee model {m}:", "", HDR] + [row(nm, r[m][nm]) for nm in ("train", "unseen", "half1", "half2")]
            L += ["", "Stress (1.5× fees, 0.15% slippage):", "", HDR] + [row(nm, r["stress"][nm]) for nm in ("train", "unseen")]
    L += ["", "## Coin-set comparison (unseen, chosen settings)", "", "| family / side | majors exp (F) | top-50 exp (F) |",
          "|---|---|---|"]
    keys = list(next(iter(sets.values()))["families"])
    for k in keys:
        vals = []
        for s in sets.values():
            r = s["families"].get(k, {})
            m = "S" if k.startswith("S5") else "F"
            u = r.get(m, {}).get("unseen", {})
            vals.append(f(u.get("avg") if k.startswith("S5") else u.get("expR"), 3))
        L.append(f"| {k} | " + " | ".join(vals) + " |")
    L += ["", "## Recommendation", ""]
    if passed:
        lbl, k, r = best_pass
        L.append(f"Paper-alert {NAMES[k[:2]]} ({k[3:]}, {json.dumps(r['combo'])}) on {lbl} for 4 weeks in Phase 4, "
                 "with no real money." if not k.startswith("S5") else
                 f"Weak candidate only: paper-alert {NAMES[k[:2]]} ({k[3:]}, {json.dumps(r['combo'])}) on {lbl} for 4 "
                 "weeks in Phase 4, with no real money. Rule in plain words: every day after the daily close, if BTC "
                 "closes above its 50-day average, hold the 3 majors with the biggest 30-day gain in equal parts "
                 "(spot buy); otherwise hold cash. It lost money in the 16 training months, and the same rule on the "
                 "top-50 coins failed, so expect it to be fragile.")
    else:
        L.append("**None passed — don't trade.** No family is good enough to paper-alert as a candidate edge. "
                 "Keep the live bot in quiet paper mode and let the recorder collect the new edge data (wallets, "
                 "liquidations, news) for Phase 3+ research.")
    L += ["", "## Known biases and limits",
          "- Survivorship: coin lists are today's; dead coins are missing (results look better than reality).",
          "- Stops fill exactly at the stop price (gaps through stops are not punished); stop-first in the same candle.",
          "- S3 uses order-book points = 0 (no history), so A-GRADE needs every other signal; indicators are computed on "
          "the full history (live uses a rolling window — EMA warm-up differences are negligible after 250 days).",
          "- S3 funding is charged on the full size even after half is closed at TP1 (slightly conservative for longs).",
          "- S5 trades at the next daily open; turnover cost = changed weight × (fee + slippage); model F also charges "
          "perpetual funding on the held coins. S5 random runs change as many coins per rebalance as the strategy "
          "(same turnover and cash days), so fees don't bias the comparison.",
          "- Funding: Binance monthly archives; the incomplete last month (and coins without archives) come from "
          "Hyperliquid (hourly) or OKX. Percentiles in S4 use 8h-equivalent rates. In the Oct-2026 overlap Hyperliquid "
          "BTC funding ran about 1.8× Binance, so Nov-2026 funding costs are a conservative approximation.",
          f"- Random baselines: {N_RANDOM} runs per S1–S4 setting, {N_RANDOM_S5} per S5 setting (S5 sits near the "
          "95% line, so it needs the larger sample); each test has its own fixed random stream.",
          "- The grid has 26 settings (S1–S4 each tested as both sides, LONG-only and SHORT-only); with that many tries, one 'pass' could be luck. That's why the "
          "random baseline and both halves are required.", "", "## Data coverage", "",
          "| coin set | coin | candles from | full 2y | funding source | funding rows | last funding |", "|---|---|---|---|---|---|---|"]
    for lbl, cv in cov.items():
        for b, v in cv.items():
            L.append(f"| {lbl} | {b} | " + (" | ".join(str(v[x]) for x in ("candles_from", "full_history", "funding",
                                                                             "funding_n", "funding_last")) if isinstance(v, dict) else v + " | | | | ") + " |")
    L += ["", "## Hand-checked trades (raw candles, explicit funding loop)", ""]
    for lbl, s in sets.items():
        for fam, hs in s["hand"].items():
            for h in hs:
                L.append(f"- {lbl} {fam}: `{json.dumps(h)}`")
    L += ["", "## Appendix — every setting tested (fee model F; S5 model S)", "",
          "| coin set | family | setting | side | train n | train exp | train PF | unseen n | unseen exp | unseen PF |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for lbl, s in sets.items():
        for a in s["appendix"]:
            tr, un = a["train"], a["unseen"]
            ex = lambda d: d.get("avg") if a["fam"] == "S5" else d.get("expR")
            L.append(f"| {lbl} | {a['fam']} | {json.dumps(a['combo'])} | {a['side']} | {tr.get('n', 0)} | {f(ex(tr), 3)} | "
                     f"{f(tr.get('pf'))} | {un.get('n', 0)} | {f(ex(un), 3)} | {f(un.get('pf'))} |")
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sets", default="majors,top50")
    a = ap.parse_args(argv)
    os.makedirs(OUT, exist_ok=True)
    sets, cov = {}, {}
    if "majors" in a.sets:
        coins, cv = load_coins(MAJORS, "majors")
        coins = [c for c in coins if cv[c.base]["full_history"]]
        cov["majors"] = cv
        sets["majors"] = evaluate_set(coins, "majors")
    if "top50" in a.sets:
        top = [c.split("/")[0] for c in bt.build_coins(50)["coins"]]
        coins, cv = load_coins(top, "top50")
        cov["top50"] = cv
        sets["top50"] = evaluate_set(coins, "top50")
    md = report(sets, cov, time.time() - T0)
    with open(os.path.join(OUT, "research_report.md"), "w") as fh:
        fh.write(md)
    with open(os.path.join(OUT, "research_results.json"), "w") as fh:
        json.dump({"sets": sets, "coverage": cov}, fh, default=float)
    log("done →", os.path.join(OUT, "research_report.md"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
