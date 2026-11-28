"""Crash watch: a plain heads-up when a top coin falls fast (closed 5m candles). Pure functions; the bot sends."""
import numpy as np

FIVE_MIN = 300_000
WINDOW = 12  # 12 closed 5m candles = the last 60 minutes
FRESH_MS = 7 * 60_000  # the newest closed candle must have closed within 7 minutes (no old moves after a restart)
COOLDOWN = 6 * 3600
MAX_PER_HOUR = 3
KEEP_EVENTS = 300
LATER = (("1h", 3600), ("4h", 4 * 3600), ("12h", 12 * 3600), ("24h", 24 * 3600))
FILL_SLACK = 1800  # a "price later" is only filled within 30 min of its time, otherwise left empty
BIG = ("BTC", "ETH")
LAST_LINE = "Heads-up only — not a trade signal."


def new_state():
    return {"cool": {}, "market_t": 0, "sent": [], "events": []}


def fall_60m(rows, now_ms):
    """(% change over the last 60 min, last close, close time) from closed 5m rows [ts, o, h, l, c, ...], or None
    when the data is stale, broken or has a gap."""
    if rows is None or len(rows) < WINDOW:
        return None
    a = np.asarray(rows, dtype=float)[-WINDOW:, :5]
    if not np.isfinite(a).all() or (a[:, 1:5] <= 0).any() or (a[:, 2] < a[:, 3]).any():
        return None
    if not np.all(np.diff(a[:, 0]) == FIVE_MIN):
        return None
    close_t = a[-1, 0] + FIVE_MIN
    if close_t > now_ms or close_t < now_ms - FRESH_MS:
        return None
    return float((a[-1, 4] / a[0, 1] - 1) * 100), float(a[-1, 4]), int(close_t)


def thr_for(base, th):
    return th["big"] if base in BIG else th["alt"]


def _cool_ok(st, base, px, thr, now):
    c = st["cool"].get(base)
    return not c or now - c["t"] >= COOLDOWN or px <= c["px"] * (1 - thr / 100)


def scan(st, now, falls, oi, th, top):
    """falls: {base: (pct, price, close_ms)}; oi: {base: 1h OI change %}; top: the bot's top-50 bases.
    Returns (kind, items) to send now, or None. Nothing in the state changes until commit()."""
    st["sent"] = [t for t in st["sent"] if now - t <= 86400 + 3600]
    if sum(1 for t in st["sent"] if now - t < 3600) >= MAX_PER_HOUR:
        return None
    wide = sorted((b for b in top if b in falls and falls[b][0] <= -th["market"]), key=lambda b: falls[b][0])
    hits = [b for b in falls if (b in top or b in BIG) and falls[b][0] <= -thr_for(b, th)
            and _cool_ok(st, b, falls[b][1], thr_for(b, th), now)]
    if len(wide) >= th["count"] and now - st.get("market_t", 0) >= COOLDOWN:
        # one message for the whole crash: also the coins past their own bar but not the market bar (BTC −4%)
        both = sorted(set(wide) | set(hits), key=lambda b: falls[b][0])
        return "market", [_item(b, falls, oi) for b in both]
    if not hits:
        return None
    return "coins", [_item(b, falls, oi) for b in sorted(hits, key=lambda b: falls[b][0])]


def _item(b, falls, oi):
    return {"coin": b, "fall": round(falls[b][0], 2), "px": falls[b][1], "close_t": falls[b][2], "oi": oi.get(b)}


def commit(st, kind, items, now, th):
    """After a successful send: start cooldowns, count the message, save the events."""
    st["sent"].append(int(now))
    if kind == "market":
        st["market_t"] = int(now)
    for it in items:  # every coin named in a message cools down (a market-wide one included)
        st["cool"][it["coin"]] = {"t": int(now), "px": it["px"]}
        st["events"].append(dict(it, t=int(now), kind=kind, later={}))
    st["events"] = st["events"][-KEEP_EVENTS:]
    st["cool"] = {b: c for b, c in st["cool"].items() if now - c["t"] < COOLDOWN * 4}


def fill_later(st, now, prices):
    """Fill 'price 1 h / 4 h / 12 h / 24 h later' for saved events (research data)."""
    for e in st["events"]:
        for key, sec in LATER:
            if key in e["later"] or now < e["t"] + sec:
                continue
            px = prices.get(e["coin"])
            if now - (e["t"] + sec) <= FILL_SLACK and px:
                e["later"][key] = px
            elif now - (e["t"] + sec) > FILL_SLACK:
                e["later"][key] = None  # the bot was not running at that time


def _oi_text(x, th):
    if x is None:
        return "traders' open bets: no data"
    s = f"traders' open bets (open interest) {x:+.1f}% in 1 h"
    return s + " — many traders forced out" if x <= -th["oi"] else s


def format_message(kind, items, th, price_fmt=str, name=str, n_top=50):
    if kind == "market":
        n = sum(1 for it in items if it["fall"] <= -th["market"])
        lines = [f"🚨 <b>MARKET-WIDE CRASH WATCH</b> — {n} of the top {n_top} coins each fell "
                 f"{th['market']:g}% or more in the last 60 minutes",
                 "Biggest falls:"]
    else:
        lines = ["🚨 <b>CRASH WATCH</b> — a sudden fall in the last 60 minutes"]
    shown = items[:8]  # keeps the message short (and its last line inside Telegram's limit)
    for it in shown:
        lines.append(f"• {name(it['coin'])}: {it['fall']:+.1f}% in 60 min · now {price_fmt(it['px'])} · "
                     f"{_oi_text(it['oi'], th)}")
    if len(items) > len(shown):
        lines.append(f"…and {len(items) - len(shown)} more")
    lines.append(LAST_LINE)
    return "\n".join(lines)


def summary_line(st, now):
    n = sum(1 for t in (st or {}).get("sent", []) if now - t <= 86400)
    return f"🚨 Crash watch: {n} heads-up{'s' if n != 1 else ''} in the last 24 h" if n else \
        "🚨 Crash watch: none in the last 24 h"
