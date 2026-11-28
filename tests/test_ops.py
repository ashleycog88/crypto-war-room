"""Workflows, heartbeat, derivatives, watchdog, diagnostics, healthcheck, secrets, loop (JRN-32..35, 38..46)."""
import http.server
import json
import os
import re
import subprocess
import sys
import threading
import time

import pytest

import war_room_bot as wr
from fakes import FakeSpot, TGRecorder, failing_source, install_scores, make_bot

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAKE_TOKEN = "123456789:FAKEtokenFAKEtokenFAKEtoken123"
FAKE_KEY = "FAKEKEY"
FAKE_CHAT = "-1009876543210"


def wf(name):
    with open(os.path.join(ROOT, ".github", "workflows", name)) as f:
        return f.read()


def test_jrn35_main_workflow():
    y = wf("war_room.yml")
    cron = re.search(r"cron:\s*'([^']+)'", y).group(1).split()
    assert cron[0] != "0" and cron[0].isdigit() and cron[1:] == ["*", "*", "*", "*"]
    assert "workflow_dispatch:" in y and "pull_request" not in y
    assert re.search(r"concurrency:\s*\n\s*group: \S+\s*\n\s*cancel-in-progress: false", y)
    perms = re.search(r"permissions:\s*\n((?:\s+\S.*\n)+?)\n", y).group(1)
    assert [p.strip() for p in perms.strip().splitlines()] == ["contents: write", "actions: write"]
    assert "timeout-minutes: 75" in y and "timeout-minutes: 63" in y
    assert "::add-mask::" in y
    assert re.search(r"if: always\(\)\s*\n\s*timeout-minutes: \d+\s*\n\s*run: python war_room_bot.py --commit-state", y)
    assert "RUN_MINUTES: '58'" in y and "SCAN_INTERVAL: '60'" in y and "cache: 'pip'" in y
    for f in ("war_room.yml", "watchdog.yml", "diagnostics.yml"):
        for use in re.findall(r"uses:\s*(\S+)(.*)", wf(f)):
            assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", use[0]), use
            assert re.search(r"# v\d", use[1])
    assert wr.git_sync.__doc__ and "reset --hard" in wr.git_sync.__doc__


def test_jrn45_watchdog_and_diag_workflows():
    w = wf("watchdog.yml")
    assert "contents: read" in w and "contents: write" not in w and "--watchdog" in w and "pull_request" not in w
    assert "*/2" in re.search(r"cron:\s*'([^']+)'", w).group(1)
    d = wf("diagnostics.yml")
    assert "schedule" not in d and "workflow_dispatch" in d and "contents: read" in d and "--diagnose" in d
    assert "pull_request" not in d


def test_jrn38_repo_tree():
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    for bad in ("frontend/", "backend/", "web/", "app/"):
        assert not [f for f in tracked if f.startswith(bad)], bad
    if os.path.isdir(os.path.join(ROOT, ".git")):
        r = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", "state.json"], cwd=ROOT)
        assert r.returncode == 0, "state.json was modified"
    gi = open(os.path.join(ROOT, ".gitignore")).read()
    for p in (".env", "backend/", "frontend/", ".emergent/", "__pycache__/", ".pytest_cache/"):
        assert p in gi


def deriv_ok(s, coins):
    return {"BTC": {"funding8h": 0.0001, "oi": 103.0}}


def test_jrn39_second_deriv_source_used_and_shown(cfg, tg, monkeypatch):
    spot = FakeSpot(pairs=["BTC/USDT"])
    install_scores(monkeypatch, spot, {"BTC/USDT": (85, 0)})
    bot = make_bot(cfg, spot, deriv_sources=[("FIRST", failing_source), ("SECOND", deriv_ok)])
    now = spot.now
    bot.state["oi_snap"]["BTC"] = [[int(now - 3600), 100.0, "SECOND"]]
    bot.cycle(now=now)
    a = tg.texts("A-GRADE ·")[0]
    assert "OI +3.0% 1h" in a and "Funding +0.0100%/8h" in a and "BTC " in a and "via Second" in a
    assert "85/100" in a and bot.deriv.primary == "SECOND"
    assert bot.state["active_trades"][0]["score"] == 85
    hb = tg.texts("times this hour")[0]
    assert "✅ Derivatives: Second" in hb


def test_deriv_score_switch_changes_score():
    import pandas as pd
    df5 = pd.DataFrame([{"ts": 0, "close": 100, "ema9": 101, "ema21": 100, "ema50": 99, "vwap": 99, "rsi": 60,
                         "vol_r": 2.0, "atr": 1.0}])
    df1 = pd.DataFrame([{"ts": 0, "close": 99, "ema21": 98, "ema50": 97, "rsi": 55},
                        {"ts": 1, "close": 100, "ema21": 99, "ema50": 98, "rsi": 55}])
    off = wr.evaluate_pair(df5, df1, 0.0, 100, -0.0001, 3.0, "up", use_deriv=False)
    on = wr.evaluate_pair(df5, df1, 0.0, 100, -0.0001, 3.0, "up", use_deriv=True)
    assert off["score"] == 85 and on["score"] == 95 and on["deriv_adj"] == 10
    capped = wr.evaluate_pair(df5, df1, 0.5, 100, -0.0001, 3.0, "up", use_deriv=True)
    assert capped["score"] == 100


def test_jrn40_all_deriv_fail(cfg, tg, monkeypatch):
    spot = FakeSpot(pairs=["BTC/USDT"])
    install_scores(monkeypatch, spot, {"BTC/USDT": (85, 0)})
    bot = make_bot(cfg, spot, deriv_sources=[("A", failing_source), ("B", failing_source)])
    bot.cycle(now=spot.now)
    a = tg.texts("A-GRADE ·")[0]
    assert "Funding n/a" in a and "OI n/a" in a
    assert "Derivatives: none" in tg.texts("times this hour")[0]


def test_hyperliquid_symbol_mapping():
    class S:
        def post(self, *a, **k):
            class R:
                def json(self):
                    return [{"universe": [{"name": "BTC"}, {"name": "kPEPE"}, {"name": "OLD", "isDelisted": True}]},
                            [{"funding": "0.0000125", "openInterest": "10"}, {"funding": "-0.00001", "openInterest": "5"},
                             {"funding": "0", "openInterest": "1"}]]
            return R()
    d = wr.deriv_hyperliquid(S(), [])
    assert set(d) == {"BTC", "PEPE"} and d["BTC"]["funding8h"] == pytest.approx(0.0001)


def test_oi_change():
    snaps = [[1000, 100.0, "X"], [1300, 90.0, "Y"]]
    assert wr.oi_change(snaps, 1000 + 3600, 103.0, "X") == 3.0
    assert wr.oi_change(snaps, 1000 + 3600, 103.0, "Z") is None
    assert wr.oi_change(snaps, 1000 + 9000, 103.0, "X") is None


def test_jrn32_jrn42_heartbeat_contents(cfg, tg, monkeypatch):
    spot = FakeSpot(name="KUCOIN", pairs=["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT"])
    install_scores(monkeypatch, spot, {"BTC/USDT": (60, 0), "ETH/USDT": (0, 55), "SOL/USDT": (40, 0),
                                       "XRP/USDT": (30, 0)})
    bot = make_bot(cfg, spot)
    bot.coins = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "BNB/USDT"]
    bot.state["last_heartbeat"] = spot.now
    bot.cycle(now=spot.now)
    bot.cycle(now=spot.now + 60)
    bot.state["last_heartbeat"] = 0
    bot.cycle(now=spot.now + 120)
    hb = tg.texts("times this hour")[0]
    assert "BOT ALIVE" in hb and "Checked 5 coins 3 times this hour" in hb
    assert "Almost alerts (need 65): BTC buy 60, ETH short 55, SOL buy 40" in hb and "XRP" not in hb
    assert "⚠️ Data: Kucoin (Binance down)" in hb and "❌ Derivatives: none" in hb and "Open trades (0): none" in hb
    assert "A-grade: 0 wins · 0 losses (no trades yet)" in hb and "❌ AI: no key" in hb
    assert "Alerts sent: 0 (AI blocked 0)" in hb
    assert "⚠️ Not available on Kucoin: BNB" in hb
    assert len(hb) < 4096


def test_v10_live_once(cfg, tg, monkeypatch):
    spot = FakeSpot(pairs=["BTC/USDT"])
    bot = make_bot(cfg, spot)
    bot.cycle(now=spot.now)
    bot.cycle(now=spot.now + 60)
    assert len(tg.texts("v10 LIVE")) == 1 and bot.state["v10_live_sent"]


def test_jrn44_watchdog(cfg, tg, tmp_path):
    now = time.time()
    msg = wr.watchdog_message({"last_cycle_time": int(now - 3 * 3600)}, now)
    assert "BOT DOWN" in msg and "3h 0m" in msg
    assert wr.watchdog_message({"last_cycle_time": int(now - 1800)}, now) is None
    with open(cfg.state_file, "w") as f:
        json.dump({"last_cycle_time": int(now - 3 * 3600)}, f)
    wr.run_watchdog(cfg)
    assert len(tg.texts("BOT DOWN")) == 1
    with open(cfg.state_file, "w") as f:
        json.dump({"last_cycle_time": int(now - 1800)}, f)
    wr.run_watchdog(cfg)
    assert len(tg.payloads) == 1


class Ping(http.server.BaseHTTPRequestHandler):
    hits = []

    def do_GET(self):
        Ping.hits.append(self.path)
        self.send_response(200)
        self.end_headers()

    def log_message(self, *a):
        pass


def test_jrn46_healthcheck(cfg, tg):
    srv = http.server.HTTPServer(("127.0.0.1", 0), Ping)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    Ping.hits.clear()
    cfg.healthcheck_url = f"http://127.0.0.1:{srv.server_address[1]}/ping-uuid"
    spot = FakeSpot(pairs=["BTC/USDT"])
    bot = make_bot(cfg, spot)
    bot.cycle(now=spot.now)
    bot.cycle(now=spot.now + 60)
    assert Ping.hits == ["/ping-uuid", "/ping-uuid"]
    cfg.healthcheck_url = ""
    bot.cycle(now=spot.now + 120)
    assert len(Ping.hits) == 2
    srv.shutdown()


def test_jrn33_loop_dry_run(cfg, capsys, monkeypatch):
    cfg.dry_run = True
    cfg.run_minutes = 2 / 60
    cfg.scan_interval = 1
    spot = FakeSpot(pairs=["BTC/USDT", "ETH/USDT"])
    spot.now = time.time()
    router = wr.SpotRouter(factories=[("BINANCE", lambda: spot)])
    bot = wr.Bot(cfg, spot=router, deriv=wr.DerivRouter(sources=[("N", failing_source)]))
    rec = TGRecorder()
    monkeypatch.setattr(wr, "_http_post", rec)
    t0 = time.time()
    wr.run_loop(cfg, bot)
    assert len(bot.cycle_stats) == 2 and all(c["secs"] < 45 for c in bot.cycle_stats)
    assert time.time() - t0 < 10 and rec.payloads == []
    out = capsys.readouterr().out
    assert "[DRY_RUN]" in out and "BOT ALIVE" in out and "v10 LIVE" in out


def test_jrn34_cycle_error_continues_and_fallback(cfg, tg, monkeypatch):
    b = FakeSpot(name="BINANCE", pairs=["BTC/USDT"])
    b.down = True
    k = FakeSpot(name="KUCOIN", pairs=["BTC/USDT"])
    router = wr.SpotRouter(factories=[("BINANCE", lambda: b), ("KUCOIN", lambda: k)])
    assert router.select().name == "KUCOIN"
    cfg.run_minutes = 3 / 60
    cfg.scan_interval = 1
    bot = wr.Bot(cfg, spot=router, deriv=wr.DerivRouter(sources=[("N", failing_source)]))
    real = bot.cycle
    n = {"i": 0}

    def flaky(now=None):
        n["i"] += 1
        if n["i"] == 1:
            raise RuntimeError("boom")
        return real(now)
    bot.cycle = flaky
    wr.run_loop(cfg, bot)
    assert n["i"] == 3 and len(bot.cycle_stats) == 2
    k2 = FakeSpot(name="OKX", pairs=["BTC/USDT"])
    router2 = wr.SpotRouter(factories=[("BINANCE", lambda: b), ("KUCOIN", lambda: k), ("OKX", lambda: k2)])
    bot2 = wr.Bot(cfg, spot=router2, deriv=wr.DerivRouter(sources=[("N", failing_source)]))
    b.down = False
    router2.select()
    assert router2.src.name == "BINANCE"
    b.down = True
    assert bot2.cycle(now=b.now) is False
    assert router2.src.name == "KUCOIN"
    assert bot2.cycle(now=k.now) is True


def _run_cli(args, tmp_path, extra=None):
    env = {k: v for k, v in os.environ.items() if not k.lower().endswith("_proxy")}
    env.update({"TELEGRAM_BOT_TOKEN": f"  {FAKE_TOKEN}\n", "TELEGRAM_CHAT_ID": FAKE_CHAT, "LLM_API_KEY": FAKE_KEY,
                "STATE_FILE": str(tmp_path / "state.json"), "HTTPS_PROXY": "http://127.0.0.1:9",
                "HTTP_PROXY": "http://127.0.0.1:9", "https_proxy": "http://127.0.0.1:9", "SPOT_SOURCES": "BINANCE",
                "http_proxy": "http://127.0.0.1:9", "NO_PROXY": "", "no_proxy": ""})
    env.update(extra or {})
    return subprocess.run([sys.executable, os.path.join(ROOT, "war_room_bot.py"), *args], env=env,
                          capture_output=True, text=True, timeout=240, cwd=str(tmp_path))


def test_jrn43_secret_leak_cli_forced_errors(tmp_path):
    with open(tmp_path / "state.json", "w") as f:
        json.dump({"last_cycle_time": int(time.time()) - 4 * 3600}, f)
    outs = []
    r = _run_cli(["--watchdog"], tmp_path)
    outs.append(r.stdout + r.stderr)
    assert r.returncode == 0 and "Telegram send failed" in r.stdout
    r = _run_cli(["--test-messages"], tmp_path)
    outs.append(r.stdout + r.stderr)
    r = _run_cli(["--diagnose"], tmp_path)
    outs.append(r.stdout + r.stderr)
    assert r.returncode == 0 and "FAIL  telegram getMe" in r.stdout and "FAIL  spot BINANCE" in r.stdout
    assert "FAIL  gemini" in r.stdout
    r = _run_cli(["--diagnose"], tmp_path, {"LLM_API_KEY": "", "TELEGRAM_BOT_TOKEN": ""})
    outs.append(r.stdout + r.stderr)
    assert "skipped: no key" in r.stdout and r.returncode == 0
    r = _run_cli([], tmp_path, {"DRY_RUN": "1", "RUN_MINUTES": "0.01", "SCAN_INTERVAL": "1"})
    outs.append(r.stdout + r.stderr)
    assert r.returncode == 0
    blob = "\n".join(outs)
    for secret in (FAKE_TOKEN, FAKE_KEY, FAKE_CHAT, "FAKEtoken"):
        assert secret not in blob, secret


def test_jrn43_in_process_redaction(cfg, monkeypatch, capsys):
    cfg.llm_key = FAKE_KEY
    wr.register_secret(FAKE_KEY)
    monkeypatch.setattr(wr, "_http_post", TGRecorder(fail=True))
    wr.Telegram(cfg).send("x")

    class E(Exception):
        status_code = 401

    def factory(api_key, base_url, timeout):
        raise E(f"Incorrect API key provided: {api_key} for {base_url}")
    monkeypatch.setattr(wr, "_make_openai_client", factory)
    wr.AIReviewer(cfg).review({})
    wr.log(f"url https://api.telegram.org/bot{FAKE_TOKEN}/sendMessage chat {FAKE_CHAT}")
    out = capsys.readouterr()
    for secret in (FAKE_TOKEN, FAKE_KEY, FAKE_CHAT):
        assert secret not in out.out + out.err


def test_token_cleaning_and_mask(monkeypatch, capsys):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", f'"{FAKE_TOKEN}" ')
    monkeypatch.setenv("TELEGRAM_CHAT_ID", f" {FAKE_CHAT}\n")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    c = wr.Config()
    assert c.tg_token == FAKE_TOKEN and c.tg_chat == FAKE_CHAT and c.checkpoint
    wr.mask_secrets(c)
    out = capsys.readouterr().out
    assert f"::add-mask::{FAKE_TOKEN}" in out and f"::add-mask::{FAKE_CHAT}" in out


def test_l2_heartbeat_counters_reset_even_if_send_fails(cfg, monkeypatch):
    monkeypatch.setattr(wr, "_http_post", TGRecorder(fail=True))
    spot = FakeSpot(pairs=["BTC/USDT"])
    bot = make_bot(cfg, spot)
    bot.state["hour_stats"]["alerts"] = 5
    bot.cycle(now=spot.now)
    assert bot.state["hour_stats"]["alerts"] == 0 and bot.state["last_heartbeat"] == int(spot.now)
    assert any("BOT ALIVE" in m["text"] for m in bot.state["outbox"])


def test_l4_sigint_saves_state(tmp_path):
    import signal as sg
    env = {k: v for k, v in os.environ.items() if not k.lower().endswith("_proxy")}
    env.update({"STATE_FILE": str(tmp_path / "s.json"), "DRY_RUN": "1", "RUN_MINUTES": "5", "SCAN_INTERVAL": "1",
                "HTTPS_PROXY": "http://127.0.0.1:9", "https_proxy": "http://127.0.0.1:9", "NO_PROXY": "",
                "SPOT_SOURCES": "BINANCE"})
    p = subprocess.Popen([sys.executable, os.path.join(ROOT, "war_room_bot.py")], env=env, cwd=str(tmp_path),
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    time.sleep(6)
    p.send_signal(sg.SIGINT)
    out, _ = p.communicate(timeout=30)
    assert "Run finished" in out and (tmp_path / "s.json").exists() and p.returncode == 0
