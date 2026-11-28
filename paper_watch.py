"""Live paper-watch of the two Phase 3 near misses (no alerts): S1 4h breakout longs and S5 top-3 momentum, majors.
Decisions come from research.py's own functions (s1_setup, s5_matrix, s5_top, net), so they match the research."""
import copy

import numpy as np
import requests

import research as rs

BINANCE = "https://data-api.binance.vision"
HL = "https://api.hyperliquid.xyz/info"
DEFAULTS = {"s1": {"n": 20, "exit": "channel", "btc": True}, "s5": {"k": 3, "reb": "daily"}}
FOUR_H, DAY = rs.TF["4h"], rs.TF["1d"]
MARGIN_MS = 120_000  # wait 2 min after a candle closes before using it
KEEP = 60


class WatchCoin(rs.Coin):
    def __init__(self, base, bars, fund=None):  # no super(): we get 4h/1d bars directly
        self.base, self.bars, self.fsrc = base, bars, "hyperliquid"
        self.ft, self.fr = fund if fund is not None else (np.array([], np.int64), np.array([]))
        self.fn = self.fr
        self.first = int(next(iter(bars.values()))[0, 0])


VALID = {"n": lambda v: type(v) is int and 4 <= v <= 200, "exit": lambda v: v in ("channel", "chandelier"),
         "btc": lambda v: type(v) is bool, "k": lambda v: type(v) is int and 1 <= v <= 10,
         "reb": lambda v: v == "daily"}  # weekly can't be anchored like research's data start, so live is daily only


def params(over_s1=None, over_s5=None):
    """Phase 3 choices, overridable via BOT_SETTINGS WATCH_S1 / WATCH_S5 (unknown keys or bad values ignored)."""
    pick = lambda d, o: dict(d, **{k: v for k, v in (o or {}).items() if k in d and VALID[k](v)})
    return pick(DEFAULTS["s1"], over_s1), pick(DEFAULTS["s5"], over_s5)


def new_state(now):
    return {"started": int(now), "announced": False, "last_ok": 0,
            "s1": {"open": {}, "closed": [], "seen": {}, "free": {}, "tot_R": 0.0, "n": 0},
            "s5": {"held": {}, "turn": 0.0, "equity": 1.0, "last_day": 0, "events": []}}


def due(st, now):
    if not st:
        return True
    return (now * 1000 - MARGIN_MS) // FOUR_H > (st["last_ok"] * 1000 - MARGIN_MS) // FOUR_H


# ---------------------------------------------------------------- data (replaced in tests)
def get_klines(base, interval, limit, now):
    """Binance spot candles: (closed rows [t,o,h,l,c,v], next open or None)."""
    r = requests.get(f"{BINANCE}/api/v3/klines", params={"symbol": f"{base}USDT", "interval": interval,
                                                         "limit": limit}, timeout=10)
    r.raise_for_status()
    rows = r.json()
    closed = [x for x in rows if int(x[6]) < now * 1000]
    nxt = float(rows[len(closed)][1]) if len(rows) == len(closed) + 1 else None
    return np.array([[float(v) for v in x[:6]] for x in closed]), nxt


def get_funding(base, start_ms):
    """Hyperliquid hourly funding since start_ms: (settle_ms array, rate paid per settlement)."""
    out, t = [], int(start_ms)
    for _ in range(10):
        r = requests.post(HL, json={"type": "fundingHistory", "coin": base, "startTime": t}, timeout=10)
        r.raise_for_status()
        rows = r.json()
        out += [(int(x["time"]), float(x["fundingRate"])) for x in rows]
        if len(rows) < 500:
            break
        t = int(rows[-1]["time"]) + 1
    return np.array([x[0] for x in out], np.int64), np.array([x[1] for x in out], float)


# ---------------------------------------------------------------- S1: 4h breakout, longs
def _closed(t, n):
    return not (t["why"] == "time" and t["j"] == n - 1 and t["j"] - t["i"] + 1 < rs.S1_MAX_BARS)


def _record(st, t, now):
    """Closed trade: it frees the coin like research's walk (j + 1); only LONG trades are reported."""
    st["free"][t["coin"]] = int(t["exit_t"] + FOUR_H if t["why"] == "channel" else t["exit_t"])
    if t["side"] != "LONG":
        return
    net = rs.net(t, "F")
    R = net / max(t["risk"], 1e-6)
    st["closed"] = (st["closed"] + [{"coin": t["coin"], "entry_t": t["entry_t"], "exit_t": t["exit_t"],
                                     "entry": t["entry"], "exit": t["exit"], "why": t["why"], "net": round(net, 4),
                                     "R": round(R, 4), "at": int(now)}])[-KEEP:]
    st["tot_R"], st["n"] = st["tot_R"] + R, st["n"] + 1


def s1_update(st, p, bars, funding, start_ms, now):
    """Same walk as research S1 (both sides share one slot per coin, as in Phase 3); shorts are shadow trades
    that only block the coin, longs are the watched strategy."""
    btc = bars["BTC"]
    for base, a in bars.items():
        n = len(a)
        if n < 3:
            continue
        cur, seen = st["open"].get(base), st["seen"].get(base, 0)
        L, S, _ = rs.s1_setup(WatchCoin(base, {"4h": a}), p, btc)
        pend = [i for i in range(n - 1) if a[i, 0] > seen and (L[i] or S[i]) and a[i + 1, 0] >= start_ms]
        if cur or pend:
            longs = (cur and cur.get("side", "LONG") == "LONG") or any(L[i] for i in pend)
            first = cur["entry_t"] if cur else int(a[pend[0] + 1, 0])
            coin = WatchCoin(base, {"4h": a}, funding(base, first - 3_600_000) if longs else None)
            make = rs.s1_setup(coin, p, btc)[2]
            if cur:
                ix = np.nonzero(a[:, 0] == cur["sig_t"])[0]
                t = make(int(ix[0]), cur.get("side", "LONG")) if len(ix) else None
                if t and _closed(t, n):
                    _record(st, t, now)
                    del st["open"][base]
                    cur = None
            for i in pend:
                if cur or a[i, 0] < st["free"].get(base, 0):
                    continue
                side = "LONG" if L[i] else "SHORT"
                t = make(i, side)
                if _closed(t, n):
                    _record(st, t, now)
                else:
                    cur = st["open"][base] = {"sig_t": int(a[i, 0]), "entry_t": t["entry_t"], "entry": t["entry"],
                                              "side": side}
        st["seen"][base] = int(a[n - 2, 0])


# ---------------------------------------------------------------- S5: top-3 momentum, spot
def s5_update(st, p, daily, nxt_open, start_ms, now):
    # research also required the coin to exist at the next rebalance (unknowable live); a coin with no next open
    # is left out here, so a held one counts as sold at 0 % for that day (edge case: delisting / data gap)
    bases = [b for b in daily if nxt_open.get(b) is not None]
    rows = {b: np.vstack([daily[b], [[daily[b][-1, 0] + DAY, nxt_open[b], np.nan, np.nan, np.nan, 0]]]) for b in bases}
    coins = [WatchCoin(b, {"1d": rows[b]}) for b in bases]
    days, btc, sma50, O, C, _ = rs.s5_matrix(coins)
    fee = rs.COST["S"]["long"] + rs.SLIP
    for d in range(31, len(days) - 1):
        if days[d] <= st["last_day"] or days[d + 1] < start_ms:
            continue
        idx = {c.base: i for i, c in enumerate(coins)}
        held, net = st["held"], None
        if held or st["turn"]:
            gross = sum(h["w"] * (O[idx[b], d + 1] / h["px"] - 1) * 100 for b, h in held.items() if b in idx)
            net = gross - st["turn"] * fee
            st["equity"] *= 1 + net / 100
        top, _ = rs.s5_top(C, d, np.isfinite(O[:, d + 1]), btc, sma50, p["k"])
        new = {coins[x].base: {"w": 1 / p["k"], "px": float(O[x, d + 1])} for x in top}
        st["turn"] = sum(abs(new.get(b, {}).get("w", 0) - held.get(b, {}).get("w", 0)) for b in set(new) | set(held))
        st["events"] = (st["events"] + [{"t": int(days[d + 1]), "at": int(now), "bought": sorted(set(new) - set(held)),
                                         "sold": sorted(set(held) - set(new)), "net_prev": net}])[-KEEP:]
        st["held"], st["last_day"] = new, int(days[d])


# ---------------------------------------------------------------- one update (runs off the main cycle)
def update(st, now, over_s1=None, over_s5=None, coins=None):
    """Returns the new paper-watch state; raises if any fetch fails (the caller keeps the old state)."""
    st = copy.deepcopy(st) if st else new_state(now)
    p1, p5 = params(over_s1, over_s5)
    coins = coins or rs.MAJORS
    start_ms = st["started"] * 1000
    bars4 = {b: get_klines(b, "4h", 1000, now)[0] for b in coins}
    daily, nxt = {}, {}
    for b in coins:
        daily[b], nxt[b] = get_klines(b, "1d", 120, now)
    s1_update(st["s1"], p1, bars4, get_funding, start_ms, now)
    s5_update(st["s5"], p5, daily, nxt, start_ms, now)
    st["last_ok"] = int(now)
    return st


# ---------------------------------------------------------------- daily summary block
def summary_lines(st, now, coin_name=str):
    if not st:
        return []
    since_ms = (now - 86400) * 1000
    s1, s5 = st["s1"], st["s5"]
    day = int((now - st["started"]) // 86400) + 1
    out = [] if st["announced"] else ["🔭 Started watching 2 near-miss strategies today."]
    out.append(f"🔭 <b>Watching (paper, not tradeable yet)</b> — day {day}")
    longs = {b: v for b, v in s1["open"].items() if v.get("side", "LONG") == "LONG"}
    opened = sum(1 for v in longs.values() if v["entry_t"] > since_ms) + \
        sum(1 for t in s1["closed"] if t["entry_t"] > since_ms)
    closed = [t for t in s1["closed"] if t["exit_t"] > since_ms]
    hold1 = ", ".join(sorted(coin_name(b) for b in longs)) or "nothing"
    out.append(f"  • 4h breakout (buy, majors): holding {hold1} · 24 h: opened {opened}, closed {len(closed)}"
               + (f" ({sum(t['net'] for t in closed):+.2f}%)" if closed else "")
               + f" · since start: {s1['tot_R']:+.2f}% at 1% risk per trade after fees ({s1['n']} closed)")
    ev = [e for e in s5["events"] if e["t"] > since_ms]
    chg = "; ".join(f"bought {', '.join(e['bought']) or '—'}, sold {', '.join(e['sold']) or '—'}"
                    for e in ev if e["bought"] or e["sold"])
    hold5 = (", ".join(sorted(coin_name(b) for b in s5["held"])) or "cash (BTC below its 50-day average)"
             if s5["events"] else "nothing yet (first rebalance after the next daily close)")
    out.append(f"  • Top-3 momentum (majors, spot): holding {hold5} · 24 h: {len(ev)} rebalance(s)"
               + (f" ({chg})" if chg else "") + f" · since start: {(s5['equity'] - 1) * 100:+.2f}% after fees")
    if st["last_ok"] and now - st["last_ok"] > 8 * 3600:
        out.append(f"  ⚠️ Watch data not updated for {int((now - st['last_ok']) // 3600)} h (positions kept)")
    return out
