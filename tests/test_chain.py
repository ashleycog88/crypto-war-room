"""Phase 2c self-chaining (REQ-154b..160b)."""
import os
import re

import pytest

import war_room_bot as wr
from fakes import FakeResp, FakeSpot, make_bot

GH_TOKEN = "ghs_FAKEgithubTOKEN1234567890abcdef"


class GH:
    def __init__(self, queued=0, post=(204,), get_fail=False):
        self.calls, self.queued, self.post, self.get_fail = [], queued, list(post), get_fail

    def __call__(self, method, url, token, json_body=None, params=None, timeout=15):
        self.calls.append((method, url, json_body, params))
        assert token == GH_TOKEN
        if method == "GET":
            if self.get_fail:
                raise ConnectionError("api down")
            return FakeResp(200, {"total_count": self.queued if params["status"] == "queued" else 0})
        r = self.post.pop(0) if self.post else 204
        if isinstance(r, Exception):
            raise r
        return FakeResp(r, {})

    def posts(self):
        return [c for c in self.calls if c[0] == "POST"]


@pytest.fixture
def gh(monkeypatch):
    def install(**kw):
        g = GH(**kw)
        monkeypatch.setattr(wr, "_gh_request", g)
        monkeypatch.setattr(wr, "_sleep", lambda s: None)
        return g
    return install


def chain_cfg(cfg, token=GH_TOKEN):
    cfg.gh_token, cfg.gh_repo, cfg.gh_ref = token, "me/war-room", "main"
    wr.register_secret(token)
    return cfg


def bot_at(cfg, minute):
    spot = FakeSpot(pairs=["BTC/USDT"])
    bot = make_bot(cfg, spot)
    bot.run_t0 = __import__("time").time() - minute * 60
    return bot, spot


def test_dispatch_once_at_minute_50(cfg, tg, gh):
    g = gh()
    bot, spot = bot_at(chain_cfg(cfg), 49)
    bot.cycle(now=spot.now)
    assert g.posts() == []
    bot.run_t0 -= 90
    for i in range(1, 4):
        bot.cycle(now=spot.now + 60 * i)
    p = g.posts()
    assert len(p) == 1 and p[0][1] == "https://api.github.com/repos/me/war-room/actions/workflows/war_room.yml/dispatches"
    assert p[0][2] == {"ref": "main"} and bot.chain == "queued ✅"
    bot.state["last_heartbeat"] = 0
    bot.cycle(now=spot.now + 300)
    assert "🔗 Next run queued ✅" in tg.texts("BOT ALIVE")[-1]


def test_not_dispatched_when_unhealthy(cfg, tg, gh):
    g = gh()
    bot, spot = bot_at(chain_cfg(cfg), 51)
    bot.state["cycle_times"] = [int(spot.now) - 900]
    bot.maybe_chain(spot.now)
    assert g.calls == [] and "no healthy scan" in bot.chain and not bot.chain_done
    info = bot.heartbeat_info(spot.now)
    assert "🔗 Next run ❌ not queued (no healthy scan in the last 10 min)" in wr.format_heartbeat(info)


def test_not_dispatched_when_run_already_queued(cfg, tg, gh):
    g = gh(queued=1)
    bot, spot = bot_at(chain_cfg(cfg), 52)
    bot.cycle(now=spot.now)
    bot.cycle(now=spot.now + 60)
    assert g.posts() == [] and len([c for c in g.calls if c[0] == "GET"]) == 1
    assert bot.chain.startswith("queued ✅")


def test_queue_check_failure_still_dispatches(cfg, tg, gh):
    g = gh(get_fail=True)
    bot, spot = bot_at(chain_cfg(cfg), 52)
    bot.cycle(now=spot.now)
    assert len(g.posts()) == 1 and bot.chain == "queued ✅"


@pytest.mark.parametrize("token,dry", [("", False), (GH_TOKEN, True)])
def test_no_token_or_dry_run_never_dispatches(cfg, tg, gh, token, dry):
    g = gh()
    cfg.dry_run = dry
    bot, spot = bot_at(chain_cfg(cfg, token) if token else cfg, 55)
    bot.cycle(now=spot.now)
    assert g.calls == [] and bot.chain is None
    assert "Next run" not in wr.format_heartbeat(bot.heartbeat_info(spot.now))


def test_failure_retried_once_then_heartbeat_warning(cfg, tg, gh, capsys):
    g = gh(post=[403, ConnectionError(f"boom token={GH_TOKEN}")])
    bot, spot = bot_at(chain_cfg(cfg), 50)
    bot.cycle(now=spot.now)
    bot.cycle(now=spot.now + 60)
    assert len(g.posts()) == 2 and bot.chain == "❌ not queued (dispatch failed: ConnectionError)"
    bot.state["last_heartbeat"] = 0
    bot.cycle(now=spot.now + 120)
    assert "🔗 Next run ❌ not queued (dispatch failed: ConnectionError)" in tg.texts("BOT ALIVE")[-1]
    out = capsys.readouterr().out
    assert "dispatch attempt 1 failed: HTTP 403" in out and GH_TOKEN not in out
    assert all(GH_TOKEN not in t for t in tg.texts())


def test_success_after_one_retry(cfg, tg, gh):
    g = gh(post=[500, 204])
    bot, spot = bot_at(chain_cfg(cfg), 50)
    bot.cycle(now=spot.now)
    assert len(g.posts()) == 2 and bot.chain == "queued ✅"


def test_chain_result_shown_in_next_runs_heartbeat(cfg, tg, gh):
    gh()
    bot, spot = bot_at(chain_cfg(cfg), 51)
    bot.cycle(now=spot.now)
    nxt = wr.Bot(cfg, spot=bot.spot, deriv=bot.deriv)  # next run: loads state, chain not yet attempted
    nxt.coins = ["BTC/USDT"]
    nxt.state["last_heartbeat"] = 0
    nxt.cycle(now=spot.now + 60)
    assert nxt.chain is None and "🔗 Next run queued ✅" in tg.texts("BOT ALIVE")[-1]


def test_spot_sources_env_limits_fallbacks(monkeypatch):
    monkeypatch.setenv("SPOT_SOURCES", "binance")
    assert [n for n, _ in wr.default_spot_factories()] == ["BINANCE"]
    monkeypatch.delenv("SPOT_SOURCES")
    assert len(wr.default_spot_factories()) == 1 + len(wr.FALLBACK_EXCHANGES)


def test_config_and_workflow(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", GH_TOKEN)
    monkeypatch.setenv("GITHUB_REPOSITORY", "me/war-room")
    monkeypatch.setenv("CHAIN_AT_MINUTE", "45")
    c = wr.Config()
    assert (c.gh_token, c.gh_repo, c.gh_ref, c.chain_at_minute) == (GH_TOKEN, "me/war-room", "main", 45)
    wr.mask_secrets(c)
    assert GH_TOKEN not in wr.redact(f"x {GH_TOKEN} y")
    y = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          ".github/workflows/war_room.yml")).read()
    assert "GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}" in y and "HEALTHCHECK_URL: ${{ secrets.HEALTHCHECK_URL }}" in y
    assert re.search(r"cron: '7 \* \* \* \*'", y) and "actions: write" in y


def test_healthcheck_pinged_every_completed_cycle(cfg, tg, monkeypatch):
    pings = []
    monkeypatch.setattr(wr, "_http_get", lambda url, timeout, params=None: pings.append(url))
    cfg.healthcheck_url = "https://hc-ping.com/fake-uuid"
    spot = FakeSpot(pairs=["BTC/USDT"])
    bot = make_bot(cfg, spot)
    for i in range(3):
        bot.cycle(now=spot.now + 60 * i)
    assert pings == ["https://hc-ping.com/fake-uuid"] * 3
