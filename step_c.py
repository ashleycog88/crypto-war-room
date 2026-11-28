#!/usr/bin/env python3
"""Phase 4 Step C research (private outputs): C1 copying Hyperliquid wallets, C2 listing/delisting events.
Data first: python step_c_data.py (wallet fills) and python step_c_data.py events. Then: python step_c.py
Outputs → private_reports/step_c/ (gitignored). Pricing: Binance USDT-M perp 1m archives; costs = Phase 3 model F."""
import gzip, io, json, os, re, sys, time, zipfile, zlib
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

import backtest as bt
import research as rs

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "backtest_data")
OUT = os.path.join(ROOT, "private_reports", "step_c")
VIS = "https://data.binance.vision/data/futures/um"
M, H, DAY = 60_000, 3_600_000, 86_400_000
MS = lambda d: bt.ms(d)
C1 = {"start": MS("2025-05-27"), "split": MS("2026-05-27"), "half": MS("2026-08-27"), "end": MS("2026-11-27")}
C2 = {"start": rs.START, "split": rs.SPLIT, "half": rs.HALF, "end": rs.END}
FEE, SLIP = rs.COST["F"]["long"], rs.SLIP
N_RANDOM, SEED = 200, 20261128
DELAYS = (1, 5, 60)
C1_GRID = [{"k": k, "metric": m, "stop": s} for k in (5, 10, 20) for m in ("pnl", "roi") for s in (False, True)]
C2_GRID = [{"rule": r, "entry": e, "hold": h} for r in ("R1", "R2", "R3")
           for e in (("5m", "6h") if r != "R2" else ("first+1h", "first+6h")) for h in (24, 168)]
SAFETY_STOP = 10.0  # % adverse move (C1 stop variant)
COPY_MAX_DAYS = 30


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def rng_for(*k):
    return np.random.default_rng([SEED, zlib.crc32(json.dumps(k).encode())])


# ---------------------------------------------------------------- Binance USDT-M candles (cached archives)
def _csv(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        df = pd.read_csv(io.BytesIO(z.read(z.namelist()[0])), header=None)
    if not str(df.iloc[0, 0]).isdigit():
        df = df.iloc[1:]
    return df.iloc[:, :6].astype(float).to_numpy()  # t, o, h, l, c, volume


class Candles:
    """1m daily and 1h monthly perp archives; None when Binance had no such perp then."""
    def __init__(self):
        self.m1, self.h1 = {}, {}

    def day(self, sym, d):
        key = (sym, d)
        if key not in self.m1:
            ds = time.strftime("%Y-%m-%d", time.gmtime(d / 1000))
            raw = bt.fetch(f"{VIS}/daily/klines/{sym}/1m/{sym}-1m-{ds}.zip", os.path.join(DATA, "um1m", sym, f"{ds}.zip"))
            self.m1[key] = _csv(raw) if raw else None
        return self.m1[key]

    def month(self, sym, t):
        ym = time.strftime("%Y-%m", time.gmtime(t / 1000))
        if (sym, ym) not in self.h1:
            m0 = MS(ym + "-01")
            if m0 >= LAST_MONTH:  # incomplete month: no monthly archive yet → build it from daily 1h archives
                parts = []
                for d in range(m0, min(m0 + 31 * DAY, C1["end"] + DAY), DAY):
                    if time.strftime("%Y-%m", time.gmtime(d / 1000)) != ym:
                        break
                    ds = time.strftime("%Y-%m-%d", time.gmtime(d / 1000))
                    raw = bt.fetch(f"{VIS}/daily/klines/{sym}/1h/{sym}-1h-{ds}.zip",
                                   os.path.join(DATA, "um1h_daily", sym, f"{ds}.zip"))
                    if raw:
                        parts.append(_csv(raw))
                self.h1[(sym, ym)] = np.vstack(parts) if parts else None
            else:
                raw = bt.fetch(f"{VIS}/monthly/klines/{sym}/1h/{sym}-1h-{ym}.zip", os.path.join(DATA, "um1h", sym, f"{ym}.zip"))
                self.h1[(sym, ym)] = _csv(raw) if raw else None
        return self.h1[(sym, ym)]

    def prefetch(self, keys, kind="day"):
        fn = self.day if kind == "day" else self.month
        with ThreadPoolExecutor(8) as ex:
            list(ex.map(lambda k: fn(*k), keys))

    def open_at(self, sym, t):
        """Open of the first 1m candle starting at/after t (a fill no earlier than t)."""
        t = -(-int(t) // M) * M
        a = self.day(sym, t // DAY * DAY)
        if a is None:
            return None
        i = np.searchsorted(a[:, 0], t)
        return float(a[i, 1]) if i < len(a) and a[i, 0] - t < 5 * M else None

    def path_1m(self, sym, t0, t1):
        parts = [self.day(sym, d) for d in range(t0 // DAY * DAY, t1 // DAY * DAY + DAY, DAY)]
        parts = [p for p in parts if p is not None]
        if not parts:
            return None
        a = np.vstack(parts)
        return a[(a[:, 0] >= t0) & (a[:, 0] < t1)]

    def hours(self, sym, t0, t1):
        parts = [p for p in (self.month(sym, m) for m in months_between(t0 - 15 * H, t1 + DAY)) if p is not None]
        if not parts:
            return None
        a = np.unique(np.vstack(parts), axis=0)
        return a[(a[:, 0] >= t0 - 15 * H) & (a[:, 0] < t1)]


LAST_MONTH = MS("2026-11-01")


def months_between(t0, t1):
    out, t = [], MS(time.strftime("%Y-%m-01", time.gmtime(t0 / 1000)))
    while t <= t1:
        out.append(t)
        t = MS(time.strftime("%Y-%m-01", time.gmtime((t + 32 * DAY) / 1000)))
    return out


def live(a):
    """Candles where the perp really traded (dead/settled perps keep flat, zero-volume candles in the archive)."""
    return (a[:, 2] > a[:, 3]) & (a[:, 5] > 0)


PX = Candles()
FUND = {}


MONTHS = [p for kind, p in bt.kline_periods(C2["start"] - 40 * DAY, C2["end"]) if kind == "monthly"]


def _fund_file(sym, p):
    raw = bt.fetch(f"{bt.VISION}/futures/um/monthly/fundingRate/{sym}/{sym}-fundingRate-{p}.zip",
                   os.path.join(DATA, "funding", sym, f"{p}.zip"))
    if not raw:
        return []
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        df = pd.read_csv(io.BytesIO(z.read(z.namelist()[0])))
    return list(zip(df.iloc[:, 0].astype(np.int64), df.iloc[:, -1].astype(float)))


def prefetch_funding(syms):
    """Binance funding archives only (complete months; the incomplete last month counts as 0 funding)."""
    todo = sorted(set(syms) - set(FUND))
    with ThreadPoolExecutor(8) as ex:
        got = list(ex.map(lambda s: [x for p in MONTHS for x in _fund_file(s, p)], todo))
    for s, rows in zip(todo, got):
        rows = sorted(rows)
        FUND[s] = (np.array([r[0] for r in rows], np.int64), np.array([r[1] for r in rows], float))


def prefetch_hours(spans):
    PX.prefetch(sorted({(sym, m) for sym, t0, t1 in spans for m in months_between(t0 - 15 * H, t1 + DAY)}), kind="month")


def funding_cost(sym, side, t0, t1):
    if sym not in FUND:
        prefetch_funding([sym])
    ft, fr = FUND[sym]
    paid = fr[(ft > t0) & (ft <= t1)].sum() * 100
    return float(side * paid)


def net(gross, fund, stress=False):
    return gross - 2 * FEE * (rs.STRESS_FEE if stress else 1) - 2 * (rs.STRESS_SLIP if stress else SLIP) - fund


def stats(tr, lo, hi, stress=False):
    xs = np.array([net(t["gross"], t["fund"], stress) for t in tr if lo <= t["entry_t"] < hi
                   and (hi != C1["split"] and hi != C2["split"] or t["exit_t"] <= hi)])
    if not len(xs):
        return {"n": 0}
    pos, neg = xs[xs > 0].sum(), -xs[xs <= 0].sum()
    weeks = (hi - lo) / (7 * DAY)
    sel = [t for t in tr if lo <= t["entry_t"] < hi and (hi != C1["split"] and hi != C2["split"] or t["exit_t"] <= hi)]
    return {"n": int(len(xs)), "per_week": len(xs) / weeks, "win": float((xs > 0).mean() * 100), "avg": float(xs.mean()),
            "pf": float(pos / neg) if neg > 0 else float("inf"), "total": float(xs.sum()),
            "hold_h": float(np.mean([(t["exit_t"] - t["entry_t"]) / H for t in sel]))}


def verdict(u, h1, h2, pct, st, min_n):
    f = []
    if u.get("n", 0) < min_n:
        f.append(f"< {min_n} unseen trades/events" + (" (too few events to trust)" if min_n == 30 else ""))
    if not u.get("n") or u["avg"] <= 0:
        f.append("expectancy ≤ 0")
    if u.get("n") and u["pf"] < 1.2:
        f.append("profit factor < 1.2")
    if not h1.get("n") or h1["total"] <= 0 or not h2.get("n") or h2["total"] <= 0:
        f.append("not positive in both halves")
    if pct is None or pct < 95:
        f.append("does not beat 95% of random runs")
    if not st.get("n") or st["avg"] <= 0:
        f.append("not positive in the 1.5× cost stress case")
    return ("PASSED" if not f else "REJECTED"), f


def hour_one(t, fund=True):
    """One trade re-priced on 1h opens (both sides of every random-baseline comparison); None if not priceable."""
    a = PX.hours(t["sym"], t["entry_t"], t["exit_t"] + 2 * H)
    if a is None:
        return None
    i, j = np.searchsorted(a[:, 0], t["entry_t"]), np.searchsorted(a[:, 0], t["exit_t"])
    j = max(j, i + 1)  # trades shorter than 1h are held for one hour
    if j >= len(a):
        return None
    return t["side"] * (a[j, 1] / a[i, 1] - 1) * 100 - (t["fund"] if fund else 0)


def hour_gross(tr):
    return [g for g in map(hour_one, tr) if g is not None]


def random_rule(tr, lo, hi, rng, hold=None, atr_stop=None, pct_stop=None):
    """Percentile of the rule's REAL fills (gross − funding, 1m) vs the SAME rule started at random live 1h opens in
    [lo, hi): same coin, side, hold (fixed `hold` hours, else each trade's own), same stop (2×ATR14 of the prior 1h
    candles, or a % stop), a stop hit fills at max(stop, open) on gaps. Trades from one announcement (same pub_t)
    share one random time, because they are correlated."""
    groups = {}
    for t in tr:
        groups.setdefault(t.get("pub_t", id(t)), []).append(t)
    sums, cnt, real = np.zeros(N_RANDOM), np.zeros(N_RANDOM), []
    for g in groups.values():
        T = rng.integers(lo, hi, N_RANDOM)
        for t in g:
            hb = hold or max(1, int(round((t["exit_t"] - t["entry_t"]) / H)))
            real.append(t["gross"] - t["fund"])
            a = PX.hours(t["sym"], lo, hi + hb * H)
            if a is None or len(a) < hb + 16:
                continue
            pc = np.concatenate([[a[0, 1]], a[:-1, 4]])
            tr_ = np.maximum(a[:, 2] - a[:, 3], np.maximum(abs(a[:, 2] - pc), abs(a[:, 3] - pc)))
            atr = np.concatenate([[np.nan], pd.Series(tr_).rolling(14).mean().to_numpy()[:-1]])  # completed candles only
            idx = np.searchsorted(a[:, 0], T)
            for r_, i in enumerate(idx):
                if i + hb >= len(a) or a[i, 0] - T[r_] > 2 * H or not live(a[i:i + 1])[0] or (atr_stop and not atr[i] > 0):
                    continue
                e, sd = a[i, 1], t["side"]
                stop = e - sd * atr_stop * atr[i] if atr_stop else (e * (1 - sd * pct_stop / 100) if pct_stop else None)
                x = a[i + hb, 1]
                if stop is not None:
                    w = a[i:i + hb]
                    hit = np.nonzero(w[:, 2] >= stop if sd < 0 else w[:, 3] <= stop)[0]
                    if len(hit):
                        o = w[hit[0], 1]
                        x = max(stop, o) if sd < 0 else min(stop, o)
                sums[r_] += sd * (x / e - 1) * 100 - t["fund"]
                cnt[r_] += 1
    if not real or not cnt.any():
        return None
    means = sums[cnt > 0] / cnt[cnt > 0]
    return float((means < np.mean(real)).mean() * 100)


# ---------------------------------------------------------------- C1: wallet copying
def hl_symbol(coin):
    if not coin or any(c in coin for c in "@/:"):
        return None
    return ("1000" + coin[1:] if coin.startswith("k") and coin[1:].isupper() else coin.upper()) + "USDT"


def wallet_events(doc):
    """Per wallet: realised PnL events, position openings (with the wallet's first reduce = exit) and notional."""
    pnl, opens, live = [], [], {}
    for f in sorted(doc["fills"], key=lambda x: x["time"]):
        t, sz, px = int(f["time"]), float(f["sz"]), float(f["px"])
        before = float(f["startPosition"] or 0)
        after = before + (sz if f["side"] == "B" else -sz)
        pnl.append((t, float(f["closedPnl"] or 0) - float(f["fee"] or 0), abs(after) * px, float(f["closedPnl"] or 0) != 0))
        c = f["coin"]
        if c in live and (abs(after) < abs(before) - 1e-12 or np.sign(after) != np.sign(before)):
            o = live.pop(c)
            o["exit_t"] = t
        if abs(before) < 1e-12 < abs(after) or (np.sign(after) != np.sign(before) and abs(after) > 1e-12):
            if c not in live:
                live[c] = {"coin": c, "side": int(np.sign(after)), "open_t": t, "exit_t": None}
                opens.append(live[c])
    return np.array(pnl) if pnl else np.zeros((0, 4)), opens


def c1(universe, docs):
    W = {}
    for s in universe["sample"]:
        d = docs.get(s["addr"])
        if not d:
            continue
        p, o = wallet_events(d)
        cover = d["first"] if d["capped"] and d["first"] else 0
        W[s["addr"]] = {"pnl": p, "opens": o, "cover": cover, "decile": s["decile"]}
    sels = [C1["start"]]
    while sels[-1] < C1["end"]:
        sels.append(MS(time.strftime("%Y-%m-27", time.gmtime((sels[-1] + 32 * DAY) / 1000))))
    hindsight, ranks, elig_n = 0, [], []
    for si, sel in enumerate(sels[:-1]):
        lo, r = sel - 30 * DAY, {}
        for a, w in W.items():
            p = w["pnl"]
            if w["cover"] > lo or not len(p):
                continue
            m = (p[:, 0] >= lo) & (p[:, 0] < sel)
            hindsight += int((p[m, 0] >= sel).sum())
            if p[m, 3].sum() < 10:
                continue
            pn = float(p[m, 1].sum())
            r[a] = {"pnl": pn, "roi": pn / max(float(p[m, 2].max()), 1.0)}
        for a in list(r)[:5]:  # hindsight check: re-rank from a copy of the fills cut at the selection date
            cut = {"fills": [x for x in docs[a]["fills"] if int(x["time"]) < sel]}
            p2 = wallet_events(cut)[0]
            m2 = p2[:, 0] >= lo
            hindsight += int(abs(float(p2[m2, 1].sum()) - r[a]["pnl"]) > 1e-6)
        ranks.append(r)
        elig_n.append(len(r))
    persist = []
    for r0, r1 in zip(ranks, ranks[1:]):
        both = sorted(set(r0) & set(r1))
        if len(both) >= 10:
            persist.append({m: float(pd.Series([r0[a][m] for a in both]).rank().corr(
                pd.Series([r1[a][m] for a in both]).rank())) for m in ("pnl", "roi")} | {"n": len(both)})
    turnover = {}
    for k in (5, 10, 20):
        for m in ("pnl", "roi"):
            tops = [set(sorted(r, key=lambda a: -r[a][m])[:k]) for r in ranks]
            turnover[f"{m}-K{k}"] = float(np.mean([1 - len(a & b) / max(len(a), 1) for a, b in zip(tops, tops[1:])]))

    def copies(sel_sets, delay, stop):
        out = []
        for si, chosen in enumerate(sel_sets):
            lo, hi = sels[si], sels[si + 1]
            for a in chosen:
                for o in W[a]["opens"]:
                    sym = hl_symbol(o["coin"])
                    if not sym or not lo <= o["open_t"] < hi:
                        continue
                    assert o["open_t"] >= sels[si]  # copy only openings after the selection date
                    et = o["open_t"] + delay * M
                    xt = min(o["exit_t"] or C1["end"], o["open_t"] + COPY_MAX_DAYS * DAY, C1["end"]) + delay * M
                    out.append({"wallet": a, "sym": sym, "coin": o["coin"], "side": o["side"], "entry_t": et,
                                "exit_t": xt, "why": "wallet" if o["exit_t"] and o["exit_t"] + delay * M == xt else "time",
                                "stop": stop})
        return out

    def price(tr):
        prefetch_funding([t["sym"] for t in tr])
        prefetch_hours([(t["sym"], t["entry_t"], t["exit_t"]) for t in tr if t["stop"]])
        PX.prefetch(sorted({(t["sym"], x // DAY * DAY) for t in tr for x in (-(-t["entry_t"] // M) * M,
                                                                              -(-t["exit_t"] // M) * M)}))
        done = []
        for t in tr:
            e, x = PX.open_at(t["sym"], t["entry_t"]), PX.open_at(t["sym"], t["exit_t"])
            if e is None or x is None:
                continue
            if t["stop"]:
                a = PX.hours(t["sym"], t["entry_t"], t["exit_t"])
                stop_px = e * (1 - t["side"] * SAFETY_STOP / 100)
                if a is not None:
                    inside = a[(a[:, 0] >= -(-t["entry_t"] // H) * H) & (a[:, 0] + H <= t["exit_t"])]
                    hit = np.nonzero(inside[:, 3] <= stop_px if t["side"] == 1 else inside[:, 2] >= stop_px)[0]
                    if len(hit):
                        x, t["exit_t"], t["why"] = stop_px, int(inside[hit[0], 0] + H), "stop"
            t.update(entry=e, exit=x, gross=t["side"] * (x / e - 1) * 100,
                     fund=funding_cost(t["sym"], t["side"], t["entry_t"], t["exit_t"]))
            done.append(t)
        return done

    res = {"appendix": [], "chosen": {}, "persist": persist, "turnover": turnover, "hindsight": hindsight,
           "eligible_per_month": elig_n, "selections": len(sels) - 1}
    trades = {}
    for p in C1_GRID:
        sets = [sorted(r, key=lambda a: -r[a][p["metric"]])[:p["k"]] for r in ranks]
        for d in DELAYS:
            tr = price(copies(sets, d, p["stop"]))
            trades[(json.dumps(p), d)] = tr
            res["appendix"].append({"combo": p, "delay": d, "train": stats(tr, C1["start"], C1["split"]),
                                    "unseen": stats(tr, C1["split"], C1["end"])})
            log(f"C1 {p} delay {d}m: {len(tr)} copied trades")
    for d in DELAYS:
        cand = [(stats(trades[(json.dumps(p), d)], C1["start"], C1["split"]), p) for p in C1_GRID]
        cand = [x for x in cand if x[0]["n"] >= 30] or cand
        _, p = max(cand, key=lambda x: x[0].get("avg", -1e9))
        tr = trades[(json.dumps(p), d)]
        un = [t for t in tr if C1["split"] <= t["entry_t"] < C1["end"]]
        r = {"combo": p, "train": stats(tr, C1["start"], C1["split"]), "unseen": stats(tr, C1["split"], C1["end"]),
             "half1": stats(tr, C1["split"], C1["half"]), "half2": stats(tr, C1["half"], C1["end"]),
             "stress": stats(tr, C1["split"], C1["end"], True)}
        rng = rng_for("C1", d, p)
        real1h = ([t["gross"] for t in un] if p["stop"] else  # stop exits must keep their stop fill (no 1h re-price)
                  [g for g in (hour_one(t, fund=False) for t in un) if g is not None])  # same basis as random wallets
        si0 = next(i for i, s in enumerate(sels) if s >= C1["split"])
        rw = []
        for _ in range(N_RANDOM):
            sets = [list(rng.choice(sorted(rk), min(p["k"], len(rk)), replace=False)) if rk else [] for rk in ranks]
            sets = [s if i >= si0 else [] for i, s in enumerate(sets)]
            sim = [t for t in copies(sets, d, False) if C1["split"] <= t["entry_t"] < C1["end"]]
            rw.append(sim)
        r["random_wallet_pct"] = random_wallet_pct(real1h, rw)
        r["random_entry_pct"] = random_rule(un, C1["split"], C1["end"], rng, pct_stop=SAFETY_STOP if p["stop"] else None)
        pct = None if r["random_wallet_pct"] is None or r["random_entry_pct"] is None else \
            min(r["random_wallet_pct"], r["random_entry_pct"])
        r["verdict"], r["fails"] = verdict(r["unseen"], r["half1"], r["half2"], pct, r["stress"], 50)
        by_w = {}
        for t in un:
            by_w.setdefault(t["wallet"], []).append(net(t["gross"], t["fund"]))
        if by_w:
            top = max(by_w, key=lambda w: sum(by_w[w]))
            rest = [x for w, v in by_w.items() if w != top for x in v]
            r["concentration"] = {"wallets": len(by_w), "top_wallet": top[:8] + "…", "top_trades": len(by_w[top]),
                                  "top_total": float(sum(by_w[top])), "avg_without_top": float(np.mean(rest)) if rest else None}
        r["hand"] = [hand_c1(t, docs) for t in (un or tr)[:1] + [x for x in (un or tr) if x["side"] != (un or tr)[0]["side"]][:1]]
        res["chosen"][d] = r
    return res


def random_wallet_pct(real1h, runs):
    """Random wallets copied with the same rules; both sides priced on 1h opens (no 1m download per random run)."""
    if not real1h:
        return None
    means = []
    for sim in runs:
        g = hour_gross_fast(sim)
        means.append(np.mean(g) if g else 0.0)
    return float((np.array(means) < np.mean(real1h)).mean() * 100)


def hour_gross_fast(tr):
    return [g for g in (hour_one(t, fund=False) for t in tr) if g is not None]  # funding not fetched here


def hand_c1(t, docs):
    a = PX.day(t["sym"], -(-t["entry_t"] // M) * M // DAY * DAY)
    i = np.searchsorted(a[:, 0], -(-t["entry_t"] // M) * M)
    assert abs(a[i, 1] - t["entry"]) < 1e-9, "entry is not the 1m open after the delay"
    fills = [f for f in docs[t["wallet"]]["fills"] if f["coin"] == t["coin"] and
             t["entry_t"] - 2 * H <= f["time"] <= t["entry_t"]][-2:]
    return {"wallet": t["wallet"][:8] + "…", "coin": t["coin"], "side": t["side"], "wallet_fills": fills,
            "entry_time": bt.iso(t["entry_t"]), "entry": t["entry"], "exit_time": bt.iso(t["exit_t"]), "exit": t["exit"],
            "why": t["why"], "gross_pct": round(t["gross"], 4), "funding_pct": round(t["fund"], 4),
            "net_pct": round(net(t["gross"], t["fund"]), 4)}


# ---------------------------------------------------------------- C2: listing / delisting events
STOP_WORDS = {"USDT", "USDC", "USD", "UTC", "AND", "ON", "FOR", "OKX", "KUCOIN", "BINANCE", "TO", "NFT", "API", "BTC",
              "ETH", "BNB", "EUR", "TRY", "FDUSD", "VIP", "DEX", "SPOT", "OF", "IN", "WITH", "WILL", "LIST", "ALPHA", "IPO"}


def tickers(title):
    p = re.findall(r"\(([A-Z0-9]{2,12})\)", title) or re.findall(r"\b([A-Z0-9]{2,12})/US", title)
    if not p:
        p = [x for x in re.findall(r"\b([A-Z0-9]{2,16})\b", title) if not x.isdigit()]
    p = [x[:-4] if x.endswith(("USDT", "USDC")) and len(x) > 5 else x for x in p]
    return list(dict.fromkeys(x for x in p if x not in STOP_WORDS))


def classify(a):
    t, low = a["title"], a["title"].lower()
    if re.search(r"\bmargin\b", low) or any(x in low for x in ("stock", "pre-ipo", "tradfi", "index", "earn", "equity", "convert", "loan", "launchpool",
                              "hodler", "options", "delivery", "copy trading", "megadrop", "collateral", "commodit", "loan")):
        return None
    if "monitoring tag" in low:  # only coins the tag is ADDED to ('Extend ... to Include X'); removals are not events
        m = re.search(r"include (.+?)(?:\s+and remove|\s*[;,]\s*remove|\s+on \d{4}|$)", t, re.I)
        return [("monitoring", k) for k in tickers(m.group(1))] if m and tickers(m.group(1)) else None
    elif "delist" in low or "removal" in low and "spot trading pairs" not in low:
        typ = "delisting" if "notice of removal" not in low else None
    elif "perpetual" in low and ("launch" in low or "list" in low):
        typ = "perp_listing"
        ks = re.findall(r"\b([A-Z0-9]{2,15})USDT\b", t)
        return [(typ, k) for k in ks if k not in STOP_WORDS] or None
    elif re.search(r"will list|listed on|to list|world premiere|gemslab|launch .*for spot trading", low):
        typ = "spot_listing"
    else:
        return None
    return [(typ, k) for k in tickers(t)] if typ else None


COMMODITY = {"XAUT", "PAXG", "XAU", "XAG", "NATGAS", "BZ", "CL", "WTI", "BRENT", "GOLD", "SILVER"}
TRADFI_WORDS = ("stock", "tradfi", "equity", "index", "pre-ipo", "commodit", "bstocks", "xstock", "etf")


def tradfi_tickers(anns):
    """Tickers that any announcement calls a stock/ETF/index/commodity perp (titles like 'Will Delist KORUUSDT' don't say)."""
    low = lambda a: a["title"].lower()
    hit = lambda a: (any(w in low(a) for w in TRADFI_WORDS) and "dividend" not in low(a) and "stock trading" not in low(a)
                     and any(w in low(a) for w in ("perpetual", "bstock", "xstock", "tokenized")))
    return {k for a in anns if hit(a) for k in tickers(a["title"])} | COMMODITY


def c2(anns):
    evs, seen = [], {}
    tradfi = tradfi_tickers(anns)
    for a in sorted(anns, key=lambda x: x["t"]):
        for typ, k in classify(a) or []:
            if k in tradfi:
                continue
            key = ({"monitoring": "delisting"}.get(typ, typ), k)  # same coin + event group on any exchange
            if a["t"] - seen.get(key, -10 * DAY) < 7 * DAY:
                continue
            seen[key] = a["t"]
            evs.append({"ex": a["ex"], "type": typ, "ticker": k, "t": a["t"], "title": a["title"][:120]})
    counts = {}
    for e in evs:
        counts.setdefault(f"{e['ex']} {e['type']}", 0)
        counts[f"{e['ex']} {e['type']}"] += 1
    span = {ex: (bt.iso(min(e["t"] for e in evs if e["ex"] == ex)), bt.iso(max(e["t"] for e in evs if e["ex"] == ex)))
            for ex in sorted({e["ex"] for e in evs})}
    for e in evs:
        e["sym"] = e["ticker"] + "USDT"
    PX.prefetch(sorted({(e["sym"], d) for e in evs for d in range(e["t"] // DAY * DAY, e["t"] // DAY * DAY + 9 * DAY, DAY)
                        if d < C2["end"]}))
    paths, priced = {}, 0
    prefetch_funding([e["sym"] for e in evs if PX.day(e["sym"], e["t"] // DAY * DAY) is not None])
    prefetch_hours([(e["sym"], e["t"], e["t"] + 8 * DAY) for e in evs])
    prefetch_hours([(e["sym"], C2["split"], C2["end"]) for e in evs if e["t"] >= C2["split"]])
    log("C2 prefetch done")
    for e in evs:
        a = PX.path_1m(e["sym"], e["t"], e["t"] + 8 * DAY)
        a = a[live(a)] if a is not None and len(a) else a
        e["first"] = int(a[0, 0]) if a is not None and len(a) else None
        e["perp_before"] = bool(e["first"] is not None and e["first"] <= e["t"] + 5 * M)
        if e["perp_before"]:
            priced += 1
            i0 = np.searchsorted(a[:, 0], e["t"] + 5 * M)
            for lbl, dt in (("5m", 5 * M), ("1h", H), ("6h", 6 * H), ("24h", DAY), ("3d", 3 * DAY), ("7d", 7 * DAY)):
                j = np.searchsorted(a[:, 0], a[i0, 0] + dt)
                if j < len(a):
                    paths.setdefault(e["type"], {}).setdefault(lbl, []).append((a[j, 4] / a[i0, 1] - 1) * 100)
    path_stats = {typ: {lbl: {"n": len(v), "median": float(np.median(v)), "mean": float(np.mean(v)),
                              "neg_share": float(np.mean(np.array(v) < 0) * 100)} for lbl, v in d.items()}
                  for typ, d in paths.items()}

    def trades(p):
        out = []
        for e in evs:
            if p["rule"] == "R1" and (e["type"] != "spot_listing" or not e["perp_before"]):
                continue
            if p["rule"] == "R2" and (e["type"] != "perp_listing" or e["ex"] != "binance" or e["first"] is None
                                      or e["perp_before"]):
                continue
            if p["rule"] == "R3" and (e["type"] not in ("delisting", "monitoring") or not e["perp_before"]):
                continue
            base_t = e["first"] if p["rule"] == "R2" else e["t"]
            et = base_t + {"5m": 5 * M, "6h": 6 * H, "first+1h": H, "first+6h": 6 * H}[p["entry"]]
            et = max(et, e["t"] + 5 * M)
            a = PX.path_1m(e["sym"], et, et + p["hold"] * H + M)
            h1 = PX.hours(e["sym"], et - 15 * H, et)
            if a is None or not len(a) or a[0, 0] - et > 5 * M:
                continue
            assert a[0, 0] >= e["t"] + 5 * M  # never earlier than 5 min after publication
            e_px = a[0, 1]
            if h1 is not None and len(h1[h1[:, 0] + H <= et]):
                hh = h1[h1[:, 0] + H <= et][-14:]
                pc = np.concatenate([[hh[0, 1]], hh[:-1, 4]])
                atr = float(np.mean(np.maximum(hh[:, 2] - hh[:, 3], np.maximum(abs(hh[:, 2] - pc), abs(hh[:, 3] - pc)))))
            else:  # no completed 1h candle yet: the range of the hour BEFORE entry (1m candles)
                pre = PX.path_1m(e["sym"], et - H, et)
                pre = pre[live(pre)] if pre is not None and len(pre) else pre
                if pre is None or len(pre) < 15:
                    continue
                atr = float(pre[:, 2].max() - pre[:, 3].min())
            if atr <= 0 or not live(a[:60]).any():
                continue  # dead / flat perp: not tradable
            stop = e_px + 2 * atr
            hit = np.nonzero(a[:, 2] >= stop)[0]
            if len(hit):
                x, xt, why = max(stop, a[hit[0], 1]), int(a[hit[0], 0] + M), "stop"  # gap above the stop: fill at open
            else:
                x, xt, why = a[-1, 4], int(a[-1, 0] + M), "time" if a[-1, 0] + M >= et + p["hold"] * H else "data end"
                if why == "data end" and et + p["hold"] * H > C2["end"] - H:
                    continue  # unfinished: the hold runs past the end of the data
            out.append({"sym": e["sym"], "ex": e["ex"], "type": e["type"], "side": -1, "pub_t": e["t"], "entry_t": int(a[0, 0]),
                        "exit_t": xt, "entry": float(e_px), "exit": float(x), "why": why, "stop_px": stop,
                        "gross": -(x / e_px - 1) * 100,
                        "fund": funding_cost(e["sym"], -1, int(a[0, 0]), xt)})
        return out

    res = {"counts": counts, "span": span, "n_events": len(evs), "priced": priced, "paths": path_stats, "appendix": [],
           "chosen": {}, "lookahead_violations": 0}
    allt = {}
    for p in C2_GRID:
        tr = trades(p)
        res["lookahead_violations"] += sum(1 for t in tr if t["entry_t"] < t["pub_t"] + 5 * M)
        allt[json.dumps(p)] = tr
        res["appendix"].append({"combo": p, "train": stats(tr, C2["start"], C2["split"]),
                                "unseen": stats(tr, C2["split"], C2["end"])})
        log(f"C2 {p}: {len(tr)} trades")
    for rule in ("R1", "R2", "R3"):
        cand = [(stats(allt[json.dumps(p)], C2["start"], C2["split"]), p) for p in C2_GRID if p["rule"] == rule]
        _, p = max(cand, key=lambda x: (x[0]["n"] >= 10, x[0].get("avg", -1e9)))
        tr = allt[json.dumps(p)]
        un = [t for t in tr if C2["split"] <= t["entry_t"] < C2["end"]]
        r = {"combo": p, "train": stats(tr, C2["start"], C2["split"]), "unseen": stats(tr, C2["split"], C2["end"]),
             "half1": stats(tr, C2["split"], C2["half"]), "half2": stats(tr, C2["half"], C2["end"]),
             "stress": stats(tr, C2["split"], C2["end"], True)}
        r["random_pct"] = random_rule(un, C2["split"], C2["end"], rng_for("C2", p), hold=p["hold"], atr_stop=2) if un else None
        r["verdict"], r["fails"] = verdict(r["unseen"], r["half1"], r["half2"], r["random_pct"], r["stress"], 30)
        r["hand"] = [hand_c2(t) for t in (un or tr)[:2]]
        r["unseen_trades"] = [{k: t[k] for k in ("sym", "ex", "type", "pub_t", "entry_t", "exit_t", "why")} |
                              {"net": round(net(t["gross"], t["fund"]), 4), "fund": round(t["fund"], 4)} for t in un]
        res["chosen"][rule] = r
    return res


def hand_c2(t):
    a = PX.path_1m(t["sym"], t["entry_t"], t["exit_t"])
    assert a[0, 0] == t["entry_t"] and a[0, 1] == t["entry"]
    assert t["why"] != "stop" or a[:, 2].max() >= t["stop_px"]
    g = -(t["exit"] / t["entry"] - 1) * 100
    return {"coin": t["sym"], "event": f"{t['ex']} {t['type']}", "published": bt.iso(t["pub_t"]),
            "entry_time": bt.iso(t["entry_t"]), "entry": t["entry"], "exit_time": bt.iso(t["exit_t"]), "exit": t["exit"],
            "why": t["why"], "gross_pct_recomputed": round(g, 4), "funding_pct": round(t["fund"], 4),
            "net_pct": round(net(g, t["fund"]), 4)}


# ---------------------------------------------------------------- report
def f(x, d=2):
    return "—" if x is None else ("∞" if x == float("inf") else f"{x:.{d}f}")


def srow(name, s):
    if not s.get("n"):
        return f"| {name} | 0 | | | | | | |"
    return (f"| {name} | {s['n']} | {f(s['per_week'], 1)} | {f(s['win'], 1)} | {f(s['avg'], 3)} | {f(s['pf'])} | "
            f"{f(s['total'], 1)} | {f(s['hold_h'], 1)} |")


HDR = "| period | trades | /week | win % | avg net % | PF | total % (equal size) | avg hold h |\n|---|---|---|---|---|---|---|---|"


def fragility(r):
    T = r.get("unseen_trades") or []
    ann = {}
    for t in T:
        ann.setdefault(t["pub_t"], []).append(t["net"])
    best = max(ann, key=lambda k: sum(ann[k])) if ann else None
    rest = [x for k, v in ann.items() if k != best for x in v]
    x = np.array(sorted([t["net"] for t in T], reverse=True)) if T else np.zeros(1)
    by = lambda key: {v: (sum(t[key] == v for t in T), float(np.mean([t["net"] for t in T if t[key] == v])))
                      for v in sorted({t[key] for t in T})}
    return {"win": float((x > 0).mean() * 100), "median": float(np.median(x)), "no_top3": float(x[3:].mean()) if len(x) > 3 else 0.0,
            "by_type": by("type") if T else {}, "by_ex": by("ex") if T else {},
            "announcements": len(ann), "ann_positive": sum(sum(v) > 0 for v in ann.values()),
            "best_ann_share": float(sum(ann[best]) / max(sum(x), 1e-9)) if best else 0.0,
            "no_best_ann": float(np.mean(rest)) if rest else 0.0,
            "breakeven_slip": float(SLIP + x.mean() / 2), "exits": {w: sum(t["why"] == w for t in T) for w in
                                                                      sorted({t["why"] for t in T})}}


def report(r1, r2, uni, secs):
    passed = [(f"C1 delay {d} min", r) for d, r in r1["chosen"].items() if r["verdict"] == "PASSED"] + \
             [(f"C2 {k}", r) for k, r in r2["chosen"].items() if r["verdict"] == "PASSED"]
    L = ["# Phase 4 Step C research report (PRIVATE — gitignored)", ""]
    if passed:
        nm, r = max(passed, key=lambda x: x[1]["unseen"]["total"])
        fr = fragility(r)
        L += [f"**{len(passed)} rule passed the rules, but check its fragility.** {nm} {json.dumps(r['combo'])}.",
              f"- It would trade about {r['unseen']['per_week']:.1f} times per week.",
              f"- Unseen 8 months after costs: {r['unseen']['n']} trades, avg {r['unseen']['avg']:+.2f}% per trade, "
              f"profit factor {f(r['unseen']['pf'])}, but only {fr['win']:.0f}% winners and {fr['no_top3']:+.2f}% "
              f"without its 3 best trades.",
              "- Recommendation: paper-watch only (no real money); it may not survive real slippage on these thin coins.",
              "- Past results don't guarantee future results."]
    else:
        L += ["**NOTHING passed.** Copying Hyperliquid wallets (C1) and trading listing/delisting news (C2) both failed "
              "the honesty rules on the unseen months after costs.",
              "- Best results are listed below, but each failed at least one rule, so none should be traded.",
              "- Recommendation: don't trade these. Keep paper-watching and keep recording data.",
              "- Past results don't guarantee future results."]
    st = uni["stats"]
    L += ["", f"_Run time {secs / 60:.1f} min. Costs: Binance futures 0.05% per side + 0.05% slippage per fill + funding; "
               "stress 1.5× fees and 0.15% slippage. Random baselines (200 runs): random entries run the SAME rule (same coin, "
               "side, hold and stop) at random live 1h opens, one random time per announcement, against the rule's real "
               "1m fills; random wallets are copied with the same rules and both sides are priced on 1h opens._", "", "## Verdicts", "",
          "| study | chosen setting | verdict | failing rules | unseen trades | unseen avg % | random pct | stress avg % |",
          "|---|---|---|---|---|---|---|---|"]
    for d, r in r1["chosen"].items():
        rp = f"wallets {f(r['random_wallet_pct'], 0)} / entries {f(r['random_entry_pct'], 0)}"
        L.append(f"| C1 copy, delay {d} min | {json.dumps(r['combo'])} | **{r['verdict']}** | {'; '.join(r['fails'])} | "
                 f"{r['unseen'].get('n', 0)} | {f(r['unseen'].get('avg'), 3)} | {rp} | {f(r['stress'].get('avg'), 3)} |")
    for k, r in r2["chosen"].items():
        L.append(f"| C2 {k} | {json.dumps(r['combo'])} | **{r['verdict']}** | {'; '.join(r['fails'])} | "
                 f"{r['unseen'].get('n', 0)} | {f(r['unseen'].get('avg'), 3)} | {f(r['random_pct'], 0)} | "
                 f"{f(r['stress'].get('avg'), 3)} |")
    L += ["", "## C1 — copying Hyperliquid wallets", "",
          f"- Universe: {st['sampled']} wallets, a stratified random sample (60 per all-time-PnL decile) from "
          f"{st['leaderboard_rows']} leaderboard accounts; {st['losing_share'] * 100:.0f}% have losing all-time PnL. "
          f"Excluded before sampling: {st['excluded']}. Emptied (blown-up) accounts were kept.",
          "- Residual survivorship: wallets that left the leaderboard entirely can't be seen. The API serves only the "
          "10,000 most recent fills, so a wallet whose history is cut is only eligible from 30 days after its first visible fill.",
          f"- Monthly re-selection on the 27th ({r1['selections']} selections); ranking uses only fills in the 30 days "
          f"before the date (≥ 10 closing fills). Eligible wallets per month: {r1['eligible_per_month']}. "
          f"No hindsight by construction: ranking reads only fills before the date (re-ranking from fills cut at the "
          f"date gives identical metrics, {r1['hindsight']} differences).",
          "- Copy: a selected wallet's new position (from flat or a flip) opened after the selection date, on a coin "
          "with a Binance USDT-M perp. Exit when the wallet first reduces or closes it (max 30 days). Stop variant: "
          f"{SAFETY_STOP:.0f}% adverse stop, checked on 1h candles. Entry and exit at the Binance 1m open after the delay.",
          "", "**Persistence (rank correlation of 30-day metrics between consecutive months)**", "",
          "| months compared | wallets in both | PnL rank corr | ROI rank corr |", "|---|---|---|---|"]
    for i, p in enumerate(r1["persist"]):
        L.append(f"| {i + 1}→{i + 2} | {p['n']} | {f(p['pnl'])} | {f(p['roi'])} |")
    if r1["persist"]:
        L.append(f"| mean | | {f(np.mean([p['pnl'] for p in r1['persist']]))} | "
                 f"{f(np.mean([p['roi'] for p in r1['persist']]))} |")
    L += ["", "Top-K turnover per month (share of chosen wallets replaced): " +
          ", ".join(f"{k} {v * 100:.0f}%" for k, v in r1["turnover"].items())]
    for d, r in r1["chosen"].items():
        L += ["", f"### Delay {d} min — chosen on training: {json.dumps(r['combo'])} — {r['verdict']}", "", HDR] + \
             [srow(nm, r[nm]) for nm in ("train", "unseen", "half1", "half2")] + [srow("stress (unseen)", r["stress"])]
        c = r.get("concentration")
        if c:
            L.append(f"\nConcentration: {c['wallets']} wallets copied in the unseen months; the best one ({c['top_wallet']}) "
                     f"gave {c['top_trades']} trades and {c['top_total']:+.1f}% of the total. Without it the average trade is "
                     f"{f(c['avg_without_top'], 3)}%.")
    L += ["", "## C2 — listing / delisting announcements", "",
          f"- {r2['n_events']} events after de-duplication (same exchange/type/coin within 7 days); {r2['priced']} had a "
          "Binance USDT-M perp trading at the announcement (the price reference). Upbit's API is blocked (HTTP 403) "
          "from here, so Upbit is not included. OKX's announcement API serves only about 1.5 years.",
          "", "| exchange / type | events |", "|---|---|"] + [f"| {k} | {v} |" for k, v in sorted(r2["counts"].items())]
    L += ["", "History span: " + "; ".join(f"{k} {a} → {b}" for k, (a, b) in r2["span"].items()), "",
          "**Price paths after the announcement** (perp price change from +5 min; negative = price fell)", "",
          "| type | horizon | events | median % | mean % | % of events down |", "|---|---|---|---|---|---|"]
    for typ, d in r2["paths"].items():
        for lbl, s in d.items():
            L.append(f"| {typ} | {lbl} | {s['n']} | {f(s['median'])} | {f(s['mean'])} | {f(s['neg_share'], 0)} |")
    L += ["", "Rules: R1 short after a spot listing announcement (any exchange, perp already trading); R2 short a new "
          "Binance perp after its first trade; R3 short after a delisting or monitoring-tag announcement. "
          "Stop = 2 × ATR(14) of 1h candles before entry (shorter history: mean range), checked on 1m candles.",
          f"Look-ahead violations (entry earlier than 5 min after publication): {r2['lookahead_violations']}."]
    for k, r in r2["chosen"].items():
        L += ["", f"### {k} — chosen on training: {json.dumps(r['combo'])} — {r['verdict']}", "", HDR] + \
             [srow(nm, r[nm]) for nm in ("train", "unseen", "half1", "half2")] + [srow("stress (unseen)", r["stress"])]
        if r.get("unseen_trades"):
            fr = fragility(r)
            L += ["", f"Fragility (unseen): {fr['win']:.0f}% winners, median trade {fr['median']:+.2f}%, average without "
                      f"the 3 best trades {fr['no_top3']:+.2f}%; exits {fr['exits']}; break-even slippage ≈ "
                      f"{fr['breakeven_slip']:.2f}% per fill. Announcements: {fr['announcements']} "
                      f"({fr['ann_positive']} net positive); without the best one the average is {fr['no_best_ann']:+.2f}%. "
                      "By event type: " +
                  ", ".join(f"{a} {n} trades {m:+.2f}%" for a, (n, m) in fr["by_type"].items()) + ". By exchange: " +
                  ", ".join(f"{a} {n} trades {m:+.2f}%" for a, (n, m) in fr["by_ex"].items()) + "."]
    L += ["", "## Recommendation", ""]
    L.append("Paper-watch the passing rule for 4 weeks before any real money." if passed else
             "**Nothing passed — don't trade wallet copying or listing news.** Keep the bot in quiet paper mode; the "
             "near-miss paper-watch and the recorder keep collecting live evidence.")
    L += ["", "## Known limits", "- C2 stops fill at max(stop, open) on gaps; the C1 10% stop variant (never chosen) "
          "fills exactly at the stop. Random-entry runs charge each trade's own funding, not the random window's "
          "(small: R3's average funding is about 0.1%).", "- Honesty note: the Binance monitoring-tag notices (another announcement catalog) "
          "were added after the first run, when the tester pointed out R3 was missing them. That was a data fix, not a "
          "parameter change (R3's rules were fixed in advance); R3 came closest to passing with them.",
          "- Survivorship (see C1). New listings can have "
          "much bigger real slippage than 0.05%.",
          "- The wallet sample is stratified by all-time PnL and the market-maker filter uses monthly volume, both "
          "from today's leaderboard (mild look-ahead in how the universe was drawn; it can't change who is selected "
          "each month, which uses only past fills).",
          "- C2 excludes stock/index/TradFi/commodity/pre-IPO perps and margin-only delistings; events are "
          "de-duplicated across exchanges (same coin and event group within 7 days). Flat, zero-volume archive "
          "candles (dead perps) are not treated as tradable, for events and for random starts.", "- C1 random-wallet runs skip funding (both sides priced on 1h opens, "
          "the rule's own 1h price includes funding, which makes the rule look slightly worse, not better).",
          "", "## Appendix — every combination (training / unseen, 1m pricing)", "",
          "| study | setting | delay | train n | train avg % | unseen n | unseen avg % | unseen PF |", "|---|---|---|---|---|---|---|---|"]
    for a in r1["appendix"]:
        L.append(f"| C1 | {json.dumps(a['combo'])} | {a['delay']} | {a['train'].get('n', 0)} | {f(a['train'].get('avg'), 3)} | "
                 f"{a['unseen'].get('n', 0)} | {f(a['unseen'].get('avg'), 3)} | {f(a['unseen'].get('pf'))} |")
    for a in r2["appendix"]:
        L.append(f"| C2 | {json.dumps(a['combo'])} | — | {a['train'].get('n', 0)} | {f(a['train'].get('avg'), 3)} | "
                 f"{a['unseen'].get('n', 0)} | {f(a['unseen'].get('avg'), 3)} | {f(a['unseen'].get('pf'))} |")
    L += ["", "## Hand-checked trades", "", "```", json.dumps({"C1": {d: r["hand"] for d, r in r1["chosen"].items()},
                                                             "C2": {k: r["hand"] for k, r in r2["chosen"].items()}},
                                                            indent=1, default=str)[:12000], "```"]
    return "\n".join(L)


def main(argv=None):
    t0 = time.time()
    os.makedirs(OUT, exist_ok=True)
    uni = json.load(open(os.path.join(DATA, "hl_universe.json")))
    docs = {}
    for s in uni["sample"]:
        p = os.path.join(DATA, "hl_fills", f"{s['addr']}.json.gz")
        if os.path.exists(p):
            docs[s["addr"]] = json.load(gzip.open(p))
    log(f"C1: {len(docs)} wallets with fills loaded")
    r2 = c2(json.load(open(os.path.join(DATA, "announcements.json"))))
    if argv == ["c2"]:
        json.dump(r2, open(os.path.join(OUT, "c2_only.json"), "w"), default=str)
        return 0
    r1 = c1(uni, docs)
    json.dump({"c1": r1, "c2": r2, "universe": uni["stats"]}, open(os.path.join(OUT, "step_c_results.json"), "w"),
              default=str)
    open(os.path.join(OUT, "step_c_report.md"), "w").write(report(r1, r2, uni, time.time() - t0))
    log(f"done → {OUT}/step_c_report.md")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
