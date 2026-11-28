"""Phase 3c privacy lock (JRN-120..128) for the live bot."""
import json
import os
import subprocess
import sys
import time

import pytest

import datalock
import war_room_bot as wr
from fakes import FakeSpot, TGRecorder, install_scores, make_bot
from test_state_git import git, sandbox  # noqa: F401  (fixture)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PASS = "test-only passphrase correct horse"
KEY = datalock.derive_key(PASS, datalock.read_salt())


def trade_state():
    st = wr.empty_state()
    st["active_trades"] = [{"symbol": "ETH/USDT", "dir": "LONG", "grade": "A", "entry": 100.0, "sl": 98.0, "tp1": 102.0,
                            "tp2": 104.0, "entry_time": int(time.time()) - 600, "tp1_hit": False, "checked_until": 0,
                            "exchange": "BINANCE", "status": "OPEN"}]
    st["totals"] = {"A": {"w": 3, "l": 1, "net_sum": 2.5}, "B": {"w": 0, "l": 2, "net_sum": -1.0}}
    st["last_alert"] = {"ETH/USDT": int(time.time()) - 60}
    return st


def test_jrn120_no_key_works_with_warning(cfg, tg):
    spot = FakeSpot(pairs=["BTC/USDT"])
    bot = make_bot(cfg, spot)
    bot.cycle(now=spot.now)
    assert os.path.exists(cfg.state_file) and not os.path.exists(cfg.state_file + ".enc")
    assert "🔓 Data not encrypted — add DATA_KEY" in tg.texts("times this hour")[0]
    s = wr.format_daily_summary(bot.state, spot.now, extra=bot.privacy_lines())
    assert "🔓 Data not encrypted — add DATA_KEY" in s


def test_jrn121_migration_preserves_everything(cfg, tg):
    wr.save_state(cfg.state_file, trade_state())
    cfg.data_key = PASS
    spot = FakeSpot(pairs=["BTC/USDT"])
    bot = make_bot(cfg, spot)
    bot.cycle(now=spot.now)
    enc = cfg.state_file + ".enc"
    assert os.path.exists(enc) and not os.path.exists(cfg.state_file)
    raw = open(enc, "rb").read()
    assert raw.startswith(datalock.MAGIC) and b"ETH" not in raw and b"active_trades" not in raw
    st, locked = wr.load_state_secure(cfg.state_file, KEY)
    assert not locked and st["active_trades"][0]["symbol"] == "ETH/USDT"
    assert st["totals"]["A"]["w"] == 3 and "ETH/USDT" in st["last_alert"]
    assert all("🔓" not in t for t in tg.texts()) and wr.push_path(cfg) == enc


def test_jrn122_wrong_key_never_overwrites(cfg, tg):
    enc = cfg.state_file + ".enc"
    datalock.write_atomic(enc, datalock.encrypt(KEY, json.dumps(trade_state()).encode()))
    before = open(enc, "rb").read()
    cfg.data_key = "the wrong passphrase"
    spot = FakeSpot(pairs=["BTC/USDT"])
    bot = make_bot(cfg, spot)
    for i in range(3):
        bot.cycle(now=spot.now + 60 * i)
    assert open(enc, "rb").read() == before and not os.path.exists(cfg.state_file)
    assert tg.texts("Can't unlock") == ["⚠️ Can't unlock my memory — check the DATA_KEY secret"]
    assert tg.texts("v10 is LIVE") == [] and tg.texts("HOW TO READ") == []


def test_jrn123_tampered_files_rejected(cfg, tg, tmp_path):
    enc = cfg.state_file + ".enc"
    blob = bytearray(datalock.encrypt(KEY, json.dumps(trade_state()).encode()))
    blob[-5] ^= 1
    open(enc, "wb").write(bytes(blob))
    with pytest.raises(datalock.LockError, match="tampered"):
        datalock.decrypt(KEY, bytes(blob))
    cfg.data_key = PASS
    bot = make_bot(cfg, FakeSpot(pairs=["BTC/USDT"]))
    assert bot.locked
    r = subprocess.run([sys.executable, os.path.join(ROOT, "datalock.py"), "decrypt", enc], capture_output=True,
                       text=True, env=dict(os.environ, DATA_KEY=PASS))
    assert r.returncode == 2 and "wrong DATA_KEY or the file was changed" in r.stderr


def test_jrn125_bot_settings(monkeypatch, cfg, tg):
    for g in ("OB_PENALTY", "A_MIN"):
        monkeypatch.setattr(wr, g, getattr(wr, g))  # restored after the test
    monkeypatch.setenv("BOT_SETTINGS", '{"MIN_TP1_FEE_MULT": 5, "OB_PENALTY": 10, "FOO": 1, "TOP_N_COINS": "abc"}')
    c = wr.Config(state_file=cfg.state_file)
    assert c.min_tp1_fee_mult == 5 and wr.OB_PENALTY == 10 and c.top_n == 50 and wr.A_MIN == 80
    assert c.settings_ignored == ["FOO", "TOP_N_COINS"]
    bot = make_bot(c, FakeSpot(pairs=["BTC/USDT"]))
    bot.cycle(now=bot.spot.factories[0][1]().now)
    assert "⚠️ Some BOT_SETTINGS ignored: FOO, TOP_N_COINS" in tg.texts("times this hour")[0]
    monkeypatch.setenv("BOT_SETTINGS", "not json")
    assert wr.Config().settings_ignored == ["(not valid JSON)"]
    monkeypatch.setenv("BOT_SETTINGS", '{"EXCLUDE_COINS": ["pepe", "WIF/USDT"], "USE_DERIV_SCORE": true, "A_MIN": 85.0}')
    c = wr.Config()
    assert c.exclude_coins == {"PEPE", "WIF"} and c.use_deriv is True and wr.A_MIN == 85 and not c.settings_ignored


def test_jrn126_quiet_public_logs(cfg, tg, monkeypatch, capsys):
    spot = FakeSpot(pairs=["BTC/USDT", "ETH/USDT"])
    install_scores(monkeypatch, spot, {"BTC/USDT": (85, 0)})
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    bot = make_bot(cfg, spot)
    bot.cycle(now=spot.now)
    assert len(bot.state["active_trades"]) == 1
    out = capsys.readouterr().out
    assert "Cycle 1 done" in out
    for bad in ("BTC", "ETH", "/USDT", "GRADE", "85/100", "LONG", "SHORT"):
        assert bad not in out, bad
    try:
        raise ValueError("boom at https://api.example.com/x?coin=BTC price 123.45 wallet 0xabc123")
    except ValueError as e:
        t = wr.err_text(e)
    assert "123" not in t and "example.com" not in t and "0xabc" not in t
    monkeypatch.setenv("LOG_DETAIL", "1")
    bot.cycle(now=spot.now + 60)
    assert "BTC" in capsys.readouterr().out


@pytest.mark.parametrize("encrypted", [True, False])
def test_jrn127_watchdog_with_and_without_encryption(cfg, monkeypatch, encrypted):
    rec = TGRecorder()
    monkeypatch.setattr(wr, "_http_post", rec)
    for ago, expect in ((3 * 3600, 1), (1800, 0)):
        st = dict(wr.empty_state(), last_cycle_time=int(time.time()) - ago)
        if encrypted:
            cfg.data_key = PASS
            wr.save_state_secure(cfg.state_file, st, KEY)
        else:
            wr.save_state(cfg.state_file, st)
        n = len(rec.texts("BOT DOWN"))
        wr.run_watchdog(cfg)
        assert len(rec.texts("BOT DOWN")) - n == expect


def test_git_sync_encrypted_replaces_plaintext(sandbox):  # noqa: F811
    tmp, remote, bot_dir, user_dir = sandbox
    wr.save_state_secure(str(bot_dir / "state.json"), dict(wr.empty_state(), last_cycle_time=321), KEY)
    assert not (bot_dir / "state.json").exists()
    assert wr.git_sync(str(bot_dir), str(bot_dir / "state.json.enc"), "main", backoff=0)
    check = tmp / "check"
    git(tmp, "clone", str(remote), str(check))
    assert not (check / "state.json").exists() and (check / "README.md").exists()
    assert json.loads(datalock.read_plain(str(check / "state.json.enc"), KEY))["last_cycle_time"] == 321


def test_jrn128_repo_hygiene():
    gi = open(os.path.join(ROOT, ".gitignore")).read()
    for p in ("backtest_report.md", "backtest_results.json", "research_report.md", "*.csv", "private_reports/"):
        assert p in gi.splitlines(), p
    assert len(datalock.read_salt()) == 16
    r = subprocess.run(["git", "check-ignore", "-q", "datalock.salt"], cwd=ROOT)
    assert r.returncode == 1  # salt is NOT ignored, so it gets committed
    readme = open(os.path.join(ROOT, "README.md")).read()
    assert "`DATA_KEY`" in readme and "Older versions stay in git history" in readme and "LOG_DETAIL=1" in readme
    for wf in ("war_room.yml", "watchdog.yml", "recorder.yml"):
        assert "DATA_KEY: ${{ secrets.DATA_KEY }}" in open(os.path.join(ROOT, ".github/workflows", wf)).read()
