"""Phase 4 Step C research engine checks (C1 wallet copying, C2 events) on synthetic inputs."""
import numpy as np

import step_c as sc


def fill(t, side, sz, start, coin="SOL", pnl=0.0, px=100.0):
    return {"coin": coin, "px": str(px), "sz": str(sz), "side": side, "time": t, "startPosition": str(start),
            "dir": "", "closedPnl": str(pnl), "fee": "0.1", "crossed": True}


def test_wallet_openings_exits_and_flips():
    doc = {"fills": [fill(1000, "B", 2, 0), fill(2000, "B", 1, 2), fill(3000, "A", 1, 3, pnl=5),
                     fill(4000, "A", 5, 2, pnl=1), fill(5000, "B", 3, -3, pnl=2)]}
    pnl, opens = sc.wallet_events(doc)
    assert [(o["side"], o["open_t"], o["exit_t"]) for o in opens] == [(1, 1000, 3000), (-1, 4000, 5000)]
    assert pnl.shape == (5, 4) and pnl[:, 3].sum() == 3  # three closing fills
    assert abs(pnl[:, 1].sum() - (8 - 0.5)) < 1e-9  # realised PnL minus fees


def test_hl_symbol_mapping():
    assert sc.hl_symbol("kPEPE") == "1000PEPEUSDT" and sc.hl_symbol("BTC") == "BTCUSDT"
    assert sc.hl_symbol("@107") is None and sc.hl_symbol("xyz:TSLA") is None and sc.hl_symbol("PURR/USDC") is None


def test_classify_announcements():
    c = lambda t: sc.classify({"title": t})
    assert c("Binance Will List Hyperliquid (HYPE) with Seed Tag Applied") == [("spot_listing", "HYPE")]
    assert c("Binance Futures Will Launch USDⓈ-Margined CTUSDT Perpetual Contract (2026-10-01)") == [("perp_listing", "CT")]
    assert c("Binance Will Delist EPIC, QUICK on 2026-10-28") == [("delisting", "EPIC"), ("delisting", "QUICK")]
    assert c("Binance Futures Will Delist PTBUSDT and MITOUSDT USDⓈ-M Perpetual Contracts") == \
        [("delisting", "PTB"), ("delisting", "MITO")]
    assert c("Binance Will Add 5 bStocks Tokenized Securities as Collateral Asset") is None
    assert c("KuCoin Futures Will List Multiple Stock Index Perpetual Contracts (2026-11-25)") is None
    assert c("OKX to list CARDS/USDT (Collector Crypt) for spot trading") == [("spot_listing", "CARDS")]


def test_costs_and_verdict_rules():
    assert abs(sc.net(1.0, 0.0) - (1.0 - 2 * sc.FEE - 2 * sc.SLIP)) < 1e-12
    assert sc.net(1.0, 0.0, stress=True) < sc.net(1.0, 0.0)
    good = {"n": 60, "avg": 0.5, "pf": 1.5, "total": 30}
    half = {"n": 30, "total": 10}
    assert sc.verdict(good, half, half, 99, {"n": 60, "avg": 0.2}, 50) == ("PASSED", [])
    v, f = sc.verdict(dict(good, n=20), half, half, 99, {"n": 20, "avg": 0.2}, 30)
    assert v == "REJECTED" and any("too few events" in x for x in f)
    assert "does not beat 95% of random runs" in sc.verdict(good, half, half, 94.9, {"n": 60, "avg": 0.2}, 50)[1]


def test_open_at_uses_first_minute_at_or_after_time(monkeypatch):
    day = 1_780_000_000_000 // sc.DAY * sc.DAY
    rows = np.array([[day + i * sc.M, 100 + i, 101 + i, 99 + i, 100.5 + i] for i in range(1440)], float)
    monkeypatch.setattr(sc.PX, "day", lambda sym, d: rows if d == day else None)
    assert sc.PX.open_at("X", day + 10 * sc.M) == 110
    assert sc.PX.open_at("X", day + 10 * sc.M + 1) == 111  # never a candle that started before the signal


def test_classify_drops_tradfi_and_margin_delistings():
    c = lambda t: sc.classify({"title": t})
    anns = [{"title": "Binance Futures Will Launch USDⓈ-Margined KORUUSDT and SOXSUSDT TradFi Perpetual Contracts"},
            {"title": "KuCoin Futures Will Delist the SOXSUSDT and KORUUSDT Perpetual Contracts (2026-07-01)"}]
    assert {"KORU", "SOXS"} <= sc.tradfi_tickers(anns)
    assert c("KuCoin Futures Will Delist the ANTHROPICUSDT Pre-IPO Stock Index Perpetual Contract") is None
    assert c("Delist And Relist the SNDKUSDT Stock Index Perpetual Contract") is None
    assert c("Binance Margin And Loan Will Delist BTTC & POWR on 2026-08-14") is None
    assert c("Binance Will Extend the Monitoring Tag to Include ACT, BLUR, PIVX & QKC on 2026-06-18") == \
        [("monitoring", x) for x in ("ACT", "BLUR", "PIVX", "QKC")]


def test_hours_loads_every_calendar_month(monkeypatch):
    seen = []
    monkeypatch.setattr(sc.PX, "month", lambda sym, m: seen.append(m) or None)
    sc.PX.hours("X", sc.MS("2026-06-01"), sc.MS("2026-07-01"))
    assert sc.MS("2026-07-01") in seen and sc.MS("2026-06-01") in seen
    assert sc.months_between(sc.MS("2026-01-31"), sc.MS("2026-03-01")) == [sc.MS("2026-01-01"), sc.MS("2026-02-01"),
                                                                           sc.MS("2026-03-01")]


def test_flat_dead_perp_candles_are_not_live():
    a = np.array([[0, 1, 1, 1, 1, 0], [1, 1, 1.1, 0.9, 1, 5], [2, 1, 1, 1, 1, 3]], float)
    assert sc.live(a).tolist() == [False, True, False]


def test_monitoring_tag_only_counts_added_coins():
    c = lambda t: sc.classify({"title": t})
    assert c("Binance Will Extend the Monitoring Tag to Include BANK, INIT and Remove the Monitoring Tag for HYPE") == \
        [("monitoring", "BANK"), ("monitoring", "INIT")]
    assert c("Binance Will Remove the Monitoring Tag for ZEC on 2026-07-01") is None
    assert "STX" not in sc.tradfi_tickers([{"title": "Binance Will Support the Seagate (STX) Cash Dividend"}])


def test_random_rule_fills_stops_at_stop_price_not_next_open(monkeypatch):
    n = 400
    t0 = sc.MS("2026-06-01")
    a = np.column_stack([t0 + np.arange(n) * sc.H, np.full(n, 100.0), np.full(n, 101.0), np.full(n, 99.0),
                         np.full(n, 100.0), np.ones(n)])
    a[200:, 1:5] = [[100, 150, 99, 120]] * (n - 200)  # spike: a short with a 2×ATR stop (ATR 2) stops at 104
    monkeypatch.setattr(sc.PX, "hours", lambda sym, lo, hi: a)
    tr = [{"sym": "X", "side": -1, "entry_t": t0 + 190 * sc.H, "exit_t": t0 + 210 * sc.H, "gross": -(104 / 100 - 1) * 100 + 1e-9, "fund": 0.0,
           "pub_t": 1}]
    rng = np.random.default_rng(1)
    monkeypatch.setattr(sc, "N_RANDOM", 50)
    pct = sc.random_rule(tr, t0 + 180 * sc.H, t0 + 199 * sc.H, rng, hold=24, atr_stop=2)
    assert pct == 100.0  # every random start stops out at exactly -4% (the stop fill), never at the better next open
