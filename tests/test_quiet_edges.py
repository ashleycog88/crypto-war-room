"""Phase 2b quiet mode — reviewer edge cases (T1). Tests marked xfail document found bugs."""
from datetime import datetime, timezone

import war_room_bot as wr
from fakes import FakeSpot, install_scores, make_bot

TF = 300_000
DAY = datetime(2026, 11, 28, tzinfo=timezone.utc).timestamp()


def q(cfg, on=True):
    cfg.quiet = on
    cfg.summary_time = "09:00"  # 09:00 IST = 03:30 UTC
    return cfg


# non-quiet: failed alert delivery must not count an AI verdict (ai_log popped)
def test_ai_log_not_counted_when_loud_alert_fails(cfg, tg, monkeypatch):
    spot = FakeSpot(pairs=["BTC/USDT"], now=DAY + 3600)
    install_scores(monkeypatch, spot, {"BTC/USDT": (85, 0)})
    bot = make_bot(q(cfg, False), spot)
    tg.fail = True
    bot.cycle(now=spot.now)
    assert bot.state["active_trades"] == [] and bot.state["ai_log"] == []
    tg.fail = False
    spot.now += 60
    bot.cycle(now=spot.now)
    assert len(bot.state["active_trades"]) == 1 and len(bot.state["ai_log"]) == 1


# quiet: trades recorded even with Telegram down, no outbox growth from per-trade msgs
def test_quiet_records_trade_when_telegram_down(cfg, tg, monkeypatch):
    spot = FakeSpot(pairs=["BTC/USDT"], now=DAY + 3600)
    install_scores(monkeypatch, spot, {"BTC/USDT": (85, 0)})
    bot = make_bot(q(cfg), spot)
    tg.fail = True
    bot.cycle(now=spot.now)
    assert len(bot.state["active_trades"]) == 1
    assert not [m for m in bot.state["outbox"] if "/100)" in m["text"]]


# ai_log pruned to 48h
def test_ai_log_pruned(cfg, tg):
    spot = FakeSpot(pairs=["BTC/USDT"], now=DAY)
    bot = make_bot(q(cfg), spot)
    bot.state["ai_log"] = [[DAY - 3 * 86400, "APPROVED"], [DAY - 3600, "REJECTED"]]
    bot.prune(DAY)
    assert bot.state["ai_log"] == [[DAY - 3600, "REJECTED"]]


# summary_slot boundaries
def test_slot_edges():
    assert wr.summary_slot(DAY, "00:00") == "2026-11-28"
    assert wr.summary_slot(DAY - 1, "00:00") == "2026-11-27"
    assert wr.summary_slot(DAY + 23 * 3600 + 59 * 60, "23:59") == "2026-11-28"
    assert wr.summary_slot(DAY, "01:30", "America/New_York") == "2026-11-27"  # 7 PM ET on 11-27 (1:30 AM that day already passed)
    assert wr.summary_slot(datetime(2027, 1, 1, 1, tzinfo=timezone.utc).timestamp(), "03:30") == "2026-12-31"


# several missed days -> only one summary
def test_multi_day_gap_sends_one_summary(cfg, tg):
    spot = FakeSpot(pairs=["BTC/USDT"], now=DAY + 5 * 3600)
    bot = make_bot(q(cfg), spot)
    bot.state.update(last_daily_summary="2026-11-20", quiet_notice_sent=True)
    bot.cycle(now=spot.now)
    spot.now += 120
    bot.cycle(now=spot.now)
    assert len(tg.texts("DAILY SUMMARY")) == 1 and bot.state["last_daily_summary"] == "2026-11-28"


# quiet notice is re-armed after quiet off -> on
def test_quiet_notice_rearmed_after_toggle(cfg, tg):
    spot = FakeSpot(pairs=["BTC/USDT"], now=DAY + 3600)
    bot = make_bot(q(cfg), spot)
    bot.cycle(now=spot.now)
    cfg.quiet = False
    spot.now += 60
    bot.cycle(now=spot.now)
    cfg.quiet = True
    spot.now += 60
    bot.cycle(now=spot.now)
    assert len(tg.texts("Quiet mode on")) == 2


# close resolved after the summary in the same cycle (backlog after downtime) must appear in the next summary
def test_close_resolved_after_summary_is_reported_somewhere(cfg, tg, monkeypatch):
    spot = FakeSpot(pairs=["BTC/USDT"], now=DAY + 2 * 3600)
    install_scores(monkeypatch, spot, {"BTC/USDT": (85, 0)})
    bot = make_bot(q(cfg), spot)
    bot.cycle(now=spot.now)  # opens trade at 02:00, sets last_daily_summary=2026-11-27
    install_scores(monkeypatch, spot, {})
    t = bot.state["active_trades"][0]
    sl_ms = (int(spot.now) * 1000 // TF + 2) * TF  # SL hit ~02:10 UTC
    spot.trade_candles = {"BTC/USDT": [[float(sl_ms), t["entry"], t["entry"], t["sl"] * 0.99, t["sl"] * 0.99, 1]]}
    spot.now = DAY + 4 * 3600 + 600  # bot was down 02:00..04:10; next run starts
    bot.cycle(now=spot.now)  # summary sent first, then the 02:10 close is resolved
    spot.trade_candles = {}
    spot.now = DAY + 86400 + 4 * 3600
    bot.cycle(now=spot.now)
    s = tg.texts("DAILY SUMMARY")
    assert len(s) == 2
    assert any("LOSS BTC" in x for x in s), s


# quiet off -> on later: documents current behaviour (summary sent at once since last_daily_summary is stale)
def test_reenable_quiet_after_long_off_behaviour(cfg, tg):
    spot = FakeSpot(pairs=["BTC/USDT"], now=DAY + 12 * 3600)
    bot = make_bot(q(cfg), spot)
    bot.cycle(now=spot.now)
    cfg.quiet = False
    spot.now = DAY + 10 * 86400
    bot.cycle(now=spot.now)
    cfg.quiet = True
    spot.now += 60
    bot.cycle(now=spot.now)
    assert len(tg.texts("DAILY SUMMARY")) == 0  # re-enabled quiet waits for the next summary time
    spot.now = DAY + 11 * 86400 + 3 * 3600 + 31 * 60
    bot.cycle(now=spot.now)
    assert len(tg.texts("DAILY SUMMARY")) == 1


# malformed ai_log entry in state.json must not crash the cycle (Bot.prune is not wrapped in _safe)
def test_malformed_ai_log_entry_does_not_crash(cfg, tg, tmp_path):
    import json
    p = tmp_path / "state.json"
    st = wr.empty_state()
    st["ai_log"] = [None, [DAY, "APPROVED"]]
    p.write_text(json.dumps(st))
    spot = FakeSpot(pairs=["BTC/USDT"], now=DAY + 60)
    bot = make_bot(q(cfg), spot)
    assert bot.cycle(now=spot.now) is True
