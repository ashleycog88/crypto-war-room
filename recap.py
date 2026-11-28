"""Sunday weekly recap: watched strategies vs just holding Bitcoin and USDT in Flexible Earn. Pure functions."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np

DAY_S = 86400
SPOT_FEE = 0.10  # Bitcoin buy-and-hold pays the spot fee once
LATE_S = 600  # sent more than 10 minutes after the recap time → "(sent late)"
MAX_LATE_S = 86400
LAST_LINE = "Paper only — nothing here is ready for real money yet."
NAMES = {"s1": "4h breakout (buy, majors)", "s5": "Top-3 momentum (majors)", "s6": "Funding factor (top 50)",
         "s8": "Vol-managed momentum (top 50)", "s12": "Blend of the 4"}


def recap_slot(now, hhmm, tz):
    """(key, unix time) of the latest Sunday recap time at or before now, in the summary time zone."""
    z = ZoneInfo(tz)
    d = datetime.fromtimestamp(now, z)
    h, m = map(int, hhmm.split(":"))
    sun = d.date() - timedelta(days=(d.weekday() - 6) % 7)
    at = datetime(sun.year, sun.month, sun.day, h, m, tzinfo=z)
    if at > d:
        at -= timedelta(days=7)
        at = datetime(at.year, at.month, at.day, h, m, tzinfo=z)  # DST-safe local time
    return at.date().isoformat(), at.timestamp()


def due(last_key, now, hhmm, tz):
    """None, or (key, late) when this week's recap should go out now (at most 24 h late)."""
    key, at = recap_slot(now, hhmm, tz)
    if last_key == key or now - at > MAX_LATE_S:
        return None
    return key, now - at > LATE_S


def _pairs(events):
    return [(b["t"], b["net_prev"]) for a, b in zip(events, events[1:]) if b.get("net_prev") is not None]


def _compound(nets):
    return float((np.prod([1 + x / 100 for x in nets]) - 1) * 100) if nets else 0.0


def strategy_results(pw, now, on=("s1", "s5", "s6", "s8", "s12")):
    """[{key, name, started, d7, total}] in % of capital after fees, recomputable from the saved state alone.
    The 7 days end at `now` (the recap time, also on a late send); 'since start' is the saved total when sent."""
    out, since, upto = [], (now - 7 * DAY_S) * 1000, now * 1000
    if not pw:
        return out
    if "s1" in on:
        s1 = pw["s1"]
        tot = s1.get("tot_net", sum(t["net"] for t in s1["closed"]))
        out.append({"key": "s1", "started": pw["started"], "total": tot / 20, "partial": bool(s1.get("tot_net_partial")),
                    "d7": sum(t["net"] for t in s1["closed"] if since < t["exit_t"] <= upto) / 20})
    if "s5" in on:
        out.append({"key": "s5", "started": pw["started"], "total": (pw["s5"]["equity"] - 1) * 100,
                    "d7": _compound([n for t, n in _pairs(pw["s5"]["events"]) if since < t <= upto])})
    for k in ("s6", "s8"):
        if k in on and pw.get(k):
            ps = pw[k]
            out.append({"key": k, "started": ps["started"], "total": (ps["equity"] - 1) * 100,
                        "d7": _compound([n for t, n in _pairs(ps["events"]) if since < t <= upto])})
    if "s12" in on and pw.get("s12"):
        b = pw["s12"]
        out.append({"key": "s12", "started": b["started"], "total": (b["equity"] - 1) * 100,
                    "thru": b["rows"][-1]["t"] / 1000 + 7 * DAY_S if b["rows"] else None,
                    "d7": _compound([r["net"] for r in b["rows"] if now - 7 * DAY_S < r["at"] <= now])})
    for r in out:
        r["name"] = NAMES[r["key"]]
    return out


def btc_hold(px_at, t0, t1):
    return (px_at(t1) / px_at(t0) - 1) * 100 - SPOT_FEE


def earn(apr, t0, t1):
    return apr * (t1 - t0) / (365 * DAY_S)


def mark(total, hold, earned):
    n = (total > hold) + (total > earned)
    return ("beat neither", "beat one", "beat both")[n]


def scoreboard_week(closed, now):
    out = {}
    for g in ("A", "B"):
        tr = [t for t in closed if isinstance(t, dict) and t.get("grade") == g and "net" in t
              and now - 7 * DAY_S < t.get("exit_time", 0) <= now and not t.get("exclude_from_scoreboard")]
        w = sum(1 for t in tr if t["net"] > 0)
        out[g] = {"n": len(tr), "win": (w / len(tr) * 100) if tr else None, "net": sum(t["net"] for t in tr)}
    return out


def build(pw, closed, now, px_at, apr, late=False, on=("s1", "s5", "s6", "s8", "s12"), day_label=""):
    rs_ = strategy_results(pw, now, on)
    h7, e7 = btc_hold(px_at, now - 7 * DAY_S, now), earn(apr, now - 7 * DAY_S, now)
    lines = [f"📊 <b>WEEKLY RECAP</b>{' (sent late)' if late else ''} — {day_label} (paper only)",
             "Last 7 days, after fees (% of the money in each test):"]
    for i, r in enumerate(sorted(rs_, key=lambda r: -r["d7"]), 1):
        lines.append(f"  {i}. {r['name']}: {r['d7']:+.2f}%")
    lines.append(f"  ₿ Just holding Bitcoin: {h7:+.2f}% · 🏦 USDT in Binance Flexible Earn ({apr:g}% a year): {e7:+.2f}%")
    lines.append("Since each test started, after fees:")
    for i, r in enumerate(sorted(rs_, key=lambda r: -r["total"]), 1):
        hold, er = btc_hold(px_at, r["started"], now), earn(apr, r["started"], now)
        r.update(hold=hold, earn=er, mark=mark(r["total"], hold, er))
        note = " (only the last 60 trades)" if r.get("partial") else ""
        if r["key"] == "s12":
            note = (f" (weeks up to {datetime.fromtimestamp(r['thru'], ZoneInfo('UTC')).date()}; finished weeks only)"
                    if r.get("thru") else " (first finished week comes about 2 weeks after the start)")
        lines.append(f"  {i}. {r['name']} (day {int((now - r['started']) // DAY_S) + 1}): {r['total']:+.2f}%{note} — "
                     f"{r['mark']} (Bitcoin {hold:+.2f}%, Earn {er:+.2f}% over the same days)")
    if not rs_:
        lines.append("  nothing watched yet")
    sb = scoreboard_week(closed, now)
    lines.append("🏆 Main bot paper scoreboard this week: " + " · ".join(
        f"{g}-grade: {v['n']} trade{'s' * (v['n'] != 1)}"
        + (f", {v['win']:.0f}% wins, {v['net']:+.2f}% after fees" if v["n"] else "") for g, v in sb.items()))
    lines.append(LAST_LINE)
    return "\n".join(lines), {"strategies": rs_, "btc7": h7, "earn7": e7, "scoreboard": sb}


def btc_prices(get_klines, now):
    """px_at(t): BTC price at time t — the open of the 1h candle containing t (1d candle when older than ~41 days);
    now → the latest close."""
    h1, _ = get_klines("BTC", "1h", 1000, now)
    d1 = None

    def px_at(t):
        nonlocal d1
        ms = t * 1000
        if t >= now - 60 or (len(h1) and ms >= h1[-1, 0] + 3_600_000):
            return float(h1[-1, 4])  # at or after the last closed hour: its close is the nearest price
        if len(h1) and ms >= h1[0, 0]:
            return float(h1[np.searchsorted(h1[:, 0], ms, side="right") - 1, 1])
        if d1 is None:
            d1 = get_klines("BTC", "1d", 1000, now)[0]
        return float(d1[max(np.searchsorted(d1[:, 0], ms, side="right") - 1, 0), 1])
    return px_at
