"""T1 adversarial checks for Phase 4 paper-watch (paper_watch.py) vs research.py decisions."""
import json
import time

import numpy as np

import paper_watch as pw
import research as rs
import war_room_bot as wr
from fakes import FakeSpot, make_bot

H, D4, DAY = rs.H, rs.TF["4h"], rs.TF["1d"]
T0 = 1_700_006_400_000 // DAY * DAY
EMPTY = (np.array([], np.int64), np.array([]), np.array([]))


def no_funding(base, start):
    return np.array([], np.int64), np.array([])


def key(t):
    return (t["coin"], int(t["entry_t"]), int(t["exit_t"]), round(t["entry"], 9), round(t["exit"], 9), t["why"])


def flip_rows(seed, days, beta, period_days=45, amp=0.35, vol=0.006):
    """1h rows whose trend flips (sinusoid) so BTC crosses its 4h EMA200 often → shorts and longs both fire."""
    rng = np.random.default_rng(seed)
    n = days * 24
    x = np.arange(n)
    trend = amp * beta * np.sin(2 * np.pi * x / (period_days * 24))
    c = 100 * np.exp(trend + np.cumsum(rng.normal(0, vol, n)))
    o = np.concatenate([[100.0], c[:-1]])
    u = np.abs(rng.normal(0, vol / 2, (2, n)))
    return np.column_stack([T0 + x * H, o, np.maximum(o, c) * (1 + u[0]), np.minimum(o, c) * (1 - u[1]), c, np.ones(n)])


def coins_flip(days):
    return {b: rs.Coin(b, flip_rows(i + 11, days, beta), EMPTY, "none")
            for i, (b, beta) in enumerate([("BTC", 1.0), ("ETH", 1.3), ("SOL", 1.6), ("XRP", 0.9)])}


def run_paper(coins, start, window=None, step=1, roundtrip=False):
    p = pw.DEFAULTS["s1"]
    full = {b: c.bars["4h"] for b, c in coins.items()}
    n4 = len(full["BTC"])
    st = pw.new_state(start / 1000)["s1"]
    first = int(np.searchsorted(full["BTC"][:, 0], start)) - 2
    ks = list(range(first, n4 + 1, step)) + [n4]
    for k in ks:
        lo = 0 if window is None else max(0, k - window)
        pw.s1_update(st, p, {b: r[lo:k] for b, r in full.items()}, no_funding, start, 0)
        if roundtrip:  # state saved/loaded as JSON between updates (restart)
            st = json.loads(json.dumps(st))
    return st, n4


def research_longs(coins, start, end, long_only_walk=False):
    p = pw.DEFAULTS["s1"]
    btc = coins["BTC"].bars["4h"]
    out = []
    for c in coins.values():
        L, S, make = rs.s1_setup(c, p, btc)
        tr = rs.walk(L, np.zeros_like(S) if long_only_walk else S, c.bars["4h"], make)
        out += [t for t in tr if t["side"] == "LONG"]
    return out, sum(1 for c in coins.values() for t in rs.s1(c, p, btc) if t["side"] == "SHORT")


def setup(monkeypatch, days=300, start_day=120):
    coins = coins_flip(days)
    start = T0 + start_day * DAY
    monkeypatch.setattr(rs, "START", start)
    monkeypatch.setattr(rs, "END", T0 + days * DAY)
    return coins, start


# S1: BTC filter flips — research S1-LONG comes from the BOTH-sides walk (shorts block longs on the same coin)
def test_s1_matches_research_long_side_with_btc_flips(monkeypatch):
    coins, start = setup(monkeypatch)
    st, n4 = run_paper(coins, start)
    ref, n_short = research_longs(coins, start, rs.END)
    ref = [t for t in ref if pw._closed(t, n4)]
    got = sorted(map(key, st["closed"]))
    want = sorted(map(key, ref))
    print(f"shorts in research={n_short} paper closed={len(got)} research longs closed={len(want)}")
    print("only in paper:", sorted(set(got) - set(want))[:5])
    print("only in research:", sorted(set(want) - set(got))[:5])
    assert n_short > 0
    assert got == want


# S1: paper-watch equals a LONG-only walk (documents what it actually implements)
def test_s1_equals_long_only_walk(monkeypatch):
    coins, start = setup(monkeypatch)
    st, n4 = run_paper(coins, start)
    ref, _ = research_longs(coins, start, rs.END, long_only_walk=True)
    ref = [t for t in ref if pw._closed(t, n4)]
    assert sorted(map(key, st["closed"])) == sorted(map(key, ref))


# S1: live uses a 1000-candle window (EMA200/ATR restart) + catch-up every 5 candles + JSON restart each update
def test_s1_window_catchup_restart_equal_bar_by_bar(monkeypatch):
    coins, start = setup(monkeypatch)
    a, _ = run_paper(coins, start)
    b, _ = run_paper(coins, start, window=1000, step=5, roundtrip=True)
    assert sorted(map(key, a["closed"])) == sorted(map(key, b["closed"]))
    assert set(a["open"]) == set(b["open"])
    assert abs(a["tot_R"] - b["tot_R"]) < 1e-6 and a["n"] == b["n"]
    whys = {t["why"] for t in a["closed"]}
    print("exit reasons seen:", whys, "n closed", a["n"])
    assert {"stop", "channel"} <= whys


# S1: a smooth uptrend after a breakout runs into the 360-bar time exit
def test_s1_time_exit_at_360_bars(monkeypatch):
    days = 140
    n = days * 24
    x = np.arange(n)
    c = 100 * np.exp(0.00004 * x)  # monotone up: no stop, no channel break
    c[: 70 * 24] = 100 * np.exp(0.002 * np.sin(x[: 70 * 24] / 5.0))  # flat-ish noise first
    o = np.concatenate([[c[0]], c[:-1]])
    rows = np.column_stack([T0 + x * H, o, np.maximum(o, c) * 1.0005, np.minimum(o, c) * 0.9995, c, np.ones(n)])
    coin = rs.Coin("ETH", rows, EMPTY, "none")
    start = T0 + 60 * DAY
    monkeypatch.setattr(rs, "START", start)
    monkeypatch.setattr(rs, "END", T0 + days * DAY)
    p = dict(pw.DEFAULTS["s1"], btc=False)
    a = coin.bars["4h"]
    ref = [t for t in rs.s1(coin, p, a) if t["side"] == "LONG"]
    st = pw.new_state(start / 1000)["s1"]
    for k in range(int(np.searchsorted(a[:, 0], start)) - 2, len(a) + 1):
        pw.s1_update(st, p, {"BTC": a[:k], "ETH": a[:k]}, no_funding, start, 0)
    eth_ref = [t for t in ref if pw._closed(t, len(a))]
    eth_got = [t for t in st["closed"] if t["coin"] == "ETH"]
    print("ref", [(t["why"], t["j"] - t["i"] + 1) for t in eth_ref], "got", [t["why"] for t in eth_got])
    assert any(t["why"] == "time" and t["j"] - t["i"] + 1 == rs.S1_MAX_BARS for t in eth_ref)
    assert sorted(key(t) for t in eth_got) == sorted(key(t) for t in eth_ref)


# S5: catch-up after missed days equals a daily update
def test_s5_catch_up_equals_daily(monkeypatch):
    days, start_day = 200, 120
    coins = {b: rs.Coin(b, flip_rows(i + 3, days, 1 + i / 5), EMPTY, "none")
             for i, b in enumerate(["BTC", "ETH", "SOL", "XRP", "ADA"])}
    start = T0 + start_day * DAY
    p = pw.DEFAULTS["s5"]
    a = pw.new_state(start / 1000)["s5"]
    b = pw.new_state(start / 1000)["s5"]
    for m in range(start_day - 1, days):
        pw.s5_update(a, p, {x: c.bars["1d"][max(0, m - 120):m] for x, c in coins.items()},
                     {x: float(c.bars["1d"][m, 1]) for x, c in coins.items()}, start, 0)
        if (m - start_day) % 3 == 0 or m == days - 1:
            pw.s5_update(b, p, {x: c.bars["1d"][max(0, m - 120):m] for x, c in coins.items()},
                         {x: float(c.bars["1d"][m, 1]) for x, c in coins.items()}, start, 0)
            b = json.loads(json.dumps(b))
    assert a["held"] == b["held"] and abs(a["equity"] - b["equity"]) < 1e-12
    assert [e["t"] for e in a["events"]] == [e["t"] for e in b["events"]]


# Bot: 'announced' set by the daily summary must survive a paper-watch result landing afterwards
def test_announced_not_lost_when_update_lands_after_summary(cfg, tg, monkeypatch):
    now = time.time()
    cfg.paper_watch, cfg.quiet = True, True
    bot = make_bot(cfg, FakeSpot())
    st = pw.new_state(now - 86400)
    bot.state["paper_watch"] = st
    bot.state["last_daily_summary"] = "0"
    bot.state["summary_sched"] = wr.sched_key(cfg)
    import threading
    gate = threading.Event()

    def slow_update(s, n, a=None, b=None):
        gate.wait(5)
        out = json.loads(json.dumps(s))
        out["last_ok"] = int(n)
        return out
    monkeypatch.setattr(pw, "update", slow_update)
    bot.maybe_paper_watch(now)  # thread started with a snapshot (announced False)
    bot.maybe_daily_summary(now)  # summary sent meanwhile → announced True
    assert bot.state["paper_watch"]["announced"] is True
    gate.set()
    bot.pw_thread.join(5)
    bot.maybe_paper_watch(now + 60)  # result harvested
    assert bot.state["paper_watch"]["announced"] is True, "announced flag overwritten by the thread's snapshot"


# Bot: a malformed paper_watch state must not block the whole daily summary
def test_bad_paper_watch_state_does_not_block_daily_summary(cfg, tg, monkeypatch):
    now = time.time()
    cfg.paper_watch, cfg.quiet = True, True
    bot = make_bot(cfg, FakeSpot())
    bot.state["paper_watch"] = {"started": int(now), "announced": True, "last_ok": 0}  # old/partial schema
    bot.state["last_daily_summary"] = "0"
    bot.state["summary_sched"] = wr.sched_key(cfg)
    bot._safe("daily summary", bot.maybe_daily_summary, now)
    assert tg.texts("DAILY SUMMARY"), "daily summary not sent because paper_watch_lines raised"


# S1: case found by scratch_s1_short_block.py (seed 4) — a SHORT open in research blocks a LONG the paper-watch takes
def test_s1_short_open_blocks_long_like_research(monkeypatch):
    seed, days = 4, 300
    start = T0 + 120 * DAY
    monkeypatch.setattr(rs, "START", start)
    monkeypatch.setattr(rs, "END", T0 + days * DAY)
    coins = {b: rs.Coin(b, flip_rows(seed * 7 + i, days, beta, period_days=30 + seed % 5 * 10), EMPTY, "none")
             for i, (b, beta) in enumerate([("BTC", 1.0), ("ETH", 1.3), ("SOL", -0.8), ("XRP", 0.4)])}
    st, n4 = run_paper(coins, start)
    ref, _ = research_longs(coins, start, rs.END)
    ref = [t for t in ref if pw._closed(t, n4)]
    got, want = set(map(key, st["closed"])), set(map(key, ref))
    print("only in paper:", sorted(got - want), "only in research:", sorted(want - got))
    assert got == want
