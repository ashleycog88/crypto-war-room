"""Live paper-watch of the two Phase 3 near misses (no alerts): S1 4h breakout longs and S5 top-3 momentum, majors.
Decisions come from research.py's own functions (s1_setup, s5_matrix, s5_top, net), so they match the research."""
import copy

import numpy as np
import requests

import factors as fx
import research as rs

BINANCE = "https://data-api.binance.vision"
HL = "https://api.hyperliquid.xyz/info"
DEFAULTS = {"s1": {"n": 20, "exit": "channel", "btc": True}, "s5": {"k": 3, "reb": "daily"},
            "s6": {"look": 14, "q": 5}, "s8": {"variant": "long", "win": 84, "reb": 7}}  # Part C, training-picked
PORT = {"s6": ("F", "Funding factor (top 50, long low / short high funding, futures)"),
        "s8": ("S", "Vol-managed momentum (top 50, spot)")}
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


def port_state(now):
    return {"started": int(now), "w": {}, "w_prev": {}, "d_t": 0, "equity": 1.0, "scale": 0.0, "last_day": 0,
            "events": [], "notes": []}


def new_state(now):
    return {"started": int(now), "announced": False, "announced4": False, "last_ok": 0,
            "s1": {"open": {}, "closed": [], "seen": {}, "free": {}, "tot_R": 0.0, "n": 0},
            "s5": {"held": {}, "turn": 0.0, "equity": 1.0, "last_day": 0, "events": []},
            "s6": port_state(now), "s8": port_state(now)}


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
    migrate_tot_net(st)  # before this trade is added (no double count)
    st["closed"] = (st["closed"] + [{"coin": t["coin"], "entry_t": t["entry_t"], "exit_t": t["exit_t"],
                                     "entry": t["entry"], "exit": t["exit"], "why": t["why"], "net": round(net, 4),
                                     "R": round(R, 4), "at": int(now)}])[-KEEP:]
    st["tot_R"], st["n"] = st["tot_R"] + R, st["n"] + 1
    st["tot_net"] += net  # % of capital ×20 (each trade uses 1/20, as in edge_v2)
    wk = s1_week(t["exit_t"])
    w = st["wk_net"]
    w[str(wk)] = w.get(str(wk), 0.0) + net / 20
    for k in sorted(w, key=int)[:-30]:
        del w[k]


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


# ---------------------------------------------------------------- S6 / S8: Part C portfolios (factors.py)
def port_update(st, fam, daily, nxt_open, fund, now, eligible=None):
    """factors.py's own decide() and period() on the same calendar rebalance days; one period closes per rebalance.
    Decisions use only `eligible` coins (the current top 50); other fetched coins are only priced (held ones get sold)."""
    p, (model, _) = DEFAULTS[fam], PORT[fam]
    bases = [b for b in daily if nxt_open.get(b) is not None and len(daily[b])]
    coins = []
    for b in bases:
        rows = np.vstack([daily[b], [[daily[b][-1, 0] + DAY, nxt_open[b], np.nan, np.nan, np.nan, np.nan]]])
        c = WatchCoin(b, {"1d": rows}, fund.get(b))
        c.fn = c.fr * 8  # Hyperliquid hourly rate → 8h-equivalent (what the S6 signal ranks)
        coins.append(c)
    mx = fx.matrices(coins)
    elig = [i for i, b in enumerate(bases) if eligible is None or b in eligible]
    dec_bases = [bases[i] for i in elig]
    mx_dec = mx if len(elig) == len(bases) else fx.matrices([coins[i] for i in elig])
    decide, step, _ = fx.s6_decide(mx_dec, p) if fam == "s6" else fx.s8_decide(mx_dec, p)
    days, idx = mx["days"], {b: i for i, b in enumerate(bases)}
    assert np.array_equal(days, mx_dec["days"])
    start_ms = st["started"] * 1000
    for d in range(len(days) - 1):
        if not fx.is_reb(days, d, step) or days[d] <= st["last_day"] or days[d + 1] < start_ms:
            continue
        net = None
        if st["d_t"]:
            dp = int(np.searchsorted(days, st["d_t"]))
            gone = sorted(b for b in st["w"] if b not in idx)
            if gone:  # same as factors.py: a coin without data keeps its last price (no gain or loss here)
                add_note(st, f"{bt_iso(days[d])}: no data for {', '.join(gone)}")
            w = {idx[b]: v for b, v in st["w"].items() if b in idx}
            w_old = {idx[b]: v for b, v in st["w_prev"].items() if b in idx}
            net, _ = fx.period(mx, dp, d, w, w_old, model)
            st["equity"] *= 1 + net / 100
        L, S, scale, valid = decide(d)
        left = len(dec_bases) - len(valid)
        if left:
            add_note(st, f"{bt_iso(days[d])}: {left} coin(s) without data left out")
        out = sorted(b for b in st["w"] if b not in dec_bases)
        if out:
            add_note(st, f"{bt_iso(days[d])}: {', '.join(out)} left the top 50 — sold")
        new = {dec_bases[x]: v for x, v in fx.weights(L, S, scale).items() if v}
        st["events"] = (st["events"] + [{"t": int(days[d + 1]), "bought": sorted(set(new) - set(st["w"])),
                                         "sold": sorted(set(st["w"]) - set(new)), "net_prev": net}])[-KEEP:]
        st["w_prev"], st["w"], st["d_t"], st["last_day"], st["scale"] = st["w"], new, int(days[d]), int(days[d]), scale


def port_due(ps, now, step=7):
    """A weekly rebalance day has closed since the last one processed (avoids fetching 50 coins every update)."""
    last_closed = (int(now * 1000) - MARGIN_MS) // DAY * DAY - DAY  # open time of the latest closed daily candle
    k = (last_closed - fx.ANCHOR) // (step * DAY)
    return fx.ANCHOR + k * step * DAY > max(ps["last_day"], ps["started"] * 1000 - DAY)


def bt_iso(t):
    return rs.bt.iso(int(t))[:10]


def port_funding(bases, start_ms):
    """Hyperliquid hourly funding per coin (HL lists 1000-unit coins as kPEPE etc.); most coins must answer."""
    out, bad = {}, 0
    for b in bases:
        for name in (b, "k" + b):
            try:
                ft, fr = get_funding(name, start_ms)
            except Exception:
                ft = fr = None
            if ft is not None and len(ft):
                out[b] = (ft, fr)
                break
        else:
            bad += 1
    if bad > len(bases) // 2:
        raise RuntimeError("funding unavailable")
    return out


# ---------------------------------------------------------------- one update (runs off the main cycle)
def add_note(ps, text):
    """Notes are capped at KEEP; n_notes counts every note so the bot can log only the new ones."""
    ps["notes"] = (ps["notes"] + [text])[-KEEP:]
    ps["n_notes"] = ps.get("n_notes", 0) + 1


def migrate_tot_net(s1):
    """Phase 5 states have no tot_net: start from the trades still saved (all of them unless more than KEEP)."""
    if "tot_net" not in s1:
        s1["tot_net"] = float(sum(t["net"] for t in s1["closed"]))
        s1["tot_net_partial"] = s1["n"] > len(s1["closed"])
    if "wk_net" not in s1:  # weekly S1 sums for the blend, seeded from the saved trades (mid-week upgrade)
        s1["wk_net"] = {}
        for t in s1["closed"]:
            k = str(s1_week(t["exit_t"]))
            s1["wk_net"][k] = s1["wk_net"].get(k, 0.0) + t["net"] / 20


def s1_week(exit_t):
    return fx.ANCHOR + DAY + ((exit_t - fx.ANCHOR - DAY) // (7 * DAY)) * 7 * DAY  # edge_v2.weekly's week


# ---------------------------------------------------------------- S12: near-miss blend (edge_v2.py's own functions)
S12_LAG = 7 * DAY + 6 * 3_600_000  # a week is final once every part's period that started in it has closed


def s12_state(now):
    return {"started": int(now), "hist": {k: {} for k in ("S1", "S5", "S6", "S8")}, "equity": 1.0, "w": {},
            "rows": [], "notes": []}


def _periods(events):
    """Part periods as edge_v2 sees them: each rebalance's net belongs to the period that started one event earlier."""
    return [{"t": a["t"], "net": b["net_prev"]} for a, b in zip(events, events[1:]) if b.get("net_prev") is not None]


def s12_streams(st):
    import edge_v2 as ev
    s1 = ev.weekly([{"exit_t": t["exit_t"], "net": t["net"]} for t in st["s1"]["closed"]], "trades")
    s1.update({int(k): v for k, v in st["s1"].get("wk_net", {}).items()})  # complete even after the 60-trade cap
    return {"S1": s1,
            "S5": ev.weekly(_periods(st["s5"]["events"]), "p"), "S6": ev.weekly(_periods(st["s6"]["events"]), "p"),
            "S8": ev.weekly(_periods(st["s8"]["events"]), "p")}


def s12_update(st, now):
    """Store each part's weekly return once the week is final, then let edge_v2.blend make the weights and result."""
    import edge_v2 as ev
    ps = st["s12"]
    start, end = ps["started"] * 1000, int(now * 1000) - S12_LAG
    weeks = [x["t"] for x in ev.blend({}, start, end)]
    cur = s12_streams(st)
    for k, h in ps["hist"].items():
        for wk in weeks:
            if str(wk) not in h:
                if wk not in cur[k]:  # edge_v2 counts a part with no result that week as 0
                    add_note(ps, f"week {bt_iso(wk)}: no {k} result, counted as 0 (as in edge_v2)")
                h[str(wk)] = float(cur[k].get(wk, 0.0))
    rows = ev.blend({k: {int(w): r for w, r in h.items()} for k, h in ps["hist"].items()}, start, end)
    old = len(ps["rows"])
    ps["rows"] = [{"t": r["t"], "net": round(r["net"], 6), "w": {k: round(v, 4) for k, v in r["w"].items()},
                   "at": ps["rows"][i]["at"] if i < old else int(now)} for i, r in enumerate(rows)]
    ps["equity"] = float(np.prod([1 + r["net"] / 100 for r in rows])) if rows else 1.0
    ps["w"] = ps["rows"][-1]["w"] if rows else {}


def update(st, now, over_s1=None, over_s5=None, coins=None, top=None, s6=True, s8=True, s12=True):
    """Returns the new paper-watch state; raises if any S1/S5 fetch fails (the caller keeps the old state).
    S6/S8 problems only skip S6/S8 for this update (their old state is kept, with a note)."""
    st = copy.deepcopy(st) if st else new_state(now)
    for fam in ("s6", "s8"):
        st.setdefault(fam, port_state(now))
    p1, p5 = params(over_s1, over_s5)
    coins = coins or rs.MAJORS
    start_ms = st["started"] * 1000
    bars4 = {b: get_klines(b, "4h", 1000, now)[0] for b in coins}
    daily, nxt = {}, {}
    for b in coins:
        daily[b], nxt[b] = get_klines(b, "1d", 120, now)
    s1_update(st["s1"], p1, bars4, get_funding, start_ms, now)
    s5_update(st["s5"], p5, daily, nxt, start_ms, now)
    due = [f for f, on in (("s8", s8), ("s6", s6)) if on and port_due(st[f], now)]
    if due:
        # the bot's list + coins still held (a coin leaving the top 50 is still priced and sold), BTC always
        held = {b for f in due for b in list(st[f]["w"]) + list(st[f]["w_prev"])}
        names = list(dict.fromkeys(["BTC"] + list(top or []) + sorted(held)))
        d50, n50, failed = {}, {}, []
        for b in names:
            try:
                d50[b], n50[b] = get_klines(b, "1d", 400, now)
            except Exception:
                failed.append(b)
        for fam in due:
            old = copy.deepcopy(st[fam])
            try:
                if "BTC" not in d50:
                    raise RuntimeError("no BTC candles")
                fund = {}
                if fam == "s6":  # funding from 14 + 2 days before the earliest pending rebalance (catch-up safe)
                    first = (st[fam]["d_t"] + 7 * DAY) if st[fam]["d_t"] else st[fam]["started"] * 1000 - DAY
                    back = first - (DEFAULTS["s6"]["look"] + 2) * DAY
                    if back < now * 1000 - 200 * DAY:
                        back = now * 1000 - 200 * DAY
                        add_note(st[fam], "funding history limited to 200 days (long outage); old catch-up signals may be short")
                    fund = port_funding(list(d50), back)
                port_update(st[fam], fam, d50, n50, fund, now, (set(top) | {"BTC"}) if top else None)  # BTC: regime
                if failed:
                    add_note(st[fam], f"no Binance candles for {', '.join(failed[:5])}")
            except Exception as e:
                st[fam] = old
                add_note(st[fam], f"update skipped ({type(e).__name__}); positions kept")
    migrate_tot_net(st["s1"])
    if s12:
        st.setdefault("s12", s12_state(now))
        old = copy.deepcopy(st["s12"])
        try:
            s12_update(st, now)
        except Exception as e:
            st["s12"] = old
            add_note(st["s12"], f"update skipped ({type(e).__name__}); results kept")
    st["last_ok"] = int(now)
    return st


# ---------------------------------------------------------------- daily summary block
def summary_lines(st, now, coin_name=str):
    if not st:
        return []
    since_ms = (now - 86400) * 1000
    s1, s5 = st["s1"], st["s5"]
    day = int((now - st["started"]) // 86400) + 1
    if "s12" in st and not st.get("announced5"):
        out = ["🔭 Now watching 5 near-miss strategies (added the blend)."]
    else:
        out = [] if st.get("announced4") or "s12" in st else ["🔭 Now watching 4 near-miss strategies."]
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
    for fam, (_, name) in PORT.items():
        ps = st.get(fam)
        if not ps:
            continue
        lo, sh = sorted((b for b, v in ps["w"].items() if v > 0), key=lambda b: -ps["w"][b]), \
            [b for b, v in ps["w"].items() if v < 0]
        top_names = lambda xs: ", ".join(coin_name(b) for b in sorted(xs)[:3]) + (" …" if len(xs) > 3 else "")
        if not ps["events"]:
            hold = "nothing yet (first rebalance after the next weekly close)"
        elif not ps["w"]:
            hold = "cash (0% exposure)" if fam == "s8" else "cash"
        elif sh:
            hold = f"long {len(lo)} ({top_names(lo)}) · short {len(sh)} ({top_names(sh)})"
        else:
            hold = f"{len(lo)} coins at {ps['scale'] * 100:.0f}% exposure ({top_names(lo)})"
        ev = [e for e in ps["events"] if e["t"] > since_ms]
        chg = f"rebalanced (bought {len(ev[-1]['bought'])}, sold {len(ev[-1]['sold'])})" if ev else "no rebalance"
        pday = int((now - ps["started"]) // 86400) + 1
        out.append(f"  • {name}: {hold} · 24 h: {chg} · since start: {(ps['equity'] - 1) * 100:+.2f}% after fees"
                   f" · day {pday}")
    b = st.get("s12")
    if b:
        new = [r for r in b["rows"] if r["at"] > now - 86400]
        chg = f"{(np.prod([1 + r['net'] / 100 for r in new]) - 1) * 100:+.2f}% (week added)" if new else "no new week"
        wts = ", ".join(f"{k} {v * 100:.0f}%" for k, v in sorted(b["w"].items())) or "equal until 4 weeks of history"
        thru = f" (weeks up to {bt_iso(b['rows'][-1]['t'])[:10]})" if b["rows"] else " (first result about 2 weeks after start)"
        out.append(f"  • Blend of the 4 above (weekly, calmer parts get more): weights {wts} · 24 h: {chg} · "
                   f"since start: {(b['equity'] - 1) * 100:+.2f}% after fees{thru} · day "
                   f"{int((now - b['started']) // 86400) + 1}")
    if st["last_ok"] and now - st["last_ok"] > 8 * 3600:
        out.append(f"  ⚠️ Watch data not updated for {int((now - st['last_ok']) // 3600)} h (positions kept)")
    return out
