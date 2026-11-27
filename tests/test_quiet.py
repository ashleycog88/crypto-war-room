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
    bot = make_bot(quiet(cfg), spot)
    bot.cycle(now=spot.now)
    bot2 = wr.Bot(cfg, spot=bot.spot, deriv=bot.deriv)
    bot2.coins = ["BTC/USDT"]
    bot2.cycle(now=spot.now + 60)
    n = tg.texts("Quiet mode on")
    assert n == ["🤫 Quiet mode on — I'll keep testing silently and send one summary every day at 09:00 (India time)."]


def test_jrn80_quiet_off_is_phase1b(cfg, tg, monkeypatch):
    spot = FakeSpot(pairs=["BTC/USDT"], now=DAY + 4 * 3600)
    install_scores(monkeypatch, spot, {"BTC/USDT": (85, 0)})
    bot = make_bot(quiet(cfg, False), spot)
    bot.state["last_daily_summary"] = "2026-11-01"
    bot.cycle(now=spot.now)
    assert len(tg.texts("/100)")) == 1 and tg.texts("DAILY SUMMARY") == [] and tg.texts("Quiet mode") == []
    assert "quiet mode" not in tg.texts("times this hour")[0] and "Alerts sent: 1" in tg.texts("times this hour")[0]


def test_config_env_and_summary_time(monkeypatch):
    assert wr.Config().quiet is False and wr.Config().daily_summary_utc == "03:30"
    monkeypatch.setenv("QUIET_MODE", "1")
    monkeypatch.setenv("DAILY_SUMMARY_UTC", "2:30")
    c = wr.Config()
    assert c.quiet and c.daily_summary_utc == "02:30" and "08:00 (India time)" in wr.quiet_notice(c.daily_summary_utc)
    monkeypatch.setenv("DAILY_SUMMARY_UTC", "bad")
    assert wr.Config().daily_summary_utc == "03:30"
    assert wr.summary_slot(DAY + 3 * 3600, "03:30") == "2026-11-27"
    assert wr.summary_slot(DAY + 3 * 3600 + 30 * 60, "03:30") == "2026-11-28"


def test_workflow_quiet_vars():
    import os
    y = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".github/workflows/war_room.yml")).read()
    assert re.search(r"QUIET_MODE: \$\{\{ vars\.QUIET_MODE \|\| '1' \}\}", y)
    assert re.search(r"DAILY_SUMMARY_UTC: \$\{\{ vars\.DAILY_SUMMARY_UTC \|\| '03:30' \}\}", y)
