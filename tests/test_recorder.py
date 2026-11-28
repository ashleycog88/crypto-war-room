"""Phase 3b edge-data recorder (JRN-100..109) incl. 3c encryption (JRN-124)."""
import glob
import gzip
import json
import os
import re
import subprocess
import sys
import time

import pytest

import datalock
import recorder as rc
import war_room_bot as wr
from test_state_git import git

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PASS = "test-only recorder passphrase"
ADDR = "0x1111111111111111111111111111111111111111"


class Resp:
    def __init__(self, data=None, status=200, content=None):
        self.status_code, self._d = status, data
        self.content = content if content is not None else json.dumps(data).encode()

    def json(self):
        return self._d


def rss(n=3, tag="a"):
    items = "".join(f"<item><title>Headline {tag}{i} about BTC</title><link>https://news.example/{tag}{i}</link>"
                    f"<pubDate>Sat, 28 Nov 2026 01:0{i}:00 GMT</pubDate></item>" for i in range(n))
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>{items}</channel></rss>'.encode()


class FakeSession:
    def __init__(self, fail=()):
        self.headers, self.fail, self.calls = {}, set(fail), []
        t = int(time.time() * 1000)
        self.t = t
        self.routes = {
            "ticker/24hr": [{"symbol": "BTCUSDT", "quoteVolume": "9e9"}, {"symbol": "ETHUSDT", "quoteVolume": "5e9"},
                            {"symbol": "DOGEUSDT", "quoteVolume": "1e9"}],
            "leaderboard": {"leaderboardRows": leaderboard_rows()},
            "support/announcements": {"data": [{"details": [{"url": "u1", "title": "OKX to list Foo (FOO)",
                                                             "pTime": str(t - 1000)}]}]},
            "cms/article": {"data": {"catalogs": [{"articles": [{"code": "c1", "title": "Binance Will List Bar (BAR)",
                                                                  "releaseDate": t - 1000}]}]}},
            "upbit": Resp(status=403),
            "kucoin": {"data": {"items": [{"annId": 7, "annTitle": "Baz (BAZ) Listed on KuCoin", "cTime": t - 1000}]}},
            "public/instruments": {"data": [{"uly": "BTC-USDT", "settleCcy": "USDT", "ctVal": "0.01"}]},
            "liquidation-orders": {"data": [{"details": [{"ts": str(t - 2000), "sz": "10", "bkPx": "60000",
                                                          "posSide": "long"}]}]},
            "long-short": {"data": [[str(t - 60000), "1.5"]]},
            "alternative.me": {"data": [{"value": "40", "value_classification": "Fear", "timestamp": str(t // 1000 - 99)}]},
            "coingecko": {"data": {"market_cap_percentage": {"btc": 55.0}, "total_market_cap": {"usd": 3e12},
                                   "updated_at": t // 1000 - 10}},
            "llama": {"peggedAssets": [{"circulating": {"peggedUSD": 1e11}}]},
        }

    def get(self, url, timeout=15, params=None, **kw):
        self.calls.append(url)
        for k in self.fail:
            if k in url:
                return Resp(status=503)
        if url in rc.FEEDS.values():
            name = [k for k, v in rc.FEEDS.items() if v == url][0]
            return Resp(content=rss(3, name))
        for k, v in self.routes.items():
            if k in url:
                return v if isinstance(v, Resp) else Resp(v)
        return Resp(status=404)

    def post(self, url, json=None, timeout=15):
        t = self.t
        typ = json["type"]
        if "hl" in self.fail:
            return Resp(status=503)
        if typ == "metaAndAssetCtxs":
            return Resp([{"universe": [{"name": "BTC"}, {"name": "OLD", "isDelisted": True}]},
                         [{"funding": "0.0001", "openInterest": "100", "markPx": "60000", "oraclePx": "60010",
                           "premium": "0.0002", "dayNtlVlm": "1e9"}, {}]])
        if typ == "clearinghouseState":
            return Resp({"assetPositions": [{"position": {"coin": "BTC", "szi": "1.0", "entryPx": "59000",
                                                          "leverage": {"value": 5}, "unrealizedPnl": "1000",
                                                          "positionValue": "60000"}}],
                         "marginSummary": {"accountValue": "200000"}, "time": t - 1000})
        if typ == "userFillsByTime":
            return Resp([{"coin": "BTC", "px": "60000", "sz": "0.1", "side": "B", "time": t - 5000, "dir": "Open Long",
                          "closedPnl": "0", "fee": "1", "oid": 1, "crossed": True}])
        return Resp(status=400)


def row(addr, av, mpnl, aroi, mvlm=1e6, name=None):
    return {"ethAddress": addr, "accountValue": str(av), "displayName": name,
            "windowPerformances": [["day", {"pnl": "1", "roi": "0.01", "vlm": "1"}],
                                   ["month", {"pnl": str(mpnl), "roi": "0.1", "vlm": str(mvlm)}],
                                   ["allTime", {"pnl": "1", "roi": str(aroi), "vlm": "1"}]]}


def leaderboard_rows():
    return [row(ADDR, 500_000, 90_000, 1.2), row("0x2222", 50_000, 99_999, 3.0),
            row("0xdfc24b077bc1425ad1dea75bcb6f8158e10df303", 9e9, 9e6, 1.0), row("0x3333", 2e6, 5e5, 0.5, mvlm=1e9),
            row("0x4444", 300_000, 50_000, 0.2, name="Big Vault"), row("0x5555", 300_000, -1, 0.5),
            row("0x6666", 300_000, 120_000, 0.8)]


@pytest.fixture
def mkrec(tmp_path, monkeypatch):
    monkeypatch.setattr(rc.time, "sleep", lambda s: None)

    def make(key=None, fail=(), **over):
        cfg = rc.RecConfig(out_dir=str(tmp_path / "rec"), push=False, dry_run=True, data_key=key or "", llm_key="",
                           whale_stream=False, n_wallets=3, **over)
        r = rc.Recorder(cfg, session=FakeSession(fail))
        r.start()
        return r
    return make


def records(root, key=None):
    out = {}
    for f in glob.glob(os.path.join(root, "data", "*", "*", "*", "*.jsonl.gz*")):
        for ln in datalock.read_plain(f, key).decode().splitlines():
            r = json.loads(ln)
            out.setdefault(r["src"], []).append(r)
    return out


def test_jrn100_dry_run_writes_every_source(mkrec, capsys, monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    r = mkrec()
    r.whales.on_trade({"s": "BTCUSDT", "p": "60000", "q": "3", "m": False, "T": rc.now_ms() - 400_000})
    r.tick()
    r.checkpoint()
    recs = records(r.cfg.out_dir)
    for src in ("hl_market", "hl_leaderboard", "hl_positions", "hl_fills", "news", "listings", "whales", "liquidations",
                "longshort", "context"):
        assert recs.get(src), src
    assert [x["coin"] for x in recs["hl_market"]] == ["BTC"]
    files = glob.glob(os.path.join(r.cfg.out_dir, "data", "*", "*", "*", "*"))
    assert all(re.search(r"/\d{2}_[a-z_]+-\d+\.jsonl\.gz$", f) for f in files)
    allr = [x for v in recs.values() for x in v]
    assert all("obs" in x and "src" in x for x in allr)
    assert sum(1 for x in allr if "event_ts" in x and x["obs"] < x["event_ts"]) == 0  # JRN-109 no look-ahead
    st = json.load(open(os.path.join(r.cfg.out_dir, "data", "status.json")))
    assert st["hours_24"] >= 1 and st["sources"]["news"]["n24"] == 18 and st["sources"]["hl_wallets"]["count"] == 2
    assert st["sources"]["news_ai"].get("disabled") is True
    out = capsys.readouterr().out
    for bad in (ADDR, "Headline", "BTC", "60000", "Foo"):
        assert bad not in out, bad


def test_jrn101_wallet_selection():
    w = rc.select_wallets(leaderboard_rows(), n=100)
    assert [x["addr"] for x in w] == ["0x6666", ADDR]  # by 30-day PnL; small, HLP, MM, vault, losing excluded
    assert w[0]["rank"] == 1 and {"month_pnl", "alltime_roi", "account_value"} <= set(w[0])
    assert len(rc.select_wallets([row(f"0x{i:040x}", 2e5, 1000 + i, 1) for i in range(150)])) == 100
    assert "300x" in rc.WALLET_RULE and "HLP" in rc.WALLET_RULE


def test_jrn102_news_ai_batches_and_unrated_backlog(mkrec, monkeypatch):
    r = mkrec()
    r.cfg.llm_key, r.cfg.ai_cap = "fake", 1000
    r.cursor.d["pending_ai"] = [{"link": f"l{i}", "title": f"BTC news {i}"} for i in range(25)]
    sizes, mode = [], {"fail": True}

    def fake(cfg, model, prompt):
        n = len(re.findall(r"^\d+\. ", prompt, re.M))
        sizes.append((model, n))
        if mode["fail"]:
            raise TimeoutError("x")
        return json.dumps([{"i": i, "coins": ["BTC"], "sentiment": 1, "category": "macro", "urgency": "high"}
                           for i in range(n)])
    monkeypatch.setattr(rc, "_ai_call", fake)
    with pytest.raises(RuntimeError, match="25 unrated waiting"):
        r.news_ai()
    assert len(r.cursor.d["pending_ai"]) == 25 and [m for m, _ in sizes] == r.cfg.ai_models
    mode["fail"] = False
    sizes.clear()
    r.news_ai()
    assert max(n for _, n in sizes) <= 10 and sum(n for _, n in sizes) == 25 and not r.cursor.d["pending_ai"]
    rated = r.store.buf["news_ai"]
    assert len(rated) == 25 and rated[0]["model"] == "gemini-3.1-flash-lite"
    assert {"coins", "sentiment", "category", "urgency"} <= set(rated[0])
    r.cursor.d["pending_ai"] = [{"link": "x", "title": "y"}] * 30
    r.cfg.ai_cap = r.cursor.d["ai"]["n"] + 1
    sizes.clear()
    r.news_ai()
    assert len(sizes) == 1 and len(r.cursor.d["pending_ai"]) == 20  # cap reached → rest stays unrated for later
    with pytest.raises(ValueError):
        rc.parse_ai_ratings("I think it's bullish", 3)


def test_jrn103_whale_thresholds_and_sides():
    a = rc.WhaleAgg(["BTCUSDT"], 100_000, 25_000)
    T = 1_800_000_000_000
    for usd, m in ((20_000, False), (60_000, False), (150_000, False), (120_000, True)):
        a.on_trade({"s": "BTCUSDT", "p": "1", "q": str(usd), "m": m, "T": T})
    a.on_trade({"s": "DOGEUSDT", "p": "1", "q": "30000", "m": False, "T": T})
    a.on_trade({"s": "DOGEUSDT", "p": "1", "q": "20000", "m": True, "T": T})
    done = a.pop_done(T + 300_000)
    b = done[("BTCUSDT", T // 300_000 * 300_000)]
    assert (b["buy_n"], b["buy_usd"], b["sell_n"], b["sell_usd"]) == (1, 150_000, 1, 120_000)
    d = done[("DOGEUSDT", T // 300_000 * 300_000)]
    assert (d["buy_n"], d["sell_n"]) == (1, 0) and a.msgs == 6 and a.pop_done(T + 300_000) == {}


def test_jrn104_failing_sources_marked_others_recorded(mkrec):
    r = mkrec(fail=("cms/article",))
    r.tick()
    r.checkpoint()
    st = json.load(open(os.path.join(r.cfg.out_dir, "data", "status.json")))["sources"]
    assert "HTTP 403" in st["listings_upbit"]["err"] and "HTTP 503" in st["listings_binance"]["err"]
    assert st["listings_upbit"]["optional"] and st["listings"]["err"] == "" and st["listings"]["n24"] == 2
    assert records(r.cfg.out_dir)["news"]
    assert wr.recorder_stalled({"sources": st}, time.time()) == []


def test_jrn124_encrypted_files_and_decrypt_helper(mkrec):
    r = mkrec(key=PASS)
    r.tick()
    r.checkpoint(final=True)
    files = glob.glob(os.path.join(r.cfg.out_dir, "data", "*", "*", "*", "*"))
    assert files and all(f.endswith(".jsonl.gz.enc") for f in files)
    for f in files:
        assert b"BTC" not in open(f, "rb").read()
    assert os.path.exists(os.path.join(r.cfg.out_dir, "data", "cursor.json.enc"))
    st = open(os.path.join(r.cfg.out_dir, "data", "status.json")).read()
    assert ADDR not in st and "Headline" not in st and json.loads(st)["encrypted"] is True
    key = datalock.derive_key(PASS, datalock.read_salt())
    f = [x for x in files if "_news-" in x][0]
    p = subprocess.run([sys.executable, os.path.join(ROOT, "datalock.py"), "decrypt", f], capture_output=True,
                       env=dict(os.environ, DATA_KEY=PASS))
    assert p.returncode == 0 and p.stdout == datalock.read_plain(f, key)
    assert json.loads(p.stdout.decode().splitlines()[0])["src"] == "news"
    blob = bytearray(open(f, "rb").read())
    blob[20] ^= 1
    open(f, "wb").write(bytes(blob))
    p = subprocess.run([sys.executable, os.path.join(ROOT, "datalock.py"), "decrypt", f], capture_output=True,
                       text=True, env=dict(os.environ, DATA_KEY=PASS))
    assert p.returncode == 2 and "tampered" in p.stderr
    r2 = mkrec(key="wrong")
    assert r2.cursor.locked


def test_jrn105_data_branch_pushes_only_new_files(tmp_path, monkeypatch):
    monkeypatch.setattr(rc.time, "sleep", lambda s: None)
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "--bare", "-b", "main", str(remote))
    seed = tmp_path / "seed"
    git(tmp_path, "clone", str(remote), str(seed))
    (seed / "README.md").write_text("main\n")
    git(seed, "add", ".")
    git(seed, "commit", "-m", "seed")
    git(seed, "push", "origin", "HEAD:main")
    main_head = git(seed, "rev-parse", "HEAD").strip()
    repo = tmp_path / "repo"
    git(tmp_path, "clone", str(remote), str(repo))
    wt = tmp_path / "wt"
    cfg = rc.RecConfig(repo_dir=str(repo), out_dir=str(wt), push=True, dry_run=False, data_key="", llm_key="",
                       whale_stream=False, n_wallets=2)
    r = rc.Recorder(cfg, session=FakeSession())
    r.start()
    r.tick()
    r.checkpoint()
    other = tmp_path / "other"  # a concurrent commit on the data branch
    git(tmp_path, "clone", "-b", "data", str(remote), str(other))
    (other / "data" / "note.txt").write_text("x")
    git(other, "add", ".")
    git(other, "commit", "-m", "concurrent")
    git(other, "push", "origin", "HEAD:data")
    r.last_run.clear()
    r.tick()
    r.checkpoint()
    r.finish()
    check = tmp_path / "check"
    git(tmp_path, "clone", "-b", "data", str(remote), str(check))
    assert (check / "data" / "note.txt").exists() and not (check / "README.md").exists()
    modified = git(check, "log", "--diff-filter=MD", "--name-only", "--format=")
    assert {m for m in modified.split() if m} <= {"data/status.json", "data/cursor.json"}
    assert len(glob.glob(str(check / "data" / "*" / "*" / "*" / "*_hl_market-*"))) >= 2
    git(seed, "fetch", "origin", "main")
    assert git(seed, "rev-parse", "origin/main").strip() == main_head


def test_jrn106_recorder_workflow():
    y = open(os.path.join(ROOT, ".github/workflows/recorder.yml")).read()
    import yaml
    d = yaml.safe_load(y)
    assert d["permissions"] == {"contents": "write", "actions": "write"}
    assert d["concurrency"]["group"] == "war-room-recorder" and "cron: '23 * * * *'" in y
    assert re.search(r"uses: actions/checkout@[0-9a-f]{40}", y) and re.search(r"setup-python@[0-9a-f]{40}", y)
    steps = d["jobs"]["record"]["steps"]
    assert steps[-1]["if"] == "always()" and "--push-only" in steps[-1]["run"]
    assert all("timeout-minutes" in s for s in steps[2:]) and d["jobs"]["record"]["timeout-minutes"] == 75
    run = steps[3]["env"]
    assert run["CHAIN_AT_MINUTE"] == "50" and run["GITHUB_TOKEN"] == "${{ secrets.GITHUB_TOKEN }}"


def test_recorder_chains_once_when_healthy(mkrec, monkeypatch):
    calls = []
    monkeypatch.setattr(wr, "chain_dispatch", lambda c, wf: calls.append(wf) or "queued ✅")
    r = mkrec()
    r.cfg.dry_run, r.cfg.gh_token, r.cfg.gh_repo = False, "tok", "me/repo"
    r.t0 -= 51 * 60
    r.maybe_chain(time.time())
    assert calls == []  # no healthy source yet
    r.tick()
    r.tick()
    assert calls == ["recorder.yml"]


def test_jrn107_bot_recorder_lines(cfg, tg, monkeypatch):
    now = time.time()
    ok = {k: {"last_ok": now - 60, "n24": n, "every_s": 300} for k, n in (("news", 412), ("listings", 3), ("whales", 50))}
    ok["hl_wallets"] = {"last_ok": now - 60, "n24": 9000, "count": 96, "every_s": 900}
    ok["listings_upbit"] = {"last_ok": 0, "optional": True, "err": "HTTP 403"}
    status = {"hours_24": 24, "sources": ok}
    assert wr.recorder_line(status, now) == ("📼 Recorder: ✅ 24/24 hours recorded "
                                             "(news 412 · wallets 96 · whales ✅ · listings 3)")
    stalled = json.loads(json.dumps(status))
    stalled["sources"]["news"]["last_ok"] = now - 4 * 3600
    assert wr.recorder_line(stalled, now).startswith("📼 Recorder: ❌ news failing")
    from fakes import FakeSpot, make_bot
    cfg.recorder_status_url = "https://raw.example/status.json"
    monkeypatch.setattr(wr, "_http_get", lambda url, timeout, params=None: Resp(stalled))
    spot = FakeSpot(pairs=["BTC/USDT"])
    bot = make_bot(cfg, spot)
    bot.cycle(now=spot.now)
    assert "⚠️ Recorder: news stalled" in tg.texts("times this hour")[0]
    assert wr.recorder_line(None, now) == "📼 Recorder: ❓ status not available"


def test_gzip_payload_is_jsonl(mkrec):
    r = mkrec()
    r.hl_market()
    f = r.store.flush()[0]
    lines = gzip.decompress(open(os.path.join(r.cfg.out_dir, f), "rb").read()).decode().splitlines()
    assert json.loads(lines[0])["coin"] == "BTC"
