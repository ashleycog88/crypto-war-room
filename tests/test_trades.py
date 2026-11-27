"""Trade resolution, fees, records (JRN-18..26, 50)."""
import pytest

import war_room_bot as wr
from fakes import FakeSpot, make_bot

T0 = 1_800_000_000 // 300 * 300  # entry exactly on a 5m boundary
TF = 300_000


def trade(direction="LONG", grade="A"):
    if direction == "LONG":
        lv = dict(entry=100.0, sl=98.2, tp1=102.0, tp2=103.6)
    else:
        lv = dict(entry=100.0, sl=101.8, tp1=98.0, tp2=96.4)
    return dict(symbol="BTC/USDT", dir=direction, grade=grade, entry_time=T0, tp1_hit=False, checked_until=0, **lv)


def bars(*hl):
    """hl: (high, low) per closed 5m candle after entry."""
    return [[float(T0 * 1000 + i * TF), 100.0, h, l, (h + l) / 2, 1.0] for i, (h, l) in enumerate(hl)]


def run(t, candles, live=None, now=None, hold=24):
    now = now or T0 + 3600
    return wr.resolve_trade(t, candles, live, now, hold)


def closes(evs):
    return [e for e in evs if e["type"] == "CLOSE"]


def test_jrn18_long_stop_loss():
    t = trade()
    ev = run(t, bars((100.5, 99.5), (100.0, 98.0)))
    c = closes(ev)[0]
    assert c["reason"] == "STOP LOSS" and round(c["gross"], 2) == -1.80 and round(c["net"], 2) == -2.00
    assert not c["win"] and t["status"] == "CLOSED"


def test_jrn19_long_tp1_then_tp2():
    t = trade()
    ev = run(t, bars((102.1, 100.5), (101.0, 100.2), (103.7, 101.5)))
    assert ev[0]["type"] == "TP1"
    msg = wr.format_tp1(t, ev[0])
    assert "TP1 HIT" in msg and "SL moved to breakeven" in msg
    c = closes(ev)[0]
    assert c["reason"] == "TP2 HIT" and round(c["gross"], 2) == 2.80 and round(c["net"], 2) == 2.60


def test_jrn19_incremental_across_cycles():
    t = trade()
    candles = bars((102.1, 100.5), (101.0, 100.2))
    ev1 = wr.resolve_trade(t, candles, 101.0, T0 + 600, 24)
    assert [e["type"] for e in ev1] == ["TP1"] and t["tp1_hit"]
    ev2 = wr.resolve_trade(t, candles + bars((0, 0), (0, 0), (103.7, 101.5))[2:], 103.0, T0 + 900, 24)
    assert [e["type"] for e in ev2] == ["CLOSE"] and round(ev2[0]["net"], 2) == 2.60


def test_jrn20_long_tp1_then_breakeven():
    t = trade()
    ev = run(t, bars((102.1, 100.5), (100.5, 99.9)))
    c = closes(ev)[0]
    assert "BREAKEVEN" in c["reason"] and round(c["gross"], 2) == 1.00 and round(c["net"], 2) == 0.80 and c["win"]


def test_jrn21_same_candle_sl_first():
    t = trade()
    ev = run(t, bars((102.5, 98.0)))
    assert [e["type"] for e in ev] == ["CLOSE"]
    assert ev[0]["reason"] == "STOP LOSS" and round(ev[0]["net"], 2) == -2.00


def test_same_candle_tp1_and_breakeven_is_conservative():
    t = trade()
    ev = run(t, bars((102.5, 99.5)))
    assert "BREAKEVEN" in closes(ev)[0]["reason"] and round(closes(ev)[0]["net"], 2) == 0.80


def test_same_candle_tp1_and_tp2():
    t = trade()
    ev = run(t, bars((103.7, 100.2)))
    assert closes(ev)[0]["reason"] == "TP2 HIT" and round(closes(ev)[0]["net"], 2) == 2.60


def test_jrn22_short_mirror_futures_fees():
    t = trade("SHORT")
    c = closes(run(t, bars((100.5, 99.5), (102.0, 100.0))))[0]
    assert c["reason"] == "STOP LOSS" and round(c["net"], 2) == -1.90
    t = trade("SHORT")
    c = closes(run(t, bars((99.5, 97.9), (99.0, 98.5), (98.5, 96.3))))[0]
    assert c["reason"] == "TP2 HIT" and round(c["net"], 2) == 2.70
    t = trade("SHORT")
    c = closes(run(t, bars((99.5, 97.9), (100.1, 99.0))))[0]
    assert "BREAKEVEN" in c["reason"] and round(c["net"], 2) == 0.90
    t = trade("SHORT")
    c = closes(run(t, bars((101.9, 97.5))))[0]
    assert c["reason"] == "STOP LOSS"


def test_jrn23_a_grade_time_exit():
    t = trade(grade="A")
    c = closes(wr.resolve_trade(t, bars((100.6, 99.5)), 100.5, T0 + 24 * 3600, 24))[0]
    assert c["reason"] == "TIME EXIT" and round(c["gross"], 2) == 0.50 and round(c["net"], 2) == 0.30


def test_jrn24_b_grade_time_exit_and_env(monkeypatch):
    t = trade(grade="B")
    c = closes(wr.resolve_trade(t, [], 99.5, T0 + 4 * 3600, 4))[0]
    assert c["reason"] == "TIME EXIT" and round(c["net"], 2) == -0.70
    t2 = trade(grade="B")
    assert closes(wr.resolve_trade(t2, [], 99.5, T0 + 3 * 3600 + 59 * 60, 4)) == []
    monkeypatch.setenv("MAX_HOLD_B_HOURS", "2")
    cfg = wr.Config()
    assert cfg.max_hold("B") == 2 and cfg.max_hold("A") == 24
    t3 = trade(grade="B")
    assert closes(wr.resolve_trade(t3, [], 99.5, T0 + 2 * 3600, cfg.max_hold("B")))[0]["reason"] == "TIME EXIT"


def test_time_exit_after_tp1_and_late_run():
    t = trade(grade="B")
    ev = wr.resolve_trade(t, bars((102.1, 100.5)), 101.0, T0 + 4 * 3600, 4)
    c = closes(ev)[0]
    assert c["reason"] == "TIME EXIT" and round(c["gross"], 2) == 1.50 and round(c["net"], 2) == 1.30
    t = trade(grade="B")
    cs = bars(*[(100.4, 99.6)] * 47 + [(100.4, 99.9)])
    cs[-1][4] = 100.3
    late = wr.resolve_trade(t, cs + [[float((T0 + 5 * 3600) * 1000), 1, 110, 99, 105, 1]], 105.0, T0 + 6 * 3600, 4)
    assert closes(late)[0]["exit"] == 100.3  # bot was down: closes at the candle at the deadline, not 6h later


def test_fees_configurable():
    assert wr.net_pnl("LONG", 1.0) == pytest.approx(0.8)
    assert wr.net_pnl("SHORT", 1.0) == pytest.approx(0.9)
    assert wr.net_pnl("LONG", 1.0, fee_spot=0.075) == pytest.approx(0.85)


def _close_into(bot, t, ev):
    tot = bot.state["totals"][t["grade"]]
    tot["w" if ev["win"] else "l"] += 1


def test_jrn25_separate_records_and_small_gross_is_loss(cfg, tg):
    spot = FakeSpot(pairs=["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT"])
    bot = make_bot(cfg, spot)
    now = int(spot.now)
    ent = now - 3600
    entry_ms = ent * 1000 // TF * TF + TF

    def mk(sym, grade, hl):
        return dict(symbol=sym, dir="LONG", grade=grade, entry=100.0, sl=98.2, tp1=102.0, tp2=103.6,
                    entry_time=ent, tp1_hit=False, checked_until=0), \
            [[float(entry_ms), 100, h, l, 100, 1] for h, l in [hl]]
    specs = [("BTC/USDT", "A", (103.7, 100.1)), ("ETH/USDT", "A", (100.1, 98.0)), ("SOL/USDT", "B", (103.7, 100.1))]
    for sym, g, hl in specs:
        t, c = mk(sym, g, hl)
        bot.state["active_trades"].append(t)
        spot.trade_candles[sym] = c
    spot.price = {s: 100.5 for s in spot.pairs}
    bot.check_trades(spot, spot.prices(spot.pairs), now)
    closes_ = tg.texts("CLOSED")
    assert len(closes_) == 3
    assert "A: 1W/1L (50%)" in closes_[-1] and "B: 1W/0L (100%)" in closes_[-1]
    assert bot.state["active_trades"] == [] and len(bot.state["closed_trades"]) == 3
    t = dict(symbol="XRP/USDT", dir="LONG", grade="B", entry=100.0, sl=98.2, tp1=102.0, tp2=103.6, entry_time=ent,
             tp1_hit=False, checked_until=0)
    ev = wr.resolve_trade(t, [], 100.15, ent + 4 * 3600, 4)
    assert round(ev[0]["gross"], 2) == 0.15 and round(ev[0]["net"], 2) == -0.05 and ev[0]["win"] is False


def test_jrn26_fetch_failure_keeps_trade_open(cfg, tg):
    spot = FakeSpot(pairs=["BTC/USDT"])
    bot = make_bot(cfg, spot)
    t = trade()
    t["entry_time"] = int(spot.now) - 600
    bot.state["active_trades"].append(t)
    spot.trade_fail.add("BTC/USDT")
    bot.check_trades(spot, {"BTC/USDT": 97.0}, spot.now)
    assert bot.state["active_trades"] == [t] and t.get("status") != "CLOSED"
    spot.trade_fail.clear()
    bot.check_trades(spot, {"BTC/USDT": 97.0}, spot.now)
    assert bot.state["active_trades"] == []


def test_jrn50_trade_tracked_after_coin_leaves_list(cfg, tg, monkeypatch):
    spot = FakeSpot(pairs=["BTC/USDT", "DOGE/USDT"])
    bot = make_bot(cfg, spot)
    bot.coins = ["BTC/USDT"]
    t = trade()
    t.update(symbol="DOGE/USDT", entry_time=int(spot.now) - 1200)
    bot.state["active_trades"].append(t)
    e_ms = (t["entry_time"] * 1000 // TF + 1) * TF
    spot.trade_candles["DOGE/USDT"] = [[float(e_ms), 100, 102.2, 100.1, 102, 1], [float(e_ms + TF), 100, 103.8, 101, 103, 1]]
    bot.cycle(now=spot.now)
    assert any("DOGE/USDT" in x for x in tg.texts("TP1 HIT"))
    assert any("DOGE/USDT" in x and "TP2 HIT" in x for x in tg.texts("CLOSED"))
    assert not [x for x in tg.texts("DOGE/USDT") if "-GRADE " in x.split("\n")[0] and "CLOSED" not in x]
