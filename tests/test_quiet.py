"""Phase 2b quiet mode (JRN-75..80)."""
import re
from datetime import datetime, timezone

import war_room_bot as wr
from fakes import FakeSpot, install_scores, make_bot

TF = 300_000
DAY = datetime(2026, 11, 28, tzinfo=timezone.utc).timestamp()  # 00:00 UTC
PER_TRADE = ("/100)", "reached Take profit 1", "trade finished")


def per_trade(tg):
    return [t for t in tg.texts() if any(k in t for k in PER_TRADE)]


def quiet(cfg, on=True):
    cfg.quiet = on
    cfg.summary_time = "09:00"  # logic tests use 09:00 IST (= 03:30 UTC, the pre-Phase-2c schedule)
    return cfg


def run_trades(cfg, tg, monkeypatch):
    pairs = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
    spot = FakeSpot(pairs=pairs)
    install_scores(monkeypatch, spot, {p: (85, 0) for p in pairs})
    bot = make_bot(cfg, spot)
    bot.cycle(now=spot.now)
    trades = {t["symbol"]: t for t in bot.state["active_trades"]}
    install_scores(monkeypatch, spot, {})
    e_ms = (int(spot.now) * 1000 // TF + 1) * TF
    tp1, sl = {}, {}
    for s, t in trades.items():
        tp1[s] = [float(e_ms), t["entry"], t["tp1"] * 1.0001, t["entry"], t["entry"], 1]
        sl[s] = [float(e_ms + TF), t["entry"], t["tp1"] * 0.999, t["sl"] * 0.999, t["entry"], 1]
    spot.trade_candles = {"BTC/USDT": [tp1["BTC/USDT"]]}
    spot.now += 600
    bot.cycle(now=spot.now)
    spot.trade_candles = {"BTC/USDT": [tp1["BTC/USDT"], sl["BTC/USDT"]], "ETH/USDT": [sl["ETH/USDT"]]}
    spot.now += 300
    bot.cycle(now=spot.now)
    strip = lambda ts: [{k: v for k, v in t.items() if k not in ("entry_time", "exit_time", "tp1_time", "checked_until",
                                                                  "id", "candle_ts")} for t in ts]
    return bot, strip(bot.state["active_trades"]), strip(bot.state["closed_trades"]), dict(bot.state["totals"])


def test_jrn75_quiet_tracks_identically_but_sends_nothing(cfg, tg, monkeypatch, tmp_path):
    bot_q, act_q, cl_q, tot_q = run_trades(quiet(cfg), tg, monkeypatch)
    assert len(cl_q) == 2 and len(act_q) == 1 and per_trade(tg) == []
    assert bot_q.state["outbox"] == []
    tg.payloads.clear()
    cfg2 = wr.Config(state_file=str(tmp_path / "loud.json"), min_tp1_fee_mult=0)
    bot_l, act_l, cl_l, tot_l = run_trades(quiet(cfg2, False), tg, monkeypatch)
    assert (act_q, cl_q, tot_q) == (act_l, cl_l, tot_l)
    assert len(tg.texts("/100)")) == 3 and len(tg.texts("reached Take profit 1")) == 1
    assert len(tg.texts("trade finished")) == 2


def closed(sym, d, g, net, ago, now):
    return {"symbol": sym, "dir": d, "grade": g, "entry": 1, "sl": 1, "tp1": 1, "tp2": 1, "entry_time": now - ago - 3600,
            "exit_time": now - ago, "net": net, "win": net > 0, "status": "CLOSED"}


def summary_bot(cfg, now):
    spot = FakeSpot(pairs=["BTC/USDT"], now=now)
    bot = make_bot(quiet(cfg), spot)
    st = bot.state
    st["closed_trades"] = [closed("ETH/USDT", "LONG", "A", 1.5, 7200, now), closed("XRP/USDT", "SHORT", "B", -0.9, 3600, now),
                           closed("DOGE/USDT", "LONG", "A", 9.0, 3 * 86400, now)]
    st["totals"] = wr.recompute_totals(st["closed_trades"])
    st["active_trades"] = [{"symbol": "SOL/USDT", "dir": "LONG", "grade": "B", "entry": 100.0, "sl": 1.0, "tp1": 1e6,
                            "tp2": 2e6, "entry_time": now - 600, "tp1_hit": False, "checked_until": 0,
                            "exchange": "BINANCE", "status": "OPEN"}]
    st["ai_log"] = [[now - 7000, "APPROVED"], [now - 4000, "APPROVED"], [now - 600, "APPROVED"],
                    [now - 500, "REJECTED"], [now - 400, "UNCLEAR"], [now - 3 * 86400, "REJECTED"]]
    st["last_daily_summary"] = "2026-11-27"
    st["quiet_notice_sent"] = True
    return bot, spot


def test_jrn76_daily_summary_once(cfg, tg, monkeypatch):
    now = DAY + 3 * 3600 + 25 * 60  # 03:25 UTC — not yet
    bot, spot = summary_bot(cfg, now)
    bot.cycle(now=now)
    assert tg.texts("DAILY SUMMARY") == []
    for t in (DAY + 3 * 3600 + 31 * 60, DAY + 3 * 3600 + 40 * 60, DAY + 20 * 3600):
        spot.now = t
        bot.cycle(now=t)
    s = tg.texts("DAILY SUMMARY")
    assert len(s) == 1, s
    s = s[0]
    assert "Paper trades opened: 3 (A 1 · B 2 · buy 2 · short 1)" in s
    assert "Paper trades closed: 2" in s and "✅ WIN ETH buy +1.50% (A)" in s and "❌ LOSS XRP short -0.90% (B)" in s
    assert "DOGE" not in s and "Best: ETH buy +1.50%" in s and "Worst: XRP short -0.90%" in s
    assert "Scoreboard (24 h): A-grade: 1 win · 0 losses (100%) | B-grade: 0 wins · 1 loss (0%)" in s
    assert "Scoreboard (all time): A-grade: 2 wins" in s and "Open trades now: 1" in s
    assert "AI: approved 3 · rejected 1 · unclear 1" in s
    assert "Paper test only — don't trade these with real money." in s and len(s) < 4096
    assert bot.state["last_daily_summary"] == "2026-11-28"


def test_jrn77_missed_summary_sent_at_next_run_start(cfg, tg, monkeypatch):
    now = DAY + 4 * 3600 + 10 * 60  # 04:10 UTC, no run was active at 03:30
    bot, spot = summary_bot(cfg, now)
    bot.cycle(now=now)
    assert len(tg.texts("DAILY SUMMARY")) == 1
    bot2 = wr.Bot(cfg, spot=bot.spot, deriv=bot.deriv)
    bot2.coins = ["BTC/USDT"]
    bot2.cycle(now=now + 3600)
    assert len(tg.texts("DAILY SUMMARY")) == 1


def test_summary_not_lost_when_telegram_fails(cfg, tg, monkeypatch):
    now = DAY + 4 * 3600
    bot, spot = summary_bot(cfg, now)
    tg.fail = True
    bot.cycle(now=now)
    assert bot.state["last_daily_summary"] == "2026-11-27"
    assert not [m for m in bot.state["outbox"] if "DAILY SUMMARY" in m["text"]]
    tg.fail = False
    bot.cycle(now=now + 60)
    assert len(tg.texts("DAILY SUMMARY")) == 1


def test_first_quiet_run_waits_for_next_summary_time(cfg, tg):
    spot = FakeSpot(pairs=["BTC/USDT"], now=DAY + 12 * 3600)
    bot = make_bot(quiet(cfg), spot)
    bot.cycle(now=spot.now)
    assert tg.texts("DAILY SUMMARY") == [] and bot.state["last_daily_summary"] == "2026-11-28"
    spot.now = DAY + 86400 + 3 * 3600 + 31 * 60
    bot.cycle(now=spot.now)
    assert len(tg.texts("DAILY SUMMARY")) == 1


def test_jrn78_heartbeat_quiet_label_and_bot_down_unchanged(cfg, tg):
    spot = FakeSpot(pairs=["BTC/USDT"])
    bot = make_bot(quiet(cfg), spot)
    bot.cycle(now=spot.now)
    bot.state["last_heartbeat"] = 0
    bot.cycle(now=spot.now + 3700)
    hb = tg.texts("times this hour")
    assert len(hb) == 2 and all("BOT ALIVE</b> (quiet mode)" in h for h in hb)
    assert "Paper trades opened (not sent): 0" in hb[0]
    msg = wr.watchdog_message({"last_cycle_time": int(spot.now - 3 * 3600)}, spot.now)
    assert msg.startswith("⚠️ <b>BOT DOWN</b> — I haven't checked the market for 3h 0m")



def test_jrn79_quiet_notice_exactly_once(cfg, tg):
    spot = FakeSpot(pairs=["BTC/USDT"])
    cfg.quiet = True
    bot = make_bot(cfg, spot)
    bot.cycle(now=spot.now)
    bot2 = wr.Bot(cfg, spot=bot.spot, deriv=bot.deriv)
    bot2.coins = ["BTC/USDT"]
    bot2.cycle(now=spot.now + 60)
    n = tg.texts("Quiet mode on")
    assert n == ["🤫 Quiet mode on — I'll keep testing silently and send one summary every day at 12:01 PM "
                 "(India time)."]
    assert tg.texts("time updated") == [] and bot2.state["summary_announced"] == "12:01 Asia/Kolkata"


def test_jrn80_quiet_off_is_phase1b(cfg, tg, monkeypatch):
    spot = FakeSpot(pairs=["BTC/USDT"], now=DAY + 4 * 3600)
    install_scores(monkeypatch, spot, {"BTC/USDT": (85, 0)})
    bot = make_bot(quiet(cfg, False), spot)
    bot.state["last_daily_summary"] = "2026-11-01"
    bot.cycle(now=spot.now)
    assert len(tg.texts("/100)")) == 1 and tg.texts("DAILY SUMMARY") == [] and tg.texts("Quiet mode") == []
    assert "quiet mode" not in tg.texts("times this hour")[0] and "Alerts sent: 1" in tg.texts("times this hour")[0]


def test_config_env_and_summary_time(monkeypatch):
    c = wr.Config()
    assert c.quiet is False and (c.summary_time, c.summary_tz) == ("12:01", "Asia/Kolkata")
    assert wr.quiet_notice(c).endswith("every day at 12:01 PM (India time).")
    monkeypatch.setenv("QUIET_MODE", "1")
    monkeypatch.setenv("DAILY_SUMMARY_TIME", "7:05")
    monkeypatch.setenv("DAILY_SUMMARY_TZ", "America/New_York")
    c = wr.Config()
    assert c.quiet and (c.summary_time, c.summary_tz) == ("07:05", "America/New_York")
    assert wr.quiet_notice(c).endswith("every day at 7:05 AM (US Eastern time).")
    assert wr.time_update_notice(c) == "🕐 Daily summary time updated: every day at 7:05 AM (US Eastern time)."
    monkeypatch.setenv("DAILY_SUMMARY_TIME", "bad")
    monkeypatch.setenv("DAILY_SUMMARY_TZ", "Mars/Olympus")
    assert (wr.Config().summary_time, wr.Config().summary_tz) == ("12:01", "Asia/Kolkata")
    monkeypatch.setenv("DAILY_SUMMARY_UTC", "2:30")  # legacy override wins only when explicitly set
    c = wr.Config()
    assert (c.summary_time, c.summary_tz) == ("02:30", "UTC") and "2:30 AM (UTC)" in wr.quiet_notice(c)
    ist = "Asia/Kolkata"  # 09:00 IST = 03:30 UTC
    assert wr.summary_slot(DAY + 3 * 3600 + 29 * 60, "09:00", ist) == "2026-11-27"
    assert wr.summary_slot(DAY + 3 * 3600 + 30 * 60, "09:00", ist) == "2026-11-28"
    assert wr.clock12("00:00") == "12:00 AM" and wr.clock12("13:45") == "1:45 PM" and wr.clock12("12:00") == "12:00 PM"


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc).timestamp()


def test_dst_boundaries_other_zone():
    ny = "America/New_York"
    # winter (EST, UTC-5): 1:30 AM = 06:30 UTC
    assert wr.summary_slot(utc(2026, 12, 15, 6, 29), "01:30", ny) == "2026-12-14"
    assert wr.summary_slot(utc(2026, 12, 15, 6, 30), "01:30", ny) == "2026-12-15"
    # summer (EDT, UTC-4): 1:30 AM = 05:30 UTC
    assert wr.summary_slot(utc(2026, 7, 1, 5, 29), "01:30", ny) == "2026-06-30"
    assert wr.summary_slot(utc(2026, 7, 1, 5, 30), "01:30", ny) == "2026-07-01"
    # spring forward 2026-03-08: 02:30 doesn't exist → due at 3:00 EDT
    assert wr.summary_slot(utc(2026, 3, 8, 6, 59), "02:30", ny) == "2026-03-07"
    assert wr.summary_slot(utc(2026, 3, 8, 7, 0), "02:30", ny) == "2026-03-08"


def test_dst_fall_back_day_sends_once(cfg, tg):
    quiet(cfg)
    cfg.summary_time, cfg.summary_tz = "01:30", "America/New_York"
    first = utc(2026, 11, 1, 5, 31)   # 1:31 AM EDT
    spot = FakeSpot(pairs=["BTC/USDT"], now=first)
    bot = make_bot(cfg, spot)
    bot.state.update(last_daily_summary="2026-10-31", summary_sched="01:30 America/New_York", quiet_notice_sent=True)
    for t in (first, utc(2026, 11, 1, 6, 31), utc(2026, 11, 1, 7, 0)):  # 1:31 AM EST happens an hour later
        spot.now = t
        bot.cycle(now=t)
    assert len(tg.texts("DAILY SUMMARY")) == 1 and bot.state["last_daily_summary"] == "2026-11-01"
    spot.now = utc(2026, 11, 2, 6, 31)  # next day 1:31 AM EST
    bot.cycle(now=spot.now)
    assert len(tg.texts("DAILY SUMMARY")) == 2


def test_upgrade_announces_new_time_once(cfg, tg):
    """State from the 09:00 IST version: one 'time updated' notice, no second quiet notice, summary at 12:01 IST."""
    now = DAY + 3 * 3600 + 40 * 60  # 09:10 IST; the old version already sent today's 09:00 summary
    spot = FakeSpot(pairs=["BTC/USDT"], now=now)
    cfg.quiet = True
    bot = make_bot(cfg, spot)
    bot.state.update(last_daily_summary="2026-11-28", quiet_notice_sent=True)
    bot.cycle(now=now)
    bot.cycle(now=now + 60)
    assert tg.texts("time updated") == ["🕐 Daily summary time updated: every day at 12:01 PM (India time)."]
    assert tg.texts("Quiet mode on") == [] and tg.texts("DAILY SUMMARY") == []
    spot.now = DAY + 6 * 3600 + 29 * 60  # 11:59 IST
    bot.cycle(now=spot.now)
    assert tg.texts("DAILY SUMMARY") == []
    spot.now = DAY + 6 * 3600 + 32 * 60  # 12:02 IST
    bot.cycle(now=spot.now)
    bot.cycle(now=spot.now + 60)
    assert len(tg.texts("DAILY SUMMARY")) == 1 and len(tg.texts("time updated")) == 1
    cfg.summary_time = "18:30"  # user changes it again → announced once more
    bot.cycle(now=spot.now + 120)
    bot.cycle(now=spot.now + 180)
    assert tg.texts("time updated")[-1] == "🕐 Daily summary time updated: every day at 6:30 PM (India time)."
    assert len(tg.texts("time updated")) == 2


def test_changed_schedule_waits_for_next_time(cfg, tg):
    now = DAY + 12 * 3600
    spot = FakeSpot(pairs=["BTC/USDT"], now=now)
    bot = make_bot(quiet(cfg), spot)
    bot.state.update(last_daily_summary="2026-11-27", summary_sched="09:00 Asia/Kolkata", quiet_notice_sent=True)
    cfg.summary_time = "20:00"  # moved later on a day whose summary is still due → wait for the new time
    bot.cycle(now=now)
    assert tg.texts("DAILY SUMMARY") == [] and bot.state["summary_sched"] == "20:00 Asia/Kolkata"
    spot.now = DAY + 14 * 3600 + 31 * 60  # 20:01 IST
    bot.cycle(now=spot.now)
    assert len(tg.texts("DAILY SUMMARY")) == 1


def test_workflow_quiet_vars():
    import os
    y = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".github/workflows/war_room.yml")).read()
    assert re.search(r"QUIET_MODE: \$\{\{ vars\.QUIET_MODE \|\| '1' \}\}", y)
    assert re.search(r"DAILY_SUMMARY_TIME: \$\{\{ vars\.DAILY_SUMMARY_TIME \|\| '12:01' \}\}", y)
    assert re.search(r"DAILY_SUMMARY_TZ: \$\{\{ vars\.DAILY_SUMMARY_TZ \|\| 'Asia/Kolkata' \}\}", y)
    assert "DAILY_SUMMARY_UTC" not in y
