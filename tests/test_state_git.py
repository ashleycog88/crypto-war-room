"""State upgrade/corruption/crash and git persistence (JRN-27..29, 36, 37)."""
import json
import os
import shutil
import signal
import subprocess
import sys
import textwrap

import pytest

import war_room_bot as wr

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
V9 = {"active_trades": [], "closed_trades": [], "last_alert_time": {}, "booted": True}


def test_jrn27_v9_upgrade(tmp_path):
    p = tmp_path / "state.json"
    shutil.copy(os.path.join(ROOT, "state.json"), p)
    st = wr.load_state(str(p))
    assert st["version"] == 10 and st["booted"] and st["v10_live_sent"] is False
    old = dict(V9, last_alert_time={"BTC/USDT": 1700000000},
               active_trades=[{"symbol": "ETH/USDT", "dir": "LONG", "entry": 3000, "sl": 2950, "tp1": 3050,
                               "tp2": 3100}],
               closed_trades=[{"symbol": "SOL/USDT", "dir": "SHORT", "pnl": 1.2, "status": "TP2 HIT"}])
    p.write_text(json.dumps(old))
    st = wr.load_state(str(p), now=1800000000)
    t = st["active_trades"][0]
    assert t["grade"] == "B" and t["entry_time"] == 1800000000 and t["legacy"] and t["tp1_hit"] is False
    assert st["last_alert_time"] == {"BTC/USDT": 1700000000}
    assert st["closed_trades"][0]["legacy"]
    wr.save_state(str(p), st)
    assert wr.load_state(str(p))["active_trades"][0]["symbol"] == "ETH/USDT"


def test_jrn28_corrupt_state(tmp_path):
    p = tmp_path / "state.json"
    p.write_text("{not json")
    st = wr.load_state(str(p))
    assert st["active_trades"] == [] and st["outbox"]
    backups = [f for f in os.listdir(tmp_path) if f.startswith("state.json.corrupt-")]
    assert backups and (tmp_path / backups[0]).read_text() == "{not json"


def test_atomic_save_and_cap(tmp_path):
    p = tmp_path / "state.json"
    st = wr.empty_state()
    st["closed_trades"] = [{"i": i} for i in range(1500)]
    st = wr.upgrade_state(st)
    assert len(st["closed_trades"]) == 1000 and st["closed_trades"][-1]["i"] == 1499
    wr.save_state(str(p), st)
    assert json.loads(p.read_text())["version"] == 10
    assert not [f for f in os.listdir(tmp_path) if f.endswith(".tmp")]


CRASH = textwrap.dedent("""
    import os, signal, sys
    sys.path[:0] = [{root!r}, {tests!r}]
    import war_room_bot as wr
    from fakes import FakeSpot, TGRecorder, install_scores, make_bot

    class MP:
        def setattr(self, obj, name, val): setattr(obj, name, val)
    wr._http_post = TGRecorder()
    wr.TG_GAP_S = 0
    cfg = wr.Config()
    spot = FakeSpot(pairs=["BTC/USDT"], now={now})
    install_scores(MP(), spot, {{"BTC/USDT": (85, 0)}})
    bot = make_bot(cfg, spot)
    orig = bot.save
    def save_then_die():
        orig()
        if bot.state["active_trades"]:
            os.kill(os.getpid(), signal.SIGKILL)
    bot.save = save_then_die
    bot.cycle(now={now})
""")


def test_jrn29_crash_after_trade_open(tmp_path, monkeypatch):
    from fakes import FakeSpot, TGRecorder, install_scores, make_bot
    now = 1_800_000_123
    sp = tmp_path / "state.json"
    script = tmp_path / "crash.py"
    script.write_text(CRASH.format(root=ROOT, tests=os.path.join(ROOT, "tests"), now=now))
    env = dict(os.environ, STATE_FILE=str(sp), TELEGRAM_BOT_TOKEN="123456789:FAKEtokenFAKEtokenFAKEtoken123",
               TELEGRAM_CHAT_ID="-1009876543210")
    r = subprocess.run([sys.executable, str(script)], env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == -signal.SIGKILL, r.stdout + r.stderr
    st = json.loads(sp.read_text())
    assert len(st["active_trades"]) == 1 and st["last_alert_time"]["BTC/USDT"] == now
    monkeypatch.setenv("STATE_FILE", str(sp))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456789:FAKEtokenFAKEtokenFAKEtoken123")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-1009876543210")
    rec = TGRecorder()
    monkeypatch.setattr(wr, "_http_post", rec)
    cfg = wr.Config()
    spot = FakeSpot(pairs=["BTC/USDT"], now=now + 60)
    install_scores(monkeypatch, spot, {"BTC/USDT": (85, 0)})
    bot = make_bot(cfg, spot)
    bot.cycle(now=now + 60)
    assert rec.texts("A-GRADE") == [] and len(bot.state["active_trades"]) == 1


def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "init.defaultBranch=main", *args],
                          cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def sandbox(tmp_path):
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "--bare", "-b", "main", str(remote))
    seed = tmp_path / "seed"
    git(tmp_path, "clone", str(remote), str(seed))
    (seed / "state.json").write_text(json.dumps(V9))
    (seed / "README.md").write_text("v1\n")
    git(seed, "add", ".")
    git(seed, "commit", "-m", "seed")
    git(seed, "push", "origin", "HEAD:main")
    bot_dir, user_dir = tmp_path / "bot", tmp_path / "user"
    git(tmp_path, "clone", str(remote), str(bot_dir))
    git(tmp_path, "clone", str(remote), str(user_dir))
    return tmp_path, remote, bot_dir, user_dir


def user_edit(user_dir, readme, state_note):
    git(user_dir, "pull", "--quiet", "--rebase", "origin", "main")
    (user_dir / "README.md").write_text(readme)
    (user_dir / "state.json").write_text(json.dumps(dict(V9, user_edit=state_note)))
    git(user_dir, "commit", "-am", "user edit on GitHub")
    git(user_dir, "push", "origin", "HEAD:main")


def test_jrn36_git_conflict_bot_state_wins(sandbox):
    tmp, remote, bot_dir, user_dir = sandbox
    user_edit(user_dir, "user changed readme\n", "x")
    bot_state = dict(wr.empty_state(), last_cycle_time=123)
    wr.save_state(str(bot_dir / "state.json"), bot_state)
    assert wr.git_sync(str(bot_dir), str(bot_dir / "state.json"), "main", backoff=0)
    check = tmp / "check"
    git(tmp, "clone", str(remote), str(check))
    assert json.loads((check / "state.json").read_text())["last_cycle_time"] == 123
    assert (check / "README.md").read_text() == "user changed readme\n"
    assert json.loads((bot_dir / "state.json").read_text())["last_cycle_time"] == 123


def test_jrn36_push_race_retries(sandbox, monkeypatch):
    tmp, remote, bot_dir, user_dir = sandbox
    real = subprocess.run
    raced = {"n": 0}

    def racing(cmd, *a, **kw):
        if "push" in cmd and cmd[0] == "git" and raced["n"] == 0 and kw.get("cwd") == str(bot_dir):
            raced["n"] += 1
            user_edit(user_dir, "raced readme\n", "race")
        return real(cmd, *a, **kw)
    monkeypatch.setattr(wr.subprocess, "run", racing)
    wr.save_state(str(bot_dir / "state.json"), dict(wr.empty_state(), last_cycle_time=456))
    assert wr.git_sync(str(bot_dir), str(bot_dir / "state.json"), "main", backoff=0)
    monkeypatch.setattr(wr.subprocess, "run", real)
    check = tmp / "check"
    git(tmp, "clone", str(remote), str(check))
    assert json.loads((check / "state.json").read_text())["last_cycle_time"] == 456
    assert (check / "README.md").read_text() == "raced readme\n"
    assert int(git(check, "rev-list", "--count", "HEAD").strip()) == 3  # seed, raced user edit, bot


def test_commit_state_cli_in_sandbox(sandbox):
    tmp, remote, bot_dir, user_dir = sandbox
    user_edit(user_dir, "cli readme\n", "y")
    shutil.copy(os.path.join(ROOT, "war_room_bot.py"), bot_dir / "war_room_bot.py")
    wr.save_state(str(bot_dir / "state.json"), dict(wr.empty_state(), last_cycle_time=789))
    env = {k: v for k, v in os.environ.items() if k not in ("STATE_FILE", "GITHUB_WORKSPACE")}
    r = subprocess.run([sys.executable, "war_room_bot.py", "--commit-state"], cwd=bot_dir, env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    check = tmp / "check"
    git(tmp, "clone", str(remote), str(check))
    assert json.loads((check / "state.json").read_text())["last_cycle_time"] == 789
    assert (check / "README.md").read_text() == "cli readme\n"


def test_jrn37_checkpoint_every_15_min_only_on_change(cfg, tg, monkeypatch):
    from fakes import FakeSpot, make_bot
    pushes = []
    monkeypatch.setattr(wr, "git_sync", lambda *a, **k: pushes.append(a) or True)
    cfg.checkpoint = True
    spot = FakeSpot(pairs=["BTC/USDT"])
    bot = make_bot(cfg, spot)
    t0 = spot.now
    for i in range(20):
        spot.now = t0 + i * 60
        bot.cycle(now=spot.now)
    assert len(pushes) == 2  # at t0 and t0+15min
    bot.maybe_checkpoint(t0 + 3000)
    n = len(pushes)
    bot.maybe_checkpoint(t0 + 5000)
    assert len(pushes) == n  # unchanged state → no push
