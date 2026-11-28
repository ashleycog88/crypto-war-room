"""Phase 6: S12 blend paper-watch, Sunday weekly recap, crash watch (JRN-170..183)."""
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pytest

import crash_watch as cw
import edge_v2 as ev
import paper_watch as pw
import recap
import war_room_bot as wr
from fakes import FakeSpot, TGRecorder, make_bot

DAY, H = 86_400_000, 3_600_000
TH = {"big": 3.0, "alt": 10.0, "market": 5.0, "count": 15, "oi": 5.0}


# ---------------------------------------------------------------- S12 blend (JRN-170, JRN-171)
def synth_records(t0_ms, weeks=26, seed=7):
    rng = np.random.default_rng(seed)
    w0 = ev.fx.ANCHOR + DAY + ((t0_ms - ev.fx.ANCHOR - DAY) // (7 * DAY) + 1) * 7 * DAY
    s1 = [{"exit_t": int(t0_ms + rng.integers(0, weeks * 7 * DAY)), "net": float(rng.normal(0.5, 4))} for _ in range(80)]
    s1.sort(key=lambda x: x["exit_t"])
    s5 = [{"t": int(w0 + d * DAY), "net_prev": None if d == 0 else float(rng.normal(0.1, 2))} for d in range(weeks * 7)]
    s6 = [{"t": int(w0 + k * 7 * DAY), "net_prev": None if k == 0 else float(rng.normal(0.2, 1))} for k in range(weeks)]
    s8 = [{"t": int(w0 + k * 7 * DAY), "net_prev": None if k == 0 else float(rng.normal(0.3, 5))} for k in range(weeks)]
    return s1, s5, s6, s8


def visible(now_ms, s1, s5, s6, s8):
    """What the live watch has at time now: closed trades / rebalances so far, capped like paper_watch (KEEP)."""
    k = pw.KEEP
    return ([t for t in s1 if t["exit_t"] + 4 * H <= now_ms][-k:], [e for e in s5 if e["t"] <= now_ms][-k:],
            [e for e in s6 if e["t"] <= now_ms][-k:], [e for e in s8 if e["t"] <= now_ms][-k:])


def test_s12_live_blend_matches_edge_v2_on_the_same_data():
    t0 = 1_740_000_000  # watch start (unix s)
    full = synth_records(t0 * 1000)
    st = pw.new_state(t0)
    st["s12"] = pw.s12_state(t0)
    for day in range(1, 26 * 7 + 20):
        now = t0 + day * 86400
        s1, s5, s6, s8 = visible(now * 1000, *full)
        st["s1"]["closed"], st["s5"]["events"], st["s6"]["events"], st["s8"]["events"] = s1, s5, s6, s8
        pw.s12_update(st, now)
    s1, s5, s6, s8 = full
    streams = {"S1": ev.weekly(s1, "trades"), "S5": ev.weekly(pw._periods(s5), "p"),
               "S6": ev.weekly(pw._periods(s6), "p"), "S8": ev.weekly(pw._periods(s8), "p")}
    ref = ev.blend(streams, t0 * 1000, int(now * 1000) - pw.S12_LAG)
    got = st["s12"]["rows"]
    assert len(got) == len(ref) >= 20
    assert np.allclose([r["net"] for r in got], [r["net"] for r in ref], atol=1e-6)
    assert all(abs(g["w"].get(k, 0) - r["w"].get(k, 0)) < 1e-4 for g, r in zip(got, ref) for k in r["w"])
    assert abs(st["s12"]["equity"] - np.prod([1 + r["net"] / 100 for r in ref])) < 1e-6
    assert any(len(set(round(v, 2) for v in r["w"].values())) > 1 for r in ref)  # weights really move


def test_s12_parity_survives_busy_s1_weeks_beyond_the_60_trade_cap():
    t0 = 1_740_000_000
    _, s5, s6, s8 = synth_records(t0 * 1000, seed=5)
    rng = np.random.default_rng(1)
    s1 = sorted([{"exit_t": int(t0 * 1000 + rng.integers(0, 26 * 7 * DAY)), "net": float(rng.normal(0.5, 4)),
                  "coin": "X", "entry_t": 0, "entry": 1, "exit": 1, "why": "channel", "side": "LONG", "risk": 1.0}
                 for _ in range(26 * 30)], key=lambda x: x["exit_t"])  # 30 closes a week
    st = pw.new_state(t0)
    st["s12"] = pw.s12_state(t0)
    done = 0
    for day in range(1, 26 * 7 + 20):
        now = t0 + day * 86400
        while done < len(s1) and s1[done]["exit_t"] + 4 * H <= now * 1000:
            net = s1[done]["net"]
            pw.rs.net, real = (lambda t, m, stress=False: net), pw.rs.net
            try:
                pw._record(st["s1"], dict(s1[done]), now)
            finally:
                pw.rs.net = real
            done += 1
        _, v5, v6, v8 = visible(now * 1000, s1, s5, s6, s8)
        st["s5"]["events"], st["s6"]["events"], st["s8"]["events"] = v5, v6, v8
        pw.s12_update(st, now)
    assert len(st["s1"]["closed"]) == pw.KEEP
    ref = ev.blend({"S1": ev.weekly(s1, "trades"), "S5": ev.weekly(pw._periods(s5), "p"),
                    "S6": ev.weekly(pw._periods(s6), "p"), "S8": ev.weekly(pw._periods(s8), "p")},
                   t0 * 1000, int(now * 1000) - pw.S12_LAG)
    assert np.allclose([r["net"] for r in st["s12"]["rows"]], [r["net"] for r in ref], atol=1e-6)


def test_s12_missing_part_counted_as_zero_with_note_and_state_is_json():
    t0 = 1_740_000_000
    st = pw.new_state(t0)
    st["s12"] = pw.s12_state(t0)
    pw.s12_update(st, t0 + 40 * 86400)
    assert st["s12"]["rows"] and all(r["net"] == 0 for r in st["s12"]["rows"])
    assert any("counted as 0" in n for n in st["s12"]["notes"]) and st["s12"]["n_notes"] >= 1
    json.dumps(st)


def test_s12_summary_line_and_announcement_once(cfg):
    now = 1_760_000_000
    st = pw.new_state(now - 30 * 86400)
    st["announced"] = st["announced4"] = True
    st["s12"] = pw.s12_state(now - 20 * 86400)
    st["s12"]["rows"] = [{"t": (now - 9 * 86400) * 1000, "net": 1.5, "w": {"S1": 0.4, "S5": 0.6}, "at": now - 3600}]
    st["s12"]["equity"], st["s12"]["w"] = 1.015, {"S1": 0.4, "S5": 0.6}
    lines = pw.summary_lines(st, now)
    assert lines[0] == "🔭 Now watching 5 near-miss strategies (added the blend)."
    blend = [x for x in lines if "Blend of the 4" in x]
    assert len(blend) == 1 and "S1 40%, S5 60%" in blend[0] and "+1.50% (week added)" in blend[0]
    assert "since start: +1.50% after fees" in blend[0] and "day 21" in blend[0]
    st["announced5"] = True
    assert "Now watching" not in " ".join(pw.summary_lines(st, now))
    del st["s12"]
    assert not any("Blend" in x for x in pw.summary_lines(st, now))


def test_watch_s12_switch_and_new_settings(cfg):
    wr.apply_settings(cfg, json.dumps({"WATCH_S12": False, "EARN_APR": 3.5, "CRASH_WATCH": False, "CRASH_BIG_PCT": 2,
                                       "CRASH_ALT_PCT": 12, "CRASH_MARKET_PCT": 4, "CRASH_MARKET_COUNT": 20,
                                       "CRASH_OI_PCT": 7, "CRASH_MARKET_COUNT_X": 1}))
    assert (cfg.watch_s12, cfg.earn_apr, cfg.crash_watch) == (False, 3.5, False)
    assert (cfg.crash_big, cfg.crash_alt, cfg.crash_market, cfg.crash_count, cfg.crash_oi) == (2, 12, 4, 20, 7)
    wr.apply_settings(cfg, json.dumps({"CRASH_MARKET_COUNT": 2.5, "EARN_APR": -1}))  # bad values ignored
    assert cfg.crash_count == 20 and cfg.earn_apr == 3.5


def test_update_skips_s12_when_switched_off(monkeypatch):
    now = 1_760_000_000
    rows = np.array([[(now * 1000 // DAY - 130 + i) * DAY, 100, 101, 99, 100, 1] for i in range(130)], float)
    monkeypatch.setattr(pw, "get_klines", lambda b, i, n, t: (rows[-n:], float(rows[-1, 4])))
    monkeypatch.setattr(pw, "get_funding", lambda *a: (np.array([], np.int64), np.array([])))
    st = pw.update(None, now, coins=["BTC"], s6=False, s8=False, s12=False)
    assert "s12" not in st
    st = pw.update(st, now + 4 * 3600, coins=["BTC"], s6=False, s8=False, s12=True)
    assert st["s12"]["started"] == now + 4 * 3600


# ---------------------------------------------------------------- weekly recap (JRN-172..174)
def ist(y, m, d, hh, mm, ss=0, tz="Asia/Kolkata"):
    return datetime(y, m, d, hh, mm, ss, tzinfo=ZoneInfo(tz)).timestamp()


def test_recap_due_sunday_once_late_and_never_more_than_24h_late():
    T, Z = "12:01", "Asia/Kolkata"
    assert recap.due(None, ist(2026, 11, 29, 12, 0, 59), T, Z) is None  # Sunday 12:00 IST: last week's slot is > 24 h old
    assert recap.due(None, ist(2026, 11, 29, 12, 1, 30), T, Z) == ("2026-11-29", False)
    assert recap.due("2026-11-29", ist(2026, 11, 29, 12, 5), T, Z) is None  # once per week
    assert recap.due(None, ist(2026, 11, 30, 12, 0), T, Z) == ("2026-11-29", True)  # Monday: sent late
    assert recap.due(None, ist(2026, 11, 30, 12, 2), T, Z) is None  # > 24 h late: skipped
    assert recap.due(None, ist(2026, 11, 28, 23, 59), T, Z) is None  # Saturday night
    # DST: New York's clocks go back on Sunday 2026-11-01; the recap still runs at 12:01 local
    key, at = recap.recap_slot(ist(2026, 11, 1, 13, 0, tz="America/New_York"), T, "America/New_York")
    assert key == "2026-11-01" and datetime.fromtimestamp(at, timezone.utc).hour == 17


def recap_state(now):
    st = pw.new_state(now - 20 * 86400)
    st["s1"]["closed"] = [{"coin": "SOL", "exit_t": (now - 2 * 86400) * 1000, "net": 4.0},
                          {"coin": "ETH", "exit_t": (now - 10 * 86400) * 1000, "net": -2.0}]
    st["s1"]["tot_net"] = 2.0
    st["s5"]["equity"] = 1.03
    st["s5"]["events"] = [{"t": (now - 9 * 86400) * 1000, "net_prev": None}, {"t": (now - 8 * 86400) * 1000, "net_prev": 1.0},
                          {"t": (now - 3 * 86400) * 1000, "net_prev": 2.0}, {"t": (now - 2 * 86400) * 1000, "net_prev": -1.0}]
    st["s6"]["started"], st["s6"]["equity"] = now - 10 * 86400, 0.99
    st["s6"]["events"] = [{"t": (now - 8 * 86400) * 1000, "net_prev": None}, {"t": (now - 1 * 86400) * 1000, "net_prev": -1.0}]
    st["s8"]["started"] = now - 10 * 86400
    st["s12"] = pw.s12_state(now - 15 * 86400)
    st["s12"]["rows"] = [{"t": 0, "net": 0.5, "w": {}, "at": now - 86400}]
    st["s12"]["equity"] = 1.005
    return st


def test_recap_numbers_recompute_from_state_and_benchmarks():
    now = ist(2026, 11, 29, 12, 2)
    st = recap_state(now)
    px = {now - 7 * 86400: 100.0, now: 95.0, st["started"]: 80.0, now - 10 * 86400: 90.0, now - 15 * 86400: 120.0}
    closed = [{"grade": "A", "net": 1.2, "exit_time": now - 3600}, {"grade": "A", "net": -0.4, "exit_time": now - 7200},
              {"grade": "B", "net": 5.0, "exit_time": now - 8 * 86400}]
    text, d = recap.build(st, closed, now, lambda t: px[t], 5.0, day_label="week to Sunday 2026-11-29")
    r = {x["key"]: x for x in d["strategies"]}
    assert r["s1"]["d7"] == pytest.approx(4.0 / 20) and r["s1"]["total"] == pytest.approx(2.0 / 20)
    assert r["s5"]["d7"] == pytest.approx((1.02 * 0.99 - 1) * 100) and r["s5"]["total"] == pytest.approx(3.0)
    assert r["s6"]["d7"] == pytest.approx(-1.0) and r["s8"]["d7"] == 0 and r["s12"]["d7"] == pytest.approx(0.5)
    assert d["btc7"] == pytest.approx(-5.0 - 0.10) and d["earn7"] == pytest.approx(5.0 * 7 / 365)
    assert r["s5"]["hold"] == pytest.approx((95 / 80 - 1) * 100 - 0.1) and r["s5"]["mark"] == "beat one"
    assert r["s12"]["mark"] == "beat both" and r["s6"]["mark"] == "beat neither"  # BTC −20.9% since s12 started
    assert d["scoreboard"]["A"] == {"n": 2, "win": 50.0, "net": pytest.approx(0.8)} and d["scoreboard"]["B"]["n"] == 0
    lines = text.splitlines()
    assert lines[0].startswith("📊 <b>WEEKLY RECAP</b> — week to Sunday 2026-11-29") and "(sent late)" not in lines[0]
    assert lines[-1] == "Paper only — nothing here is ready for real money yet."
    order = [x for x in lines if x.startswith("  1. ")]
    assert order[0].endswith("+0.98%")  # s5 had the best 7 days
    assert "₿ Just holding Bitcoin: -5.10%" in text and "🏦 USDT in Binance Flexible Earn (5% a year): +0.10%" in text
    late, _ = recap.build(st, [], now, lambda t: px[t], 5.0, late=True)
    assert "(sent late)" in late.splitlines()[0]
    for bad in ("buy now", "sell now", "you should"):
        assert bad not in text.lower()


def test_bot_recap_sent_once_through_outbox_when_telegram_fails(cfg, monkeypatch):
    rec = TGRecorder(fail=True)
    monkeypatch.setattr(wr, "_http_post", rec)
    cfg.paper_watch = True
    now = ist(2026, 11, 29, 12, 3)
    h1 = np.array([[(now - 999 * 3600 + i * 3600) * 1000 // 3_600_000 * 3_600_000, 100, 101, 99, 100, 1] for i in range(1000)], float)
    monkeypatch.setattr(pw, "get_klines", lambda b, i, n, t: (h1, None))
    bot = make_bot(cfg, FakeSpot(now=now))
    bot.state["paper_watch"] = recap_state(now)
    bot.maybe_weekly_recap(now)
    box = [m for m in bot.state["outbox"] if "WEEKLY RECAP" in m["text"]]
    assert len(box) == 1 and bot.state["last_recap"] == "2026-11-29"
    bot.maybe_weekly_recap(now + 600)
    assert len([m for m in bot.state["outbox"] if "WEEKLY RECAP" in m["text"]]) == 1  # at most once a week
    rec.fail = False
    bot.flush_outbox()
    assert len(rec.texts("WEEKLY RECAP")) == 1 and not bot.state["outbox"]


def test_bot_recap_late_after_downtime(cfg, tg, monkeypatch):
    cfg.paper_watch = True
    now = ist(2026, 11, 30, 9, 0)
    h1 = np.array([[(now - 999 * 3600 + i * 3600) * 1000 // 3_600_000 * 3_600_000, 100, 101, 99, 100, 1] for i in range(1000)], float)
    monkeypatch.setattr(pw, "get_klines", lambda b, i, n, t: (h1, None))
    bot = make_bot(cfg, FakeSpot(now=now))
    bot.state["paper_watch"] = recap_state(now)
    bot.maybe_weekly_recap(now)
    assert "(sent late)" in tg.texts("WEEKLY RECAP")[0].splitlines()[0]


# ---------------------------------------------------------------- crash watch (JRN-175..183)
NOW = 1_764_000_000  # unix s
NOW_MS = NOW * 1000


def rows5(fall_pct, now_ms=NOW_MS, n=20, last_closed_ago_ms=30_000, gap=False):
    """Closed 5m candles: flat at 100, then the last 12 fall linearly by fall_pct."""
    last_open = (now_ms - last_closed_ago_ms) // cw.FIVE_MIN * cw.FIVE_MIN - cw.FIVE_MIN
    t = last_open - (n - 1 - np.arange(n)) * cw.FIVE_MIN
    if gap:
        t[-5:] += cw.FIVE_MIN
        t = t - cw.FIVE_MIN
    c = np.full(n, 100.0)
    c[-12:] = 100 * (1 - fall_pct / 100 * np.arange(1, 13) / 12)
    o = np.concatenate([[100.0], c[:-1]])
    return np.column_stack([t, o, np.maximum(o, c) * 1.0005, np.minimum(o, c) * 0.9995, c])


def falls_for(spec, now_ms=NOW_MS):
    return {b: cw.fall_60m(rows5(p, now_ms), now_ms) for b, p in spec.items()}


TOP = ["BTC", "ETH"] + [f"C{i}" for i in range(48)]


def test_crash_triggers_btc_eth_3pct_and_alts_10pct():
    st = cw.new_state()
    f = falls_for({"BTC": 3.2, "ETH": 2.5, "C1": 10.5, "C2": 9.5})
    kind, items = cw.scan(st, NOW, f, {}, TH, TOP)
    assert kind == "coins" and [i["coin"] for i in items] == ["C1", "BTC"]  # one grouped message, biggest first
    assert items[1]["fall"] == pytest.approx(-3.2, abs=0.01)
    msg = cw.format_message(kind, items, TH)
    assert msg.splitlines()[0].startswith("🚨 <b>CRASH WATCH</b>") and msg.splitlines()[-1] == cw.LAST_LINE
    assert "C1: -10.5% in 60 min · now 89.5" in msg
    assert cw.scan(cw.new_state(), NOW, falls_for({"BTC": 2.9, "C1": 9.9}), {}, TH, TOP) is None


def test_market_wide_replaces_per_coin_and_has_own_cooldown():
    spec = {b: 6.0 for b in TOP[2:18]}
    spec.update({"BTC": 5.5, "C30": 12.0})
    f = falls_for(spec)
    st = cw.new_state()
    kind, items = cw.scan(st, NOW, f, {"C30": -8.0}, TH, TOP)
    assert kind == "market" and len(items) == 18 and items[0]["coin"] == "C30"
    msg = cw.format_message(kind, items, TH)
    assert msg.splitlines()[0].startswith("🚨 <b>MARKET-WIDE CRASH WATCH</b> — 18 of the top 50 coins each fell 5%")
    assert "…and 10 more" in msg and "many traders forced out" in msg and msg.endswith(cw.LAST_LINE)
    cw.commit(st, kind, items, NOW, TH)
    assert set(st["cool"]) == {i["coin"] for i in items}  # every coin named in the message cools down
    again = cw.scan(st, NOW + 600, falls_for(spec, NOW_MS + 600_000), {}, TH, TOP)
    assert again is None  # market-wide 6 h cooldown; BTC and C30 are cooling down too


def test_market_wide_includes_btc_past_its_own_bar_and_no_follow_up_from_the_same_crash():
    spec = {b: 6.0 for b in TOP[2:18]}
    spec["BTC"] = 4.0  # past BTC's 3% bar, not the 5% market bar
    st = cw.new_state()
    kind, items = cw.scan(st, NOW, falls_for(spec), {}, TH, TOP)
    assert kind == "market" and "BTC" in [i["coin"] for i in items]
    assert cw.format_message(kind, items, TH).startswith("🚨 <b>MARKET-WIDE CRASH WATCH</b> — 16 of the top 50")
    cw.commit(st, kind, items, NOW, TH)
    assert cw.scan(st, NOW + 60, falls_for(spec, NOW_MS + 60_000), {}, TH, TOP) is None
    deeper = {b: (-11.0, 94.0 * 0.95, NOW_MS + 300_000) for b in TOP[2:18]}  # −11%, only 5% below the alerted price
    assert cw.scan(st, NOW + 300, deeper, {}, TH, TOP) is None


def test_long_coin_messages_keep_the_last_line():
    items = [{"coin": f"C{i}", "fall": -12.0, "px": 1.0, "close_t": 0, "oi": None} for i in range(40)]
    msg = cw.format_message("coins", items, TH)
    assert len(msg) < 1500 and "…and 32 more" in msg and msg.endswith(cw.LAST_LINE)


def test_s1_since_start_migrates_phase5_state():
    st = pw.new_state(1_760_000_000)
    st["s1"]["closed"] = [{"exit_t": 1, "net": 4.0}, {"exit_t": 2, "net": 6.0}]
    st["s1"]["n"] = 2
    st["s1"]["tot_net"] = float(sum(t["net"] for t in st["s1"]["closed"]))
    assert recap.strategy_results(st, 1_760_100_000, ("s1",))[0]["total"] == pytest.approx(0.5)


def test_oi_label_only_when_down_5pct():
    f = falls_for({"BTC": 4.0, "ETH": 4.0, "C1": 11.0})
    kind, items = cw.scan(cw.new_state(), NOW, f, {"BTC": -6.2, "ETH": -2.0}, TH, TOP)
    msg = cw.format_message(kind, items, TH)
    lines = {ln.split(":")[0]: ln for ln in msg.splitlines() if ln.startswith("• ")}
    assert "many traders forced out" in lines["• BTC"] and "-6.2% in 1 h" in lines["• BTC"]
    assert "-2.0% in 1 h" in lines["• ETH"] and "forced out" not in lines["• ETH"]
    assert "open bets: no data" in lines["• C1"]


def test_cooldown_6h_refire_after_further_fall_and_cap_3_per_hour():
    st = cw.new_state()
    k, it = cw.scan(st, NOW, falls_for({"C1": 11.0}), {}, TH, TOP)
    cw.commit(st, k, it, NOW, TH)
    px = it[0]["px"]
    f2 = {"C1": (-12.0, px * 0.95, NOW_MS + 1_200_000)}
    assert cw.scan(st, NOW + 1200, f2, {}, TH, TOP) is None  # 6 h cooldown
    f3 = {"C1": (-11.0, px * 0.89, NOW_MS + 1_800_000)}
    assert cw.scan(st, NOW + 1800, f3, {}, TH, TOP)[1][0]["coin"] == "C1"  # a further full 10% below the alert price
    f4 = {"C1": (-11.0, px, NOW_MS + 6 * 3600_000)}
    assert cw.scan(st, NOW + 6 * 3600, f4, {}, TH, TOP) is not None  # cooldown over
    st2 = cw.new_state()
    for i in range(3):
        k, it = cw.scan(st2, NOW + i * 60, {f"C{i}": (-12.0, 50.0, NOW_MS + i * 60_000)}, {}, TH, TOP)
        cw.commit(st2, k, it, NOW + i * 60, TH)
    assert cw.scan(st2, NOW + 300, {"C9": (-15.0, 50.0, NOW_MS + 300_000)}, {}, TH, TOP) is None  # cap 3/hour
    assert cw.scan(st2, NOW + 3601, {"C9": (-15.0, 50.0, NOW_MS + 3_601_000)}, {}, TH, TOP) is not None


def test_stale_gapped_or_broken_candles_never_alert():
    assert cw.fall_60m(rows5(20, last_closed_ago_ms=8 * 60_000), NOW_MS) is None  # stale (restart after an old move)
    assert cw.fall_60m(rows5(20, gap=True), NOW_MS) is None
    bad = rows5(20)
    bad[-1, 4] = np.nan
    assert cw.fall_60m(bad, NOW_MS) is None
    assert cw.fall_60m(rows5(20)[-11:], NOW_MS) is None
    old = rows5(20, now_ms=NOW_MS - 2 * 3600_000)  # a crash 2 h ago, seen by the first cycle after a restart
    assert cw.fall_60m(old, NOW_MS) is None
    future = rows5(20, last_closed_ago_ms=-300_000)
    assert cw.fall_60m(future, NOW_MS) is None  # a candle that has not closed yet


def test_events_saved_capped_and_prices_later_filled():
    st = cw.new_state()
    for i in range(310):
        st["sent"] = []
        k, it = cw.scan(st, NOW + i * 7 * 3600, {"C1": (-12.0, 10.0, (NOW + i * 7 * 3600) * 1000)}, {}, TH, TOP)
        cw.commit(st, k, it, NOW + i * 7 * 3600, TH)
    assert len(st["events"]) == cw.KEEP_EVENTS
    st = cw.new_state()
    k, it = cw.scan(st, NOW, {"BTC": (-4.0, 60000.0, NOW_MS)}, {}, TH, TOP)
    cw.commit(st, k, it, NOW, TH)
    cw.fill_later(st, NOW + 3600 + 30, {"BTC": 61000.0})
    cw.fill_later(st, NOW + 4 * 3600 + 3 * 3600, {"BTC": 59000.0})  # bot was down at the 4 h mark
    e = st["events"][0]
    assert e["later"] == {"1h": 61000.0, "4h": None} and e["kind"] == "coins" and e["fall"] == -4.0
    json.dumps(st)
    assert cw.summary_line(st, NOW + 3600) == "🚨 Crash watch: 1 heads-up in the last 24 h"
    assert cw.summary_line(st, NOW + 2 * 86400) == "🚨 Crash watch: none in the last 24 h"


def crash_bot(cfg, monkeypatch, fall=5.0, phase=30):
    spot = FakeSpot(pairs=["BTC/USDT", "ETH/USDT", "SOL/USDT"])
    spot.now = spot.now // 300 * 300 + phase
    spot.override[("BTC/USDT", "5m")] = lambda now: [list(r) + [100.0] for r in rows5(fall, int(now * 1000), n=300,
                                                                                    last_closed_ago_ms=0)] + \
        [[float((int(now * 1000) // cw.FIVE_MIN) * cw.FIVE_MIN), 96.0, 96.0, 96.0, 96.0, 1.0]]
    return spot, make_bot(cfg, spot)


@pytest.mark.parametrize("phase", [5, 45, 85])
def test_bot_sends_crash_in_quiet_mode_within_90s_of_close(cfg, tg, monkeypatch, phase):
    cfg.quiet = True
    spot, bot = crash_bot(cfg, monkeypatch, phase=phase)
    bot.cycle(now=spot.now)
    msgs = tg.texts("CRASH WATCH")
    assert len(msgs) == 1 and "BTC" in msgs[0] and msgs[0].endswith(cw.LAST_LINE)
    close = spot.now // 300 * 300
    assert spot.now - close <= 90  # sent in the first cycle after the candle closed
    assert bot.state["crash"]["events"][0]["coin"] == "BTC"
    bot.cycle(now=spot.now + 60)
    assert len(tg.texts("CRASH WATCH")) == 1  # no double send in the next cycle


def test_bot_failed_send_starts_no_cooldown_and_retries(cfg, monkeypatch):
    rec = TGRecorder(fail=True)
    monkeypatch.setattr(wr, "_http_post", rec)
    spot, bot = crash_bot(cfg, monkeypatch)
    bot.cycle(now=spot.now)
    assert not bot.state["crash"]["cool"] and not bot.state["crash"]["sent"]
    assert not any("CRASH" in m["text"] for m in bot.state.get("outbox", []))  # old news is not queued
    rec.fail = False
    bot.cycle(now=spot.now + 60)
    assert len(rec.texts("CRASH WATCH")) == 1 and "BTC" in bot.state["crash"]["cool"]


def test_bot_crash_watch_off_and_daily_summary_line(cfg, tg, monkeypatch):
    cfg.crash_watch = False
    spot, bot = crash_bot(cfg, monkeypatch)
    bot.cycle(now=spot.now)
    assert not tg.texts("CRASH") and "crash" not in bot.state
    cfg.crash_watch = True
    st = bot.state.setdefault("crash", cw.new_state())
    st["sent"] = [spot.now - 3600, spot.now - 7200]
    text = wr.format_daily_summary(bot.state, spot.now, extra=[cw.summary_line(st, spot.now)])
    assert "🚨 Crash watch: 2 heads-ups in the last 24 h" in text


def test_wk_net_seeded_on_upgrade_mid_week():
    s1 = pw.new_state(1_760_000_000)["s1"]
    w = pw.s1_week(1_760_000_000_000)
    s1["closed"] = [{"exit_t": w + DAY, "net": 2.0}, {"exit_t": w + 2 * DAY, "net": 4.0}]
    s1["n"] = 2
    pw.migrate_tot_net(s1)
    assert s1["wk_net"] == {str(w): pytest.approx(0.3)}


def test_recap_btc_price_nearest_the_slot_and_7_day_window_ends_at_slot():
    now = ist(2026, 11, 29, 12, 3)
    at = recap.recap_slot(now, "12:01", "Asia/Kolkata")[1]
    h0 = int(now * 1000) // H * H - 1000 * H
    h1 = np.array([[h0 + i * H, 100 + i, 101 + i, 99 + i, 100.5 + i, 1] for i in range(1000)], float)
    px = recap.btc_prices(lambda *a: (h1, None), now)
    assert px(at) == h1[-1, 4]  # the last closed hour's close, not an older open
    st = recap_state(now)
    st["s1"]["closed"].append({"exit_t": int(at * 1000) + 600_000, "net": 9.0})  # after the slot (late send)
    r = {x["key"]: x for x in recap.strategy_results(st, at)}
    assert r["s1"]["d7"] == pytest.approx(4.0 / 20)


def test_bot_skips_recap_when_watch_started_after_the_slot(cfg, tg, monkeypatch):
    cfg.paper_watch = True
    now = ist(2026, 11, 29, 18, 0)
    bot = make_bot(cfg, FakeSpot(now=now))
    bot.state["paper_watch"] = recap_state(now)
    bot.state["paper_watch"]["started"] = now - 3600
    bot.maybe_weekly_recap(now)
    assert not tg.texts("WEEKLY RECAP") and bot.state["last_recap"] == "2026-11-29"


def test_crash_defaults_after_the_2026_replay(cfg):
    c = wr.Config()
    assert (c.crash_big, c.crash_alt, c.crash_market, c.crash_count, c.crash_oi) == (4.0, 12.0, 5.0, 15, 5.0)
    th = {"big": c.crash_big, "alt": c.crash_alt, "market": c.crash_market, "count": c.crash_count, "oi": c.crash_oi}
    f = falls_for({"BTC": 3.6, "ETH": 4.3, "C1": 11.0, "C2": 12.6})
    kind, items = cw.scan(cw.new_state(), NOW, f, {}, th, TOP)
    assert kind == "coins" and [i["coin"] for i in items] == ["C2", "ETH"]
