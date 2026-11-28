"""Phase 4 Part A: paper-watch of the Phase 3 near misses (JRN-140..143) and the outside-watchdog line."""
import time

import numpy as np

import paper_watch as pw
import research as rs
import war_room_bot as wr
from fakes import FakeResp, FakeSpot, make_bot

H = rs.H
T0 = 1_700_006_400_000 // rs.TF["1d"] * rs.TF["1d"]  # UTC midnight
EMPTY = (np.array([], np.int64), np.array([]), np.array([]))


def rows_1h(seed, days, drift=0.0003, vol=0.008):
    rng = np.random.default_rng(seed)
    n = days * 24
    c = 100 * np.exp(np.cumsum(rng.normal(drift, vol, n)))
    o = np.concatenate([[100.0], c[:-1]])
    u = np.abs(rng.normal(0, vol / 2, (2, n)))
    return np.column_stack([T0 + np.arange(n) * H, o, np.maximum(o, c) * (1 + u[0]), np.minimum(o, c) * (1 - u[1]),
                            c, np.ones(n)])


def research_coins(bases, days):
    return {b: rs.Coin(b, rows_1h(i + 1, days, drift=0.0004 if b == "BTC" else 0.0002), EMPTY, "none")
            for i, b in enumerate(bases)}


def no_funding(base, start):
    return np.array([], np.int64), np.array([])


def key(t):
    return (t["coin"], int(t["entry_t"]), int(t["exit_t"]), round(t["entry"], 9), round(t["exit"], 9))


def test_s1_paper_watch_matches_research(monkeypatch):
    days, start_day = 260, 150
    coins = research_coins(["BTC", "ETH", "SOL"], days)
    start = T0 + start_day * rs.TF["1d"]
    monkeypatch.setattr(rs, "START", start)
    monkeypatch.setattr(rs, "END", T0 + days * rs.TF["1d"])
    p = pw.DEFAULTS["s1"]
    btc = coins["BTC"].bars["4h"]
    ref = [t for c in coins.values() for t in rs.s1(c, p, btc) if t["side"] == "LONG"]
    n4 = len(btc)
    ref = [t for t in ref if pw._closed(t, n4)]
    st = pw.new_state(start / 1000)["s1"]
    first = int(np.searchsorted(btc[:, 0], start)) - 2
    for k in list(range(first, n4 + 1, 1)):  # one update per closed 4h candle
        pw.s1_update(st, p, {b: c.bars["4h"][:k] for b, c in coins.items()}, no_funding, start, 0)
    got = st["closed"]
    assert len(ref) >= 5, "synthetic data should produce some breakouts"
    assert sorted(map(key, got)) == sorted(map(key, ref))
    nets = {key(t): rs.net(t, "F") for t in ref}
    assert all(abs(t["net"] - nets[key(t)]) < 1e-3 for t in got)  # same fee model F


def test_s1_catch_up_after_downtime_and_no_duplicates(monkeypatch):
    days, start_day = 230, 150
    coins = research_coins(["BTC", "ETH"], days)
    start = T0 + start_day * rs.TF["1d"]
    p = pw.DEFAULTS["s1"]
    full = {b: c.bars["4h"] for b, c in coins.items()}
    n4 = len(full["BTC"])
    a = pw.new_state(start / 1000)["s1"]
    b = pw.new_state(start / 1000)["s1"]
    first = int(np.searchsorted(full["BTC"][:, 0], start)) - 2
    for k in range(first, n4 + 1):
        pw.s1_update(a, p, {x: r[:k] for x, r in full.items()}, no_funding, start, 0)
    for k in range(first, n4 + 1, 7):  # updates missed for 28 h at a time
        pw.s1_update(b, p, {x: r[:k] for x, r in full.items()}, no_funding, start, 0)
    pw.s1_update(b, p, full, no_funding, start, 0)
    assert sorted(map(key, a["closed"])) == sorted(map(key, b["closed"]))
    before = (list(map(key, b["closed"])), dict(b["open"]))
    pw.s1_update(b, p, full, no_funding, start, 0)  # same candles again (e.g. after a restart)
    assert (list(map(key, b["closed"])), dict(b["open"])) == before


def test_s5_paper_watch_matches_research(monkeypatch):
    days, start_day = 200, 120
    bases = ["BTC", "ETH", "SOL", "XRP", "ADA"]
    coins = research_coins(bases, days)
    start = T0 + start_day * rs.TF["1d"]
    monkeypatch.setattr(rs, "START", start)
    monkeypatch.setattr(rs, "END", T0 + days * rs.TF["1d"])
    monkeypatch.setattr(pw, "KEEP", 10_000)
    p = pw.DEFAULTS["s5"]
    ref = rs.s5(list(coins.values()), p)
    st = pw.new_state(start / 1000)["s5"]
    for m in range(start_day - 1, days):  # m closed days, the next day's open known
        daily = {b: c.bars["1d"][:m] for b, c in coins.items()}
        nxt = {b: float(c.bars["1d"][m, 1]) for b, c in coins.items()}
        pw.s5_update(st, p, daily, nxt, start, 0)
    ev = st["events"]
    assert len(ev) >= 50
    by_t = {x["t"]: x for x in ref}
    for e, prev in zip(ev[1:], ev[:-1]):
        r = by_t[prev["t"]]
        assert abs(e["net_prev"] - r["net"]) < 1e-9  # same holdings, same open-to-open return, same spot fees
    held = set()
    for e in ev:  # identical holdings after every rebalance
        held = (held - set(e["sold"])) | set(e["bought"])
        if e["t"] in by_t:  # research has no row for the last day (its period hasn't ended yet)
            assert held == set(by_t[e["t"]]["coins"])
    assert sum(e["t"] in by_t for e in ev) >= len(ev) - 1
    assert any(e["bought"] or e["sold"] for e in ev[1:])


def test_params_overridable_and_unknown_keys_ignored(cfg):
    bad = wr.apply_settings(cfg, '{"WATCH_S1": {"n": 55, "junk": 1}, "WATCH_S5": {"k": 5}, "PAPER_WATCH": false}')
    assert bad == [] and cfg.paper_watch is False
    p1, p5 = pw.params(cfg.watch_s1, cfg.watch_s5)
    assert p1 == {"n": 55, "exit": "channel", "btc": True} and p5 == {"k": 5, "reb": "daily"}
    assert pw.params() == (pw.DEFAULTS["s1"], pw.DEFAULTS["s5"])


def sample_state(now):
    st = pw.new_state(now - 3 * 86400)
    st["last_ok"] = int(now - 600)
    st["s1"]["open"]["ETH"] = {"sig_t": 0, "entry_t": int((now - 3600) * 1000), "entry": 3000.0}
    st["s1"]["closed"] = [{"coin": "BTC", "entry_t": int((now - 2 * 86400) * 1000), "exit_t": int((now - 7200) * 1000),
                           "entry": 1, "exit": 1, "why": "channel", "net": 2.5, "R": 1.2, "at": now}]
    st["s1"]["tot_R"], st["s1"]["n"] = 1.2, 1
    st["s5"].update(held={"SOL": {"w": 1 / 3, "px": 1}, "XRP": {"w": 1 / 3, "px": 1}, "DOGE": {"w": 1 / 3, "px": 1}},
                    equity=1.034, events=[{"t": int((now - 3600) * 1000), "at": now, "bought": ["DOGE"],
                                           "sold": ["ADA"], "net_prev": 0.4}])
    return st


def summary_bot(cfg, tg, monkeypatch, st):
    cfg.paper_watch, cfg.quiet = True, True
    bot = make_bot(cfg, FakeSpot())
    bot.state["paper_watch"] = st
    bot.state["last_daily_summary"] = "0"
    bot.state["summary_sched"] = wr.sched_key(cfg)
    return bot


def test_daily_summary_block_and_first_start_line(cfg, tg, monkeypatch):
    now = time.time()
    bot = summary_bot(cfg, tg, monkeypatch, sample_state(now))
    bot.maybe_daily_summary(now)
    text = tg.texts("DAILY SUMMARY")[-1]
    assert "🔭 Now watching 4 near-miss strategies." in text
    assert "🔭 <b>Watching (paper, not tradeable yet)</b> — day 4" in text
    assert "4h breakout (buy, majors): holding ETH · 24 h: opened 1, closed 1 (+2.50%)" in text
    assert "since start: +1.20% at 1% risk per trade after fees (1 closed)" in text
    assert "Top-3 momentum (majors, spot): holding DOGE, SOL, XRP · 24 h: 1 rebalance(s) (bought DOGE, sold ADA)" in text
    assert "since start: +3.40% after fees" in text
    assert bot.state["paper_watch"]["announced"] is True
    bot.state["last_daily_summary"] = "0"
    bot.maybe_daily_summary(now + 60)
    assert len(tg.texts("DAILY SUMMARY")) == 2
    assert "Now watching 4" not in tg.texts("DAILY SUMMARY")[-1]
    assert all("DAILY SUMMARY" in t for t in tg.texts())  # paper-watch never sends its own alerts


def test_update_runs_off_cycle_and_failures_keep_positions(cfg, tg, monkeypatch):
    now = time.time()
    st = sample_state(now)
    st["last_ok"] = 0  # due
    bot = summary_bot(cfg, tg, monkeypatch, st)

    def slow_fail(*a, **k):
        time.sleep(1.5)
        raise ConnectionError("down")
    monkeypatch.setattr(pw, "get_klines", slow_fail)
    t0 = time.perf_counter()
    bot.maybe_paper_watch(now)
    assert time.perf_counter() - t0 < 0.5  # the scan cycle is not held up
    bot.pw_thread.join(5)
    bot.maybe_paper_watch(now + 1)
    assert bot.state["paper_watch"]["s1"]["open"] == st["s1"]["open"]  # update skipped, positions kept
    assert bot.pw_retry > now and bot.pw_thread is None
    bot.maybe_paper_watch(now + 2)
    assert bot.pw_thread is None  # waits for the retry time
    assert not tg.texts()


def test_successful_update_is_saved_encrypted_and_survives_restart(cfg, tg, monkeypatch):
    cfg.data_key = "throwaway-test-passphrase"
    now = time.time()
    bot = summary_bot(cfg, tg, monkeypatch, None)
    new = sample_state(now)
    monkeypatch.setattr(pw, "update", lambda st, now, *a, **k: new)
    bot.maybe_paper_watch(now)
    bot.pw_thread.join(5)
    bot.maybe_paper_watch(now + 1)
    assert bot.state["paper_watch"]["s1"]["open"].keys() == {"ETH"}
    raw = open(cfg.state_file + ".enc", "rb").read()
    assert b"paper_watch" not in raw and b"ETH" not in raw
    bot2 = wr.Bot(cfg, spot=bot.spot, deriv=bot.deriv)
    assert bot2.state["paper_watch"] == bot.state["paper_watch"]


def test_healthcheck_line_three_states(cfg, monkeypatch):
    url = "https://hc-ping.com/secret-uuid-1234"
    assert wr.healthcheck_line(False, None).endswith("➖ not set up")
    bot = make_bot(cfg, FakeSpot())
    bot.healthcheck()
    assert bot.healthcheck_line() == "🩺 Outside watchdog (Healthchecks): ➖ not set up"
    cfg.healthcheck_url = url
    for resp, want in ((FakeResp(200), "✅ connected"), (FakeResp(503), "❌ ping failing (HTTP 503)"),
                       (ConnectionError(f"Max retries exceeded with url: {url}"), "❌ ping failing (no connection)")):
        def fake(u, t, params=None, r=resp):
            if isinstance(r, Exception):
                raise r
            return r
        monkeypatch.setattr(wr, "_http_get", fake)
        bot.healthcheck()
        line = bot.healthcheck_line()
        assert line == f"🩺 Outside watchdog (Healthchecks): {want}"
        hb = wr.format_heartbeat(bot.heartbeat_info(time.time()))
        assert line in hb and "hc-ping" not in hb and "secret-uuid" not in hb


def port_inputs():
    from test_factors import synthetic
    mx = synthetic()
    names = ["BTC"] + [f"C{i}" for i in range(1, mx["C"].shape[0])]
    rows = {b: np.column_stack([mx["days"], mx["O"][i], np.maximum(mx["O"][i], mx["C"][i]) * 1.01,
                                np.minimum(mx["O"][i], mx["C"][i]) * 0.99, mx["C"][i], mx["V"][i] / mx["C"][i]])
            for i, b in enumerate(names)}
    fund = {b: (mx["coins"][i].ft, mx["coins"][i].fn / 8) for i, b in enumerate(names)}
    return names, rows, fund


def test_s6_s8_paper_watch_match_factors():
    import factors as fx
    names, rows, fund = port_inputs()
    for fam in ("s6", "s8"):
        p = pw.DEFAULTS[fam]
        coins = []
        for b in names:
            c = pw.WatchCoin(b, {"1d": rows[b]}, fund[b] if fam == "s6" else None)
            c.fn = c.fr * 8
            coins.append(c)
        mx = fx.matrices(coins)
        decide, step, model = fx.s6_decide(mx, p) if fam == "s6" else fx.s8_decide(mx, p)
        sched = fx.schedule(mx, decide, step)
        ref = fx.evaluate(mx, sched, step, model)
        st = pw.port_state(rows["BTC"][0, 0] / 1000)
        n = len(rows["BTC"])
        for m in range(215, n):  # one update per daily close, the next open known
            pw.port_update(st, fam, {b: r[:m] for b, r in rows.items()}, {b: float(r[m, 1]) for b, r in rows.items()},
                           fund if fam == "s6" else {}, 0)
        got = [e["net_prev"] for e in st["events"] if e["net_prev"] is not None]
        assert len(got) >= 10 and len(ref) >= len(got)
        assert np.allclose(got, [x["net"] for x in ref[:len(got)]], atol=1e-9), fam
        d_last = int(np.searchsorted(mx["days"], st["d_t"]))
        L, S, scale, _ = decide(d_last)  # the latest decision (its period hasn't ended, so research has no row yet)
        assert st["w"] == {names[x]: v for x, v in fx.weights(L, S, scale).items() if v}
        assert abs(st["equity"] - np.prod([1 + x["net"] / 100 for x in ref[:len(got)]])) < 1e-9


def test_s6_s8_update_catch_up_matches_factors(monkeypatch):
    """End to end through update(): 400-bar windows, funding only from the start asked for, 17-day gaps."""
    import factors as fx
    names, rows, fund = port_inputs()
    days = rows["BTC"][:, 0]
    cur = {"m": 0}

    def klines(base, interval, limit, now):
        r = rows[base][:cur["m"] + 1][-limit:]
        return r[:-1].copy(), float(r[-1, 1])

    def funding(name, start_ms):
        if name not in fund:
            return np.array([], np.int64), np.array([])
        ft, fr = fund[name]
        k = (ft >= start_ms) & (ft <= days[cur["m"]] + 5 * 60_000)
        return ft[k], fr[k]
    monkeypatch.setattr(pw, "get_klines", klines)
    monkeypatch.setattr(pw, "get_funding", funding)
    monkeypatch.setattr(pw, "s1_update", lambda *a, **k: None)
    monkeypatch.setattr(pw, "s5_update", lambda *a, **k: None)
    start = days[0] + 420 * fx.DAY
    for fam in ("s6", "s8"):
        coins = []
        for b in names:
            c = pw.WatchCoin(b, {"1d": rows[b]}, fund[b] if fam == "s6" else None)
            c.fn = c.fr * 8
            coins.append(c)
        mx = fx.matrices(coins)
        decide, step, model = fx.s6_decide(mx, pw.DEFAULTS[fam]) if fam == "s6" else fx.s8_decide(mx, pw.DEFAULTS[fam])
        ref = fx.evaluate(mx, [x for x in fx.schedule(mx, decide, step) if days[x[0]] >= start - fx.DAY], step, model)
        st = pw.new_state(start / 1000)
        for m in range(int(np.searchsorted(days, start)) + 1, len(days), 17):  # bot down 16 of every 17 days
            cur["m"] = m
            st = pw.update(st, days[m] / 1000 + 300, coins=["BTC"], top=names, s6=fam == "s6", s8=fam == "s8")
        got = [e["net_prev"] for e in st[fam]["events"] if e["net_prev"] is not None]
        assert len(got) >= 8, fam
        assert np.allclose(got, [x["net"] for x in ref[:len(got)]], atol=1e-9), fam
        assert not any("skipped" in n for n in st[fam]["notes"]), fam


def test_watch_s6_s8_switches(cfg):
    assert wr.apply_settings(cfg, '{"WATCH_S6": false, "WATCH_S8": false}') == []
    assert cfg.watch_s6 is False and cfg.watch_s8 is False


def test_summary_shows_portfolio_watch_lines():
    now = time.time()
    st = sample_state(now)
    st["s6"] = dict(pw.port_state(now - 86400), w={"BTC": 0.1, "ETH": 0.1, "SOL": 0.1, "XRP": 0.1, "DOGE": -0.2,
                                                    "ADA": -0.2}, equity=1.021,
                    events=[{"t": int((now - 3600) * 1000), "bought": ["BTC"], "sold": ["LINK"], "net_prev": 0.3}])
    st["s8"] = dict(pw.port_state(now - 86400), w={"SOL": 0.21, "BNB": 0.21, "ETH": 0.21}, scale=0.63, equity=0.99,
                    events=[{"t": int((now - 9 * 86400) * 1000), "bought": ["SOL"], "sold": [], "net_prev": None}])
    text = "\n".join(pw.summary_lines(st, now))
    assert "🔭 Now watching 4 near-miss strategies." in text
    assert ("Funding factor (top 50, long low / short high funding, futures): long 4 (BTC, ETH, SOL …) · "
            "short 2 (ADA, DOGE) · 24 h: rebalanced (bought 1, sold 1) · since start: +2.10% after fees · day 2") in text
    assert ("Vol-managed momentum (top 50, spot): 3 coins at 63% exposure (BNB, ETH, SOL) · 24 h: no rebalance · "
            "since start: -1.00% after fees") in text


def test_notes_counted_past_cap():
    ps = pw.port_state(0)
    for i in range(pw.KEEP + 5):
        pw.add_note(ps, f"n{i}")
    assert len(ps["notes"]) == pw.KEEP and ps["n_notes"] == pw.KEEP + 5 and ps["notes"][-1] == f"n{pw.KEEP + 4}"


def test_s6_s8_held_coin_leaving_top_is_sold_and_repickable_after_reentry():
    names, rows, fund = port_inputs()
    n = len(rows["BTC"])

    def run(fam, out_coin=None, out_from=None, out_to=None):
        st, hist = pw.port_state(rows["BTC"][0, 0] / 1000), []
        for m in range(215, n):
            elig = set(names)
            if out_coin and out_from <= rows["BTC"][m - 1, 0] <= out_to:
                elig.discard(out_coin)
            before = st["d_t"]
            pw.port_update(st, fam, {b: r[:m] for b, r in rows.items()}, {b: float(r[m, 1]) for b, r in rows.items()},
                           fund if fam == "s6" else {}, 0, elig)
            if st["d_t"] != before:
                hist.append((st["d_t"], dict(st["w"]), st["events"][-1], st["equity"]))
        return hist
    checked = 0
    for fam in ("s6", "s8"):
        base = run(fam)
        cand = [(k, b) for k in range(2, len(base) - 6) for b in base[k][1] if b != "BTC"
                and any(b in base[j][1] for j in range(k + 4, len(base)))]
        if not cand:
            continue
        k, coin = cand[0]
        t_out, t_in = base[k + 1][0], base[k + 3][0]  # out of the top 50 for 3 rebalances
        hist = run(fam, coin, t_out, t_in)
        assert [h[0] for h in hist] == [h[0] for h in base]
        assert coin in hist[k][1]                                       # held before it dropped out
        assert coin not in hist[k + 1][1] and coin in hist[k + 1][2]["sold"]  # sold at the next rebalance
        assert all(coin not in hist[j][1] for j in range(k + 1, k + 4))      # not eligible while out
        assert abs(hist[k + 1][2]["net_prev"] - base[k + 1][2]["net_prev"]) < 1e-12  # priced normally
        for j in range(k + 4, len(base)):                                # back in: same decisions as research
            assert hist[j][1] == base[j][1]
        assert any(coin in hist[j][1] for j in range(k + 4, len(base)))  # picked again after re-entry
        checked += 1
    assert checked >= 1
