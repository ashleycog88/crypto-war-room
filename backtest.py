#!/usr/bin/env python3
"""Phase 2: 3-month backtest of the War Room v10 rules. Run: python backtest.py

Every 5m bar is scored with the LIVE functions from war_room_bot.py on exactly the candle windows the live bot
would have seen at that moment (309 closed 5m + 259 closed 1h candles). Trades use the live resolve_trade for the
live exit style; exit-style variants (b)(c)(d) are the only trade logic defined here.
"""
import argparse, csv, hashlib, io, json, math, os, pickle, sys, time, zipfile
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd
import requests

import war_room_bot as wr
from war_room_bot import (add_indicators, to_df, validate_candles, evaluate_pair, btc_regime, make_levels,
                          resolve_trade, pct_move, net_pnl, build_coin_list, norm_base, TF_MS, LIMIT_5M, LIMIT_1H,
                          COOLDOWN_S, A_MIN, B_MIN)

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "backtest_data")
START, TRAIN_END, END = "2026-08-27", "2026-10-27", "2026-11-27"
WIN5, WIN1 = LIMIT_5M - 1, LIMIT_1H - 1  # closed candles visible to the live bot (forming one dropped)
WARMUP_DAYS = 12
SLIP = 0.0005
HOLDS = [1, 2, 4, 8, 12, 24, 48]
LIVE_HOLD = {"A": 24, "B": 4}
WHATIF_THR = (68, 55)
VISION = "https://data.binance.vision/data"
FEE_SPOT, FEE_FUT = 0.10, 0.05
M5 = TF_MS["5m"]
H1 = TF_MS["1h"]
DIR_CODE = {None: 0, "LONG": 1, "SHORT": -1}
CODE_DIR = {0: None, 1: "LONG", -1: "SHORT"}

_S = requests.Session()
_S.mount("https://", requests.adapters.HTTPAdapter(pool_connections=8, pool_maxsize=48))


def ms(day):
    return int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def iso(t_ms):
    return datetime.fromtimestamp(t_ms / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M")


# ---------------------------------------------------------------- downloads (cached)
def fetch(url, path, method="GET", body=None):
    """Download once into the cache. Returns bytes, or None for a cached/confirmed 404."""
    if os.path.exists(path):
        with open(path, "rb") as f:
            return f.read()
    if os.path.exists(path + ".404"):
        return None
    os.makedirs(os.path.dirname(path), exist_ok=True)
    for attempt in range(4):
        try:
            r = _S.post(url, json=body, timeout=30) if method == "POST" else _S.get(url, timeout=30)
            if r.status_code == 404:
                open(path + ".404", "w").close()
                return None
            r.raise_for_status()
            tmp = path + ".part"
            with open(tmp, "wb") as f:
                f.write(r.content)
            os.replace(tmp, path)
            return r.content
        except Exception:
            time.sleep(1 + 2 * attempt)
    return None


def kline_periods(start_ms, end_ms):
    """Monthly archives for complete months, daily archives for the (incomplete) last month."""
    out, d = [], datetime.fromtimestamp(start_ms / 1000, timezone.utc).replace(day=1, hour=0, minute=0)
    end = datetime.fromtimestamp(end_ms / 1000, timezone.utc)
    while d < end:
        nxt = (d + timedelta(days=32)).replace(day=1)
        if nxt <= end.replace(day=1):
            out.append(("monthly", d.strftime("%Y-%m")))
        else:
            day = d
            while day < end:
                out.append(("daily", day.strftime("%Y-%m-%d")))
                day += timedelta(days=1)
        d = nxt
    return out


def _parse_kline_csv(raw):
    df = pd.read_csv(io.BytesIO(raw), header=None, usecols=range(6))
    if not str(df.iloc[0, 0]).strip().lstrip("-").isdigit():
        df = df.iloc[1:]
    a = df.astype(float).to_numpy()
    a[:, 0] = np.where(a[:, 0] > 1e14, a[:, 0] // 1000, a[:, 0])  # 2025+ spot archives use microseconds
    return a


def load_klines(sym, tf, start_ms, end_ms):
    """Rows [ts, o, h, l, c, v] from data.binance.vision archives, data-api REST for any missing day."""
    s = sym.replace("/", "")
    parts = []
    for kind, p in kline_periods(start_ms, end_ms):
        name = f"{s}-{tf}-{p}.zip"
        raw = fetch(f"{VISION}/spot/{kind}/klines/{s}/{tf}/{name}", os.path.join(DATA, "klines", s, tf, name))
        if raw:
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                parts.append(_parse_kline_csv(z.read(z.namelist()[0])))
        elif kind == "daily":
            d0 = ms(p)
            api = fetch(f"{wr.BINANCE_DATA}/api/v3/klines?symbol={s}&interval={tf}&startTime={d0}"
                        f"&endTime={d0 + 86_400_000 - 1}&limit=1000",
                        os.path.join(DATA, "klines", s, tf, f"{s}-{tf}-{p}.api.json"))
            rows = json.loads(api) if api else []
            if rows:
                parts.append(np.array([[float(x) for x in r[:6]] for r in rows]))
    if not parts:
        return np.zeros((0, 6))
    a = np.concatenate(parts)
    a = a[(a[:, 0] >= start_ms) & (a[:, 0] < end_ms)]
    a = a[np.argsort(a[:, 0], kind="stable")]
    return a[np.concatenate([[True], np.diff(a[:, 0]) > 0])]


def hl_universe():
    raw = fetch("https://api.hyperliquid.xyz/info", os.path.join(DATA, "deriv", "hl_meta.json"), "POST",
                {"type": "meta"})
    return {norm_base(u["name"][1:] if u["name"][:1] == "k" and u["name"][1:].isupper() else u["name"]): u["name"]
            for u in json.loads(raw)["universe"]} if raw else {}


def hl_funding(name, start_ms, end_ms):
    out, t = [], start_ms
    for page in range(40):
        raw = fetch("https://api.hyperliquid.xyz/info",
                    os.path.join(DATA, "deriv", "hl", f"{name}_{start_ms}_{end_ms}_{page}.json"), "POST",
                    {"type": "fundingHistory", "coin": name, "startTime": t, "endTime": end_ms})
        rows = json.loads(raw) if raw else []
        if not rows:
            break
        out += [(int(r["time"]), float(r["fundingRate"]) * 8) for r in rows]
        t = int(rows[-1]["time"]) + 1
        if len(rows) < 500:
            break
    return out


def okx_funding(base, start_ms, end_ms):
    out, after = [], end_ms
    for page in range(20):
        raw = fetch(f"https://www.okx.com/api/v5/public/funding-rate-history?instId={base}-USDT-SWAP&limit=100"
                    f"&after={after}", os.path.join(DATA, "deriv", "okx", f"{base}_{end_ms}_{page}.json"))
        rows = (json.loads(raw).get("data") or []) if raw else []
        if not rows:
            break
        out += [(int(r["fundingTime"]), float(r["fundingRate"])) for r in rows]
        after = int(rows[-1]["fundingTime"])
        if after < start_ms:
            break
    out = sorted(set(out))
    if len(out) >= 2:
        hrs = np.median(np.diff([x[0] for x in out])) / H1 or 8
        out = [(t, r * 8 / hrs) for t, r in out]
    return [x for x in out if start_ms - 86_400_000 <= x[0] < end_ms]


def bn_metrics(base, start_ms, end_ms):
    """Binance USD-M futures open interest (5-minute snapshots) from data.binance.vision daily metrics files."""
    days = [d for k, d in kline_periods(start_ms, end_ms) if k == "daily"] or []
    d, end = datetime.fromtimestamp(start_ms / 1000, timezone.utc), datetime.fromtimestamp(end_ms / 1000, timezone.utc)
    days = []
    while d < end:
        days.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)
    probe = days[len(days) // 2]
    sym = None
    for cand in (f"{base}USDT", f"1000{base}USDT", f"1000000{base}USDT", f"1M{base}USDT"):
        if fetch(f"{VISION}/futures/um/daily/metrics/{cand}/{cand}-metrics-{probe}.zip",
                 os.path.join(DATA, "deriv", "bnm", cand, f"{cand}-metrics-{probe}.zip")):
            sym = cand
            break
    if not sym:
        return None, []

    def one(day):
        raw = fetch(f"{VISION}/futures/um/daily/metrics/{sym}/{sym}-metrics-{day}.zip",
                    os.path.join(DATA, "deriv", "bnm", sym, f"{sym}-metrics-{day}.zip"))
        if not raw:
            return []
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            df = pd.read_csv(io.BytesIO(z.read(z.namelist()[0])))
        tt = pd.to_datetime(df["create_time"], utc=True).astype("int64") // 1_000_000
        return list(zip(tt.tolist(), df["sum_open_interest"].astype(float).tolist()))
    with ThreadPoolExecutor(8) as p:
        rows = sorted(x for r in p.map(one, days) for x in r)
    return sym, rows


# ---------------------------------------------------------------- per-bar evaluation (live functions)
def series_at(times, vals, t, max_age):
    """Last value with time <= t (no look-ahead) if not older than max_age. Returns (value, time) or (None, None)."""
    i = int(np.searchsorted(times, t, side="right")) - 1
    if i < 0 or t - times[i] > max_age:
        return None, None
    return vals[i], int(times[i])


def oi_change_at(oi_t, oi_v, t):
    now, tn = series_at(oi_t, oi_v, t, 15 * 60_000)
    old, _ = series_at(oi_t, oi_v, t - H1, 15 * 60_000)
    if now is None or not old:
        return None, None
    return round((now - old) / old * 100, 2), tn


def eval_coin(job):
    """Score every 5m bar of one coin with the live code. Output: dict of per-bar lists."""
    sym, rows5, rows1, start_ms, end_ms, f_t, f_v, oi_t, oi_v, reg_t, reg_v = job
    ts5, ts1 = rows5[:, 0], rows1[:, 0]
    close1 = ts1 + H1
    out = {k: [] for k in ("i", "t", "d_off", "s_off", "d_on", "s_on", "adj", "atr", "rsi5", "rsi1h", "vol",
                           "last5", "last1h", "f8", "f_time", "oic", "oi_time", "regime")}
    cache1 = {}
    rows5_l, rows1_l = rows5.tolist(), rows1.tolist()
    for i in range(len(rows5_l) - 1):
        t = int(ts5[i] + M5)
        if t < start_ms or t > end_ms - M5 or ts5[i + 1] != t:
            continue
        j = int(np.searchsorted(close1, t, side="right")) - 1
        if j < 0:
            continue
        if j not in cache1:
            closed1, why = validate_candles(rows1_l[max(0, j - WIN1 + 1): j + 1], "1h", t)
            cache1[j] = add_indicators(to_df(closed1)) if closed1 else None
        df1 = cache1[j]
        if df1 is None:
            continue
        closed5, why = validate_candles(rows5_l[max(0, i - WIN5 + 1): i + 1], "5m", t)
        if not closed5:
            continue
        df5 = add_indicators(to_df(closed5))
        price = float(rows5[i, 4])
        f8, ft = series_at(f_t, f_v, t, 9 * H1) if len(f_t) else (None, None)
        oic, ot = oi_change_at(oi_t, oi_v, t) if len(oi_t) else (None, None)
        reg = "n/a"
        if len(reg_t):
            ri = int(np.searchsorted(reg_t, t, side="right")) - 1
            reg = reg_v[ri] if ri >= 0 else "n/a"
        off = evaluate_pair(df5, df1, 0.0, price, None, None, "n/a", False)
        if off is None:
            continue
        on = evaluate_pair(df5, df1, 0.0, price, f8, oic, reg, True)
        for k, v in (("i", i), ("t", t), ("d_off", DIR_CODE[off["dir"]]), ("s_off", off["score"]),
                     ("d_on", DIR_CODE[on["dir"]]), ("s_on", on["score"]), ("adj", on["deriv_adj"]),
                     ("atr", off["atr"]), ("rsi5", off["rsi5"]), ("rsi1h", off["rsi1h"]), ("vol", off["vol"]),
                     ("last5", int(closed5[-1][0])), ("last1h", int(df1.iloc[-1]["ts"])), ("f8", f8),
                     ("f_time", ft), ("oic", oic), ("oi_time", ot), ("regime", reg)):
            out[k].append(v)
    return sym, out


def btc_regime_series(rows1, start_ms, end_ms):
    """Live btc_regime() on the exact 1h window available at each hour close."""
    ts, vals = [], []
    rl = rows1.tolist()
    for j in range(len(rl)):
        t = int(rows1[j, 0] + H1)
        if t < start_ms - H1 or t > end_ms:
            continue
        closed, _ = validate_candles(rl[max(0, j - WIN1 + 1): j + 1], "1h", t)
        if closed:
            ts.append(t)
            vals.append(btc_regime(add_indicators(to_df(closed))))
    return np.array(ts, dtype=float), vals


# ---------------------------------------------------------------- trade resolution
def resolve_live_style(trade, rows5, k, hold_h):
    """Style (a): the live resolve_trade, fed one 5m candle at a time like the live 60 s loop."""
    for m in range(k, len(rows5)):
        r = rows5[m]
        now_s = (r[0] + M5) / 1000
        evs = resolve_trade(trade, [r], r[4], now_s, hold_h, FEE_SPOT, FEE_FUT)
        for e in evs:
            if e["type"] == "CLOSE":
                return e, int(r[0] + M5)
    return None, None


def resolve_variant(trade, rows5, k, hold_h, style, atr):
    """Styles (b) full exit at TP1, (c) half at TP1 + 1-ATR trailing stop, (d) half at TP1 + BE stop to max hold."""
    d, e = trade["dir"], trade["entry"]
    long_ = d == "LONG"
    deadline = trade["entry_time"] * 1000 + hold_h * H1
    p1 = pct_move(d, e, trade["tp1"])
    tp1_hit, stop, best = False, trade["sl"], None

    def close(reason, gross, t_ms, px):
        n = net_pnl(d, gross, FEE_SPOT, FEE_FUT)
        trade["tp1_hit"] = tp1_hit
        return {"type": "CLOSE", "reason": reason, "exit": px, "gross": gross, "net": n, "win": n > 0}, t_ms
    for m in range(k, len(rows5)):
        ts, _, hi, lo, cl = rows5[m][:5]
        t_close = int(ts + M5)
        if ts >= deadline:
            break
        stopped = lo <= stop if long_ else hi >= stop
        if not tp1_hit:
            if stopped:
                return close("STOP LOSS", pct_move(d, e, trade["sl"]), t_close, trade["sl"])
            if (hi >= trade["tp1"]) if long_ else (lo <= trade["tp1"]):
                if style == "b":
                    return close("TP1 FULL", p1, t_close, trade["tp1"])
                tp1_hit, stop = True, e
                if (lo <= e) if long_ else (hi >= e):
                    return close("BREAKEVEN after TP1", 0.5 * p1, t_close, e)
                best = hi if long_ else lo
        elif stopped:
            reason = "BREAKEVEN after TP1" if stop == e else "TRAIL STOP"
            return close(reason, 0.5 * p1 + 0.5 * pct_move(d, e, stop), t_close, stop)
        if tp1_hit and style == "c":
            best = max(best, hi) if long_ else min(best, lo)
            stop = max(e, best - atr) if long_ else min(e, best + atr)
        if t_close >= deadline:
            g = pct_move(d, e, cl)
            return close("TIME EXIT", 0.5 * p1 + 0.5 * g if tp1_hit else g, t_close, cl)
    return None, None


# ---------------------------------------------------------------- simulation
def grade_of(score, thr):
    return "A" if score >= thr[0] else "B" if score >= thr[1] else None


def simulate(variant, evals, candles, meta):
    """Replay one variant: live cooldown, one open trade per pair, entry next open + slippage."""
    trades, unresolved = [], 0
    thr, use_deriv = variant.get("thr", (A_MIN, B_MIN)), variant.get("use_deriv", False)
    hold, style = variant.get("hold", LIVE_HOLD), variant.get("style", "a")
    grades, sides = variant.get("grades", {"A", "B"}), variant.get("sides", {"LONG", "SHORT"})
    coins = variant.get("coins")
    for sym, ev in evals.items():
        if coins is not None and sym not in coins:
            continue
        rows5 = candles[sym]
        has_perp = meta[sym]["has_perp"]
        free_at, last_alert = -1, -10 ** 15
        dcol, scol = ("d_on", "s_on") if use_deriv else ("d_off", "s_off")
        for n in range(len(ev["t"])):
            t = ev["t"][n]
            if t < free_at or t - last_alert < COOLDOWN_S * 1000:
                continue
            d = CODE_DIR[ev[dcol][n]]
            if d is None or d not in sides:
                continue
            g = grade_of(ev[scol][n], thr)
            if g is None or g not in grades:
                continue
            if d == "SHORT" and not has_perp:
                continue
            reg = ev["regime"][n]
            if variant.get("regime_filter") and sym != "BTC/USDT" and (
                    (d == "LONG" and reg == "down") or (d == "SHORT" and reg == "up")):
                continue
            k = ev["i"][n] + 1
            o = rows5[k][1]
            entry = o * (1 + SLIP) if d == "LONG" else o * (1 - SLIP)
            atr = ev["atr"][n]
            sl, tp1, tp2 = make_levels(d, entry, atr)
            tr = {"symbol": sym, "dir": d, "grade": g, "entry": entry, "sl": sl, "tp1": tp1, "tp2": tp2,
                  "entry_time": rows5[k][0] / 1000, "tp1_hit": False, "checked_until": 0}
            h = hold[g]
            if style == "a":
                ev_close, t_exit = resolve_live_style(tr, rows5, k, h)
            else:
                ev_close, t_exit = resolve_variant(tr, rows5, k, h, style, atr)
            if ev_close is None:
                unresolved += 1
                break
            sl_pct = abs(entry - sl) / entry * 100
            trades.append({
                "variant": variant["name"], "coin": sym, "dir": d, "grade": g, "score": ev[scol][n],
                "signal_time": t, "entry_time": int(rows5[k][0]), "entry": entry, "sl": sl, "tp1": tp1, "tp2": tp2,
                "atr": atr, "sl_pct": sl_pct, "exit_time": t_exit, "exit": ev_close["exit"],
                "reason": ev_close["reason"], "tp1_hit": tr.get("tp1_hit", False),
                "gross": ev_close["gross"], "net": ev_close["net"], "win": ev_close["win"],
                "R": ev_close["net"] / sl_pct if sl_pct else 0.0, "hold_h": (t_exit - rows5[k][0]) / H1,
                "period": "train" if t < meta["_train_end"] else "test", "last5": ev["last5"][n],
                "last1h": ev["last1h"][n], "f8": ev["f8"][n], "f_time": ev["f_time"][n], "oic": ev["oic"][n],
                "oi_time": ev["oi_time"][n], "regime": reg, "adj": ev["adj"][n]})
            free_at, last_alert = t_exit, t
    return trades, unresolved


def metrics(trades):
    n = len(trades)
    if not n:
        return {"trades": 0, "win_rate": None, "avg_net": None, "total_net": 0.0, "pf": None, "max_dd": 0.0,
                "exp_r": None, "avg_hold_h": None, "note": "too few trades to trust"}
    nets = np.array([t["net"] for t in sorted(trades, key=lambda x: x["exit_time"])])
    cum = np.cumsum(nets)
    dd = float(np.max(np.maximum.accumulate(np.concatenate([[0], cum]))[1:] - cum)) if n else 0.0
    wins, losses = nets[nets > 0].sum(), -nets[nets <= 0].sum()
    return {"trades": n, "win_rate": round(float((nets > 0).mean() * 100), 1),
            "avg_net": round(float(nets.mean()), 3), "total_net": round(float(nets.sum()), 2),
            "pf": round(float(wins / losses), 2) if losses > 0 else None, "max_dd": round(max(dd, 0.0), 2),
            "exp_r": round(float(np.mean([t["R"] for t in trades])), 3),
            "avg_hold_h": round(float(np.mean([t["hold_h"] for t in trades])), 2),
            "note": "too few trades to trust" if n < 30 else ""}


def split_metrics(trades, flt=None):
    ts = [t for t in trades if flt is None or flt(t)]
    return {"all": metrics(ts), "train": metrics([t for t in ts if t["period"] == "train"]),
            "test": metrics([t for t in ts if t["period"] == "test"])}


def lookahead_violations(trades):
    bad = 0
    for t in trades:
        s = t["signal_time"]
        bad += t["last5"] + M5 > s or t["last1h"] + H1 > s or t["entry_time"] < s
        bad += (t["f_time"] is not None and t["f_time"] > s) or (t["oi_time"] is not None and t["oi_time"] > s)
    return int(bad)


# ---------------------------------------------------------------- independent hand checks (verification only)
def hand_ema(vals, span):
    a, y = 2 / (span + 1), vals[0]
    for v in vals[1:]:
        y = a * v + (1 - a) * y
    return y


def hand_rsi(closes, n=14):
    g = l = None
    for p, c in zip(closes, closes[1:]):
        up, dn = max(c - p, 0), max(p - c, 0)
        g = up if g is None else g + (up - g) / n
        l = dn if l is None else l + (dn - l) / n
    return 100.0 if not l else 100 - 100 / (1 + g / l)


def hand_atr(rows, n=14):
    y, prev = None, None
    for r in rows:
        tr = r[2] - r[3] if prev is None else max(r[2] - r[3], abs(r[2] - prev), abs(r[3] - prev))
        y = tr if y is None else y + (tr - y) / n
        prev = r[4]
    return y


def hand_trade(trade, rows5, hold_h):
    """Plain re-computation of a live-style trade from raw candles (independent of resolve_trade)."""
    by_ts = {int(r[0]): r for r in rows5}
    k_ts, d = trade["entry_time"], trade["dir"]
    o = by_ts[k_ts][1]
    entry = o * (1.0005 if d == "LONG" else 0.9995)
    s = 1 if d == "LONG" else -1
    atr = trade["atr"]
    sl, tp1, tp2 = entry - s * 1.8 * atr, entry + s * 2.0 * atr, entry + s * 3.6 * atr
    mv = lambda px: s * (px - entry) / entry * 100
    fee = 0.20 if d == "LONG" else 0.10
    half, t = None, k_ts
    while t < k_ts + hold_h * H1:
        r = by_ts[t]
        hi, lo = r[2], r[3]
        up = lambda lvl: hi >= lvl if s == 1 else lo <= lvl
        down = lambda lvl: lo <= lvl if s == 1 else hi >= lvl
        if half is None:
            if down(sl):
                return {"exit": sl, "reason": "STOP LOSS", "net": mv(sl) - fee, "entry": entry, "exit_ts": t}
            if up(tp1):
                half = mv(tp1) / 2
        if half is not None:
            if down(entry):
                return {"exit": entry, "reason": "BREAKEVEN after TP1", "net": half - fee, "entry": entry, "exit_ts": t}
            if up(tp2):
                return {"exit": tp2, "reason": "TP2 HIT", "net": half + mv(tp2) / 2 - fee, "entry": entry, "exit_ts": t}
        t += M5
    cl = by_ts[t - M5][4]
    g = mv(cl) if half is None else half + mv(cl) / 2
    return {"exit": cl, "reason": "TIME EXIT", "net": g - fee, "entry": entry, "exit_ts": t - M5}


# ---------------------------------------------------------------- report
COLS = ["Trades", "Win %", "Avg net %", "Total net %", "PF", "Max DD %", "Exp R", "Avg hold h"]


def fmt_row(label, m):
    f = lambda v, p="": "–" if v is None else f"{v}{p}"
    note = f" ⚠️ {m['note']}" if m["note"] else ""
    return (f"| {label} | {m['trades']} | {f(m['win_rate'])} | {f(m['avg_net'])} | {f(m['total_net'])} | "
            f"{f(m['pf'])} | {f(m['max_dd'])} | {f(m['exp_r'])} | {f(m['avg_hold_h'])} |{note}")


def table(title, rows, period="all"):
    head = f"\n**{title}** ({ {'all': 'full 3 months', 'train': 'training months', 'test': 'unseen month'}[period] })\n\n"
    head += "| Variant | " + " | ".join(COLS) + " |\n|" + "---|" * (len(COLS) + 1) + "\n"
    return head + "\n".join(fmt_row(lbl, m[period]) for lbl, m in rows) + "\n"


def tt(m):
    """Compact 'train → unseen' text for a split-metrics dict."""
    def one(x):
        if not x["trades"]:
            return "0 trades"
        return f"{x['trades']} trades, {x['total_net']:+.1f}% total, {x['exp_r']:+.3f} R/trade, win {x['win_rate']}%"
    return f"train: {one(m['train'])} → unseen: {one(m['test'])}"


def overfit_warn(m):
    a, b = m["train"]["exp_r"], m["test"]["exp_r"]
    if a is not None and b is not None and a > 0 and b < 0.5 * a:
        return " ⚠️ unseen-month expectancy is below 50% of training (likely overfitting / luck)"
    return ""


# ---------------------------------------------------------------- main
def build_coins(n, refresh=False):
    path = os.path.join(DATA, "coins.json")
    if os.path.exists(path) and not refresh:
        with open(path) as f:
            return json.load(f)
    s = wr.BinanceSpot()
    s.connect()
    coins = build_coin_list(s.tickers_24h(), n, tradable=s.markets, exclude_symbols=s.stock_tokens)
    if "BTC/USDT" not in coins:
        coins.append("BTC/USDT")
    doc = {"built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "coins": coins}
    os.makedirs(DATA, exist_ok=True)
    with open(path, "w") as f:
        json.dump(doc, f)
    return doc


def load_derivs(coins, start_ms, end_ms, log):
    hl = hl_universe()
    res = {}

    def one(sym):
        base = norm_base(sym)
        f, f_src = [], None
        if base in hl:
            f, f_src = hl_funding(hl[base], start_ms - 86_400_000, end_ms), "Hyperliquid"
        if len(f) < 100:
            g = okx_funding(base, start_ms, end_ms)
            if len(g) > len(f) // 8:
                f, f_src = g, "OKX"
        oi_sym, oi = bn_metrics(base, start_ms - 86_400_000, end_ms)
        return sym, {"funding": f if f else [], "funding_src": f_src if f else None, "oi": oi,
                     "oi_src": f"Binance futures metrics ({oi_sym})" if oi else None}
    with ThreadPoolExecutor(6) as p:
        for sym, d in p.map(one, coins):
            res[sym] = d
    log(f"derivatives loaded for {len(res)} coins")
    return res


def run(args):
    t_start = time.time()
    log = lambda *a: print(f"[{time.time() - t_start:6.1f}s]", *a, flush=True)
    start_ms, train_ms, end_ms = ms(args.start), ms(args.train_end), ms(args.end)
    warm = start_ms - WARMUP_DAYS * 86_400_000
    coin_doc = build_coins(args.coins, args.refresh_coins)
    coins = coin_doc["coins"][:args.coins] + ([] if "BTC/USDT" in coin_doc["coins"][:args.coins] else ["BTC/USDT"])
    log(f"{len(coins)} coins (list built {coin_doc['built_at']})")
    with ThreadPoolExecutor(12) as p:
        kl = dict(zip([(c, tf) for c in coins for tf in ("5m", "1h")],
                      p.map(lambda ct: load_klines(ct[0], ct[1], warm, end_ms), [(c, tf) for c in coins for tf in ("5m", "1h")])))
    log("candles loaded")
    deriv = load_derivs(coins, start_ms, end_ms, log)
    reg_t, reg_v = btc_regime_series(kl[("BTC/USDT", "1h")], start_ms, end_ms)
    meta = {"_train_end": train_ms}
    jobs, n_bars = [], int((end_ms - start_ms) / M5)
    for c in coins:
        r5, r1 = kl[(c, "5m")], kl[(c, "1h")]
        d = deriv[c]
        f = np.array(d["funding"], dtype=float).reshape(-1, 2)
        oi = np.array(d["oi"], dtype=float).reshape(-1, 2)
        first = int(r5[0, 0]) if len(r5) else None
        meta[c] = {"bars5": len(r5), "first": iso(first) if first else None,
                   "partial": not len(r5) or first > warm + 86_400_000, "has_perp": bool(d["funding_src"] or d["oi_src"]),
                   "funding_src": d["funding_src"], "oi_src": d["oi_src"]}
        jobs.append((c, r5, r1, start_ms, end_ms, f[:, 0], f[:, 1], oi[:, 0], oi[:, 1], reg_t, reg_v))
    import inspect
    src = "".join(inspect.getsource(f) for f in (add_indicators, to_df, validate_candles, evaluate_pair, wr.score_sides,
                                                 wr.deriv_adjustment, wr.grade_for, btc_regime, eval_coin))
    code_hash = hashlib.sha256((src + str((WIN5, WIN1, wr.A_MIN, wr.B_MIN))).encode()).hexdigest()[:12]
    evals, todo = {}, []
    for jb in jobs:
        key = hashlib.sha256((code_hash + jb[0]).encode() + jb[1].tobytes() + jb[2].tobytes() + jb[5].tobytes()
                             + jb[7].tobytes() + jb[8].tobytes() + str((start_ms, end_ms)).encode()).hexdigest()[:16]
        path = os.path.join(DATA, "eval", f"{jb[0].replace('/', '')}_{key}.pkl")
        if os.path.exists(path):
            with open(path, "rb") as f_:
                evals[jb[0]] = pickle.load(f_)
        else:
            todo.append((jb, path))
    log(f"evaluating {len(todo)} coins with the live scoring code ({len(evals)} cached)")
    if todo:
        with ProcessPoolExecutor(args.workers) as p:
            for (sym, out), (_, path) in zip(p.map(eval_coin, [x[0] for x in todo]), todo):
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb") as f_:
                    pickle.dump(out, f_)
                evals[sym] = out
    log("evaluation done")
    for c in coins:
        ev = evals[c]
        meta[c]["bars_scored"] = len(ev["t"])
        fc = sum(x is not None for x in ev["f8"]) / max(1, len(ev["t"]))
        oc = sum(x is not None for x in ev["oic"]) / max(1, len(ev["t"]))
        meta[c].update(funding_cov=round(fc, 3), oi_cov=round(oc, 3), deriv_ok=fc >= 0.8 and oc >= 0.8)
    candles = {c: kl[(c, "5m")].tolist() for c in coins}
    covered = {c for c in coins if meta[c]["deriv_ok"]}

    base = {"name": "live"}
    variants = [base] + [{"name": f"hold_{h}h", "hold": {"A": h, "B": h}} for h in HOLDS] + \
               [{"name": f"style_{s}", "style": s} for s in "bcd"] + \
               [{"name": "live_deriv_covered", "coins": covered},
                {"name": "deriv_score_on", "use_deriv": True, "coins": covered},
                {"name": "btc_regime_filter", "regime_filter": True},
                {"name": "whatif_ob_thresholds_68_55", "thr": WHATIF_THR}]
    sims, unresolved = {}, {}
    with ThreadPoolExecutor(1) as _:
        for v in variants:
            sims[v["name"]], unresolved[v["name"]] = simulate(v, evals, candles, meta)
    log(f"{len(variants)} variants simulated")
    R = {name: split_metrics(tr) for name, tr in sims.items()}
    live = sims["live"]
    by = lambda tr, f: split_metrics(tr, f)
    grade_rows = {g: by(live, lambda t, g=g: t["grade"] == g) for g in "AB"}
    side_rows = {s: by(live, lambda t, s=s: t["dir"] == s) for s in ("LONG", "SHORT")}
    hold_rows = {g: {h: by(sims[f"hold_{h}h"], lambda t, g=g: t["grade"] == g) for h in HOLDS} for g in "AB"}
    coin_rows = {c: by(live, lambda t, c=c: t["coin"] == c) for c in coins}

    def best(cands):
        ok = [(k, m) for k, m in cands if m["train"]["trades"] >= 30 and m["train"]["exp_r"] is not None]
        return max(ok, key=lambda km: km[1]["train"]["exp_r"]) if ok else None
    pick = {}
    pick["grades"] = {g for g in "AB" if grade_rows[g]["train"]["trades"] >= 30 and grade_rows[g]["train"]["exp_r"] > 0}
    pick["sides"] = {s for s in ("LONG", "SHORT") if side_rows[s]["train"]["trades"] >= 30 and side_rows[s]["train"]["exp_r"] > 0}
    pick["hold"] = {g: (best([(h, hold_rows[g][h]) for h in HOLDS]) or (LIVE_HOLD[g], None))[0] for g in "AB"}
    pick["style"] = (best([("a", R["live"])] + [(s, R[f"style_{s}"]) for s in "bcd"]) or ("a", None))[0]
    pick["regime_filter"] = (R["btc_regime_filter"]["train"]["exp_r"] or -9) > (R["live"]["train"]["exp_r"] or -9)
    pick["deriv_score"] = (R["deriv_score_on"]["train"]["exp_r"] or -9) > (R["live_deriv_covered"]["train"]["exp_r"] or -9)
    pick["drop"] = sorted(c for c in coins if coin_rows[c]["train"]["trades"] >= 10 and coin_rows[c]["train"]["total_net"] < 0)
    tradeable = bool(pick["grades"] and pick["sides"])
    rec = {"name": "recommended", "hold": pick["hold"], "style": pick["style"],
           "regime_filter": pick["regime_filter"], "use_deriv": pick["deriv_score"],
           "grades": pick["grades"] or {max("AB", key=lambda g: grade_rows[g]["train"]["exp_r"] or -9)},
           "sides": pick["sides"] or {max(("LONG", "SHORT"), key=lambda s: side_rows[s]["train"]["exp_r"] or -9)},
           "coins": (covered if pick["deriv_score"] else set(coins)) - set(pick["drop"])}
    sims["recommended"], unresolved["recommended"] = simulate(rec, evals, candles, meta)
    R["recommended"] = split_metrics(sims["recommended"])
    log("walk-forward done")

    all_trades = [t for tr in sims.values() for t in tr]
    la = lookahead_violations(all_trades)
    assert la == 0, f"look-ahead violations: {la}"
    checks = hand_checks(live, candles, sims, evals, kl)
    profitable_unseen = sorted(k for k, m in R.items() if m["test"]["total_net"] > 0 and m["test"]["trades"] > 0)
    robust_unseen = [k for k in profitable_unseen if R[k]["test"]["trades"] >= 30]
    runtime = round(time.time() - t_start, 1)
    results = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "runtime_s": runtime,
               "period": {"start": args.start, "train_end": args.train_end, "end": args.end},
               "coin_list": coin_doc, "coins_tested": coins, "meta": {k: v for k, v in meta.items() if k != "_train_end"},
               "variants": R, "unresolved_at_end": unresolved, "grade": grade_rows, "side": side_rows,
               "hold": {g: {str(h): m for h, m in hs.items()} for g, hs in hold_rows.items()}, "coins": coin_rows,
               "picks": {k: sorted(v) if isinstance(v, set) else v for k, v in pick.items()},
               "recommended_variant": {k: (sorted(v) if isinstance(v, set) else v) for k, v in rec.items()},
               "tradeable": tradeable, "profitable_unseen": profitable_unseen, "robust_unseen": robust_unseen,
               "lookahead_violations": la, "signals_checked": len(all_trades), "hand_checks": checks,
               "deriv_covered": sorted(covered), "code_hash": code_hash}
    out = args.out
    with open(os.path.join(out, "backtest_results.json"), "w") as f:
        json.dump(results, f, indent=1, default=lambda x: sorted(x) if isinstance(x, set) else str(x))
    keys = list(all_trades[0].keys()) if all_trades else []
    with open(os.path.join(out, "backtest_trades.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for t in all_trades:
            w.writerow({k: (iso(v) if k in ("signal_time", "entry_time", "exit_time") else
                            round(v, 6) if isinstance(v, float) else v) for k, v in t.items()})
    with open(os.path.join(out, "backtest_report.md"), "w") as f:
        f.write(report(results, R, grade_rows, side_rows, hold_rows, coin_rows, meta, coins, pick, tradeable))
    log(f"done in {runtime}s · {len(all_trades)} trades across variants · look-ahead violations {la}")
    return results


def hand_checks(live, candles, sims, evals, kl):
    """JRN-57/58: recompute 5 trades and 2 signals' indicators from raw candles."""
    want = [("STOP LOSS", lambda t: t["reason"] == "STOP LOSS" and t["dir"] == "LONG"),
            ("TP1 → TP2", lambda t: t["reason"] == "TP2 HIT"),
            ("TP1 → breakeven", lambda t: t["reason"].startswith("BREAKEVEN")),
            ("SHORT", lambda t: t["dir"] == "SHORT"),
            ("TIME EXIT", lambda t: t["reason"] == "TIME EXIT")]
    out = {"trades": [], "signals": []}
    used = set()
    for label, fn in want:
        t = next((x for x in live if fn(x) and id(x) not in used), None)
        if not t:
            out["trades"].append({"case": label, "found": False})
            continue
        used.add(id(t))
        h = hand_trade(t, candles[t["coin"]], LIVE_HOLD[t["grade"]])
        out["trades"].append({"case": label, "found": True, "coin": t["coin"], "dir": t["dir"], "grade": t["grade"],
                              "signal": iso(t["signal_time"]), "entry_bar_open_raw": candles[t["coin"]][
                                  [int(r[0]) for r in candles[t["coin"]]].index(t["entry_time"])][1],
                              "entry_bt": t["entry"], "entry_hand": h["entry"], "reason_bt": t["reason"],
                              "reason_hand": h["reason"], "exit_bt": t["exit"], "exit_hand": h["exit"],
                              "net_bt": round(t["net"], 4), "net_hand": round(h["net"], 4),
                              "match": abs(t["net"] - h["net"]) < 0.01 and t["reason"] == h["reason"]})
    for g in "AB":
        t = next((x for x in live if x["grade"] == g), None)
        if not t:
            continue
        rows5, rows1 = kl[(t["coin"], "5m")], kl[(t["coin"], "1h")]
        i5 = int(np.searchsorted(rows5[:, 0], t["last5"]))
        i1 = int(np.searchsorted(rows1[:, 0], t["last1h"]))
        w5, w1 = rows5[i5 - WIN5 + 1: i5 + 1], rows1[i1 - WIN1 + 1: i1 + 1]
        ev = evals[t["coin"]]
        n = ev["t"].index(t["signal_time"])
        out["signals"].append({"grade": g, "coin": t["coin"], "signal": iso(t["signal_time"]),
                               "last_5m_candle": iso(t["last5"]), "last_1h_candle": iso(t["last1h"]),
                               "rsi5_bt": ev["rsi5"][n], "rsi5_hand": round(hand_rsi(list(w5[:, 4])), 1),
                               "rsi1h_bt": ev["rsi1h"][n], "rsi1h_hand": round(hand_rsi(list(w1[:, 4])), 1),
                               "atr_bt": round(ev["atr"][n], 8), "atr_hand": round(hand_atr(w5.tolist()), 8),
                               "ema21_1h_hand": round(hand_ema(list(w1[:, 4]), 21), 6),
                               "ema50_1h_hand": round(hand_ema(list(w1[:, 4]), 50), 6),
                               "close_1h": float(w1[-1, 4])})
    return out


def report(res, R, grade_rows, side_rows, hold_rows, coin_rows, meta, coins, pick, tradeable):
    live, rec = R["live"], R["recommended"]
    lt, rt = live["test"], rec["test"]
    pu, ru = res["profitable_unseen"], res["robust_unseen"]
    L = []
    if not pu:
        L.append("**NO — none of the tested settings made money after fees on the unseen month. Do not trade real "
                 "money with these rules.**")
    elif not ru or rt["total_net"] <= 0 or not tradeable:
        L.append("**NOT PROVEN — the rules did not reliably make money after fees on the unseen month (only small or "
                 "cherry-picked variants were positive). Do not trade real money with these rules yet.**")
    else:
        L.append(f"**YES, cautiously — the recommended settings made {rt['total_net']:+.1f}% (sum of trades) after fees "
                 f"on the unseen month over {rt['trades']} trades; this is 1 month of data, so start very small.**")
    L.append(f"- Live rules as they are now: {lt['trades']} trades on the unseen month, {lt['total_net']:+.1f}% total "
             f"after fees, win rate {lt['win_rate']}%; over all 3 months {live['all']['total_net']:+.1f}% in "
             f"{live['all']['trades']} trades.")
    ga = {g: grade_rows[g]["all"] for g in "AB"}
    bg = max("AB", key=lambda g: ga[g]["exp_r"] if ga[g]["exp_r"] is not None else -9)
    sa = {s: side_rows[s]["all"] for s in ("LONG", "SHORT")}
    bs = max(sa, key=lambda s: sa[s]["exp_r"] if sa[s]["exp_r"] is not None else -9)
    L.append(f"- Better grade: {bg}-GRADE ({ga[bg]['exp_r']:+.3f} R per trade vs {ga['B' if bg == 'A' else 'A']['exp_r']}); "
             f"better side: {bs} ({sa[bs]['exp_r']:+.3f} R vs {sa['SHORT' if bs == 'LONG' else 'LONG']['exp_r']}) — "
             f"R = result as a multiple of the stop-loss distance.")
    L.append(f"- Best hold time picked on the first 2 months: A {pick['hold']['A']} h, B {pick['hold']['B']} h; best exit "
             f"style: {STYLE_NAMES[pick['style']]}.")
    L.append(f"- Recommendation: {'test the recommended settings with tiny size only' if (pu and ru and tradeable and rt['total_net'] > 0) else 'keep the bot as an alert/learning tool only and do not risk real money on it'}"
             f" (recommended settings: {tt(rec)}).")
    out = "# War Room v10 — 3-month backtest report\n\n## Summary (plain English)\n\n" + "\n".join(L) + "\n"
    p = res["period"]
    out += (f"\n## How this was tested\n\n- Period: {p['start']} → {p['end']} (training = first 2 months to "
            f"{p['train_end']}, unseen test = last month). {len(coins)} coins: today's live top-50 list + BTC.\n"
            "- Each 5-minute candle close was scored by the **live bot's own code** (imported from war_room_bot.py) on the "
            "exact candles the live bot would have seen (309 closed 5m + 259 closed 1h). Order book = 0 (no history), "
            "AI review skipped (it cannot be replayed: the model's answers are not reproducible and the free key's rate "
            "limit cannot serve tens of thousands of setups).\n"
            "- Entry = next 5m candle open + 0.05% slippage against you. Same 1 h cooldown, one trade per coin, "
            "stop-loss-first rule, half at TP1 then stop to breakeven, fees LONG 0.20% / SHORT 0.10% round trip.\n"
            "- 'Total net %' = simple sum of per-trade % results (each trade the same size, no compounding). Max DD = "
            "largest drop of that running sum. PF = profit factor (gross wins ÷ gross losses). Exp R = average result "
            "divided by the stop-loss distance.\n"
            f"- Look-ahead check: {res['signals_checked']} simulated trades checked, **{res['lookahead_violations']} used "
            "a candle or derivatives value that was not yet available**.\n")
    out += "\n## Results\n"
    out += table("A-GRADE vs B-GRADE (live rules)", [(f"{g}-GRADE", grade_rows[g]) for g in "AB"])
    out += table("LONG vs SHORT (live rules)", [(s, side_rows[s]) for s in ("LONG", "SHORT")])
    for g in "AB":
        out += table(f"Max hold time — {g}-GRADE trades", [(f"{h} h", hold_rows[g][h]) for h in HOLDS])
    out += table("Exit styles (live hold times A 24 h / B 4 h)",
                 [(STYLE_NAMES["a"], R["live"])] + [(STYLE_NAMES[s], R[f"style_{s}"]) for s in "bcd"])
    out += table("Derivatives variants", [("Off (live default, all coins)", R["live"]),
                                          ("BTC-regime filter (all coins)", R["btc_regime_filter"]),
                                          ("Off — coins with derivatives history only", R["live_deriv_covered"]),
                                          ("USE_DERIV_SCORE on — same coins", R["deriv_score_on"])])
    excl = [c for c in coins if not meta[c]["deriv_ok"]]
    out += (f"\nCoins excluded from the USE_DERIV_SCORE comparison (no full funding + open-interest history): "
            f"{', '.join(excl) or 'none'}. They are still in all other results.\n")
    out += ("\n### What-if: order-book thresholds (NOT the main result)\nThe order book is worth 15 points live but has "
            "no history, so the top possible score here is 85. This what-if scales the grade lines to that maximum "
            "(A ≥ 68, B ≥ 55). Treat it as a sensitivity check only.\n")
    out += table("What-if thresholds", [("Live thresholds (80/65)", R["live"]),
                                       ("What-if thresholds (68/55)", R["whatif_ob_thresholds_68_55"])])
    out += "\n### Per-coin ranking (live rules, full period, best first)\n\n| # | Coin | Trades | Total net % | Win % | Exp R | Train net % | Unseen net % | Flags |\n|---|---|---|---|---|---|---|---|---|\n"
    ranked = sorted(coins, key=lambda c: -coin_rows[c]["all"]["total_net"])
    for i, c in enumerate(ranked, 1):
        m = coin_rows[c]
        flags = []
        if m["all"]["trades"] < 10:
            flags.append("not enough data")
        if meta[c]["partial"]:
            flags.append(f"partial history (from {meta[c]['first']})")
        if not meta[c]["has_perp"]:
            flags.append("no perp found: SHORTs skipped")
        out += (f"| {i} | {c} | {m['all']['trades']} | {m['all']['total_net']:+.2f} | {m['all']['win_rate'] or '–'} | "
                f"{m['all']['exp_r'] if m['all']['exp_r'] is not None else '–'} | {m['train']['total_net']:+.2f} | "
                f"{m['test']['total_net']:+.2f} | {', '.join(flags)} |\n")
    out += "\n## Walk-forward check (choose on months 1–2, test on month 3)\n\n"
    out += table("Live rules vs settings chosen on training data", [("Live rules", R["live"]), ("Chosen settings", R["recommended"])], "train")
    out += table("Same settings on the unseen month", [("Live rules", R["live"]), ("Chosen settings", R["recommended"])], "test")
    out += (overfit_warn(R["recommended"]).strip() or "Unseen-month expectancy is at least half of training.") + "\n"
    out += "\n## Recommended settings (each with training → unseen-month numbers)\n\n"
    if not tradeable:
        out += ("- **No grade/side combination was profitable on the training months**, so no real-money setting is "
                "recommended. The 'chosen settings' below are the least-bad choices, shown only so you can see them.\n")
    r = res["recommended_variant"]
    out += f"- Grades to trade: {', '.join(r['grades'])} — " + "; ".join(f"{g}: {tt(grade_rows[g])}" for g in "AB") + "\n"
    out += f"- Sides: {', '.join(r['sides'])} — " + "; ".join(f"{s}: {tt(side_rows[s])}" for s in ("LONG", "SHORT")) + "\n"
    for g in "AB":
        h = pick["hold"][g]
        out += f"- Max hold {g}-GRADE: {h} h — {tt(hold_rows[g][h])}{overfit_warn(hold_rows[g][h])}\n"
    st = pick["style"]
    out += f"- Exit style: {STYLE_NAMES[st]} — {tt(R['live'] if st == 'a' else R[f'style_{st}'])}\n"
    out += f"- BTC-regime filter: {'ON' if pick['regime_filter'] else 'OFF'} — on: {tt(R['btc_regime_filter'])}; off: {tt(R['live'])}\n"
    out += (f"- USE_DERIV_SCORE: {'ON' if pick['deriv_score'] else 'OFF'} — on: {tt(R['deriv_score_on'])}; off (same "
            f"coins): {tt(R['live_deriv_covered'])}\n")
    out += (f"- Coins to drop (≥10 training trades and losing on training): {', '.join(pick['drop']) or 'none'}. "
            "Keep the rest (most have too few trades to judge).\n")
    out += f"- **All of the above combined:** {tt(R['recommended'])}{overfit_warn(R['recommended'])}\n"
    out += f"- Variants positive on the unseen month: {', '.join(pu) or 'none'} (with ≥30 unseen trades: {', '.join(ru) or 'none'}).\n"
    out += ("\n## Known biases (read before trusting any number)\n\n"
            "- **Survivorship bias:** coins are today's top-50 by volume; coins that crashed out of the list during the "
            "3 months are missing, and coins that pumped into it are over-represented.\n"
            "- **No order book:** the 15 order-book points are always 0, so fewer setups reach the live grade lines than "
            "live (see the what-if).\n- **No AI review:** live alerts the AI rejects are included here.\n"
            "- **Slippage/fills:** a flat 0.05% adverse slippage on entry; stops and targets assumed filled exactly at "
            "their price (real stops can slip further in fast moves). Fees: Binance standard, no BNB discount.\n"
            "- **Candle resolution:** trades are checked on 5m candles; when one candle touches both the stop and a "
            "target the stop is assumed first (pessimistic, same as live).\n"
            "- **Derivatives data:** funding from Hyperliquid (OKX if missing); open interest from Binance futures "
            "metrics (the live bot uses Hyperliquid OI — same idea, different exchange).\n"
            "- **One period:** 3 months of one market regime; 1 unseen month is a small sample.\n")
    out += "\n### Derivatives coverage per coin\n\n| Coin | Funding source | OI source | Funding cov. | OI cov. |\n|---|---|---|---|---|\n"
    for c in coins:
        m = meta[c]
        out += f"| {c} | {m['funding_src'] or 'none'} | {m['oi_src'] or 'none'} | {m['funding_cov']:.0%} | {m['oi_cov']:.0%} |\n"
    out += "\n## Appendix — hand-checked trades and signals\n\nRecomputed with separate plain code straight from the cached raw candles.\n\n"
    out += "| Case | Coin | Dir | Signal (UTC) | Entry candle open | Entry bt / hand | Exit bt / hand | Reason bt / hand | Net % bt / hand | Match |\n|---|---|---|---|---|---|---|---|---|---|\n"
    for h in res["hand_checks"]["trades"]:
        if not h["found"]:
            out += f"| {h['case']} | – | – | not found in this run | | | | | | |\n"
            continue
        out += (f"| {h['case']} | {h['coin']} | {h['dir']} {h['grade']} | {h['signal']} | {h['entry_bar_open_raw']:.8g} | "
                f"{h['entry_bt']:.8g} / {h['entry_hand']:.8g} | {h['exit_bt']:.8g} / {h['exit_hand']:.8g} | "
                f"{h['reason_bt']} / {h['reason_hand']} | {h['net_bt']:+.4f} / {h['net_hand']:+.4f} | "
                f"{'✅' if h['match'] else '❌'} |\n")
    out += "\n| Signal | Coin | Signal time | Last 5m candle | Last 1h candle | RSI5 bt/hand | RSI1h bt/hand | ATR bt/hand | 1h close vs EMA21/EMA50 (hand) |\n|---|---|---|---|---|---|---|---|---|\n"
    for s in res["hand_checks"]["signals"]:
        out += (f"| {s['grade']}-GRADE | {s['coin']} | {s['signal']} | {s['last_5m_candle']} | {s['last_1h_candle']} | "
                f"{s['rsi5_bt']}/{s['rsi5_hand']} | {s['rsi1h_bt']}/{s['rsi1h_hand']} | {s['atr_bt']:.6g}/{s['atr_hand']:.6g} | "
                f"{s['close_1h']:.6g} vs {s['ema21_1h_hand']:.6g}/{s['ema50_1h_hand']:.6g} |\n")
    out += f"\nRun time {res['runtime_s']} s · code hash {res['code_hash']} · generated {res['generated']}\n"
    return out


STYLE_NAMES = {"a": "(a) live: half at TP1, breakeven, rest to TP2", "b": "(b) full exit at TP1",
               "c": "(c) half at TP1 + 1-ATR trailing stop", "d": "(d) half at TP1 + breakeven, hold to max time"}


def main(argv=None):
    ap = argparse.ArgumentParser(description="War Room v10 backtest")
    ap.add_argument("--start", default=START)
    ap.add_argument("--train-end", default=TRAIN_END)
    ap.add_argument("--end", default=END)
    ap.add_argument("--coins", type=int, default=50)
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    ap.add_argument("--refresh-coins", action="store_true")
    ap.add_argument("--out", default=ROOT)
    run(ap.parse_args(argv))
    return 0


if __name__ == "__main__":
    sys.exit(main())
