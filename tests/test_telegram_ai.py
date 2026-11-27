"""Telegram safety, AI review, duplicates, delivery-before-record (JRN-10..17, 41, 53)."""
import html
import re
import time
from types import SimpleNamespace

import pytest

import war_room_bot as wr
from fakes import FakeResp, FakeSpot, TGRecorder, install_scores, make_bot

HOSTILE = "<script>alert(1)</script> a < b & c > d <b>unclosed " + "x" * 5000


class FakeClient:
    def __init__(self, behaviours, log):
        self.behaviours = behaviours
        self.log = log
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.log.append(kw)
        b = self.behaviours.pop(0) if self.behaviours else "APPROVED: ok"
        if isinstance(b, Exception):
            raise b
        if callable(b):
            return b(kw)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=b))])


class ApiErr(Exception):
    def __init__(self, msg, status):
        super().__init__(msg)
        self.status_code = status


@pytest.fixture
def ai_env(monkeypatch):
    calls, made = [], []

    def install(*behaviours):
        bl = list(behaviours)

        def factory(api_key, base_url, timeout):
            made.append({"base_url": base_url, "timeout": timeout})
            return FakeClient(bl, calls)
        monkeypatch.setattr(wr, "_make_openai_client", factory)
        return calls, made
    return install


def ai_cfg(cfg, key="FAKEKEY"):
    cfg.llm_key = key
    wr.register_secret(key)
    return cfg


def alert_bot(cfg, monkeypatch, score=(85, 0), pairs=("BTC/USDT",)):
    spot = FakeSpot(pairs=list(pairs))
    install_scores(monkeypatch, spot, {p: score for p in pairs})
    return make_bot(cfg, spot), spot


def test_jrn11_ai_approved_sentence_in_alert(cfg, tg, monkeypatch, ai_env):
    calls, made = ai_env("APPROVED: momentum aligned, watch BTC dominance.")
    bot, spot = alert_bot(ai_cfg(cfg), monkeypatch)
    bot.cycle(now=spot.now)
    a = tg.texts("A-GRADE ·")
    assert len(a) == 1 and "✅ looks good — “momentum aligned, watch BTC dominance.”" in a[0]
    assert calls[0]["max_tokens"] >= 512 and calls[0]["reasoning_effort"] == "low"


def test_jrn12_ai_rejected_drops_alert(cfg, tg, monkeypatch, ai_env):
    ai_env("REJECTED: thin liquidity.")
    bot, spot = alert_bot(ai_cfg(cfg), monkeypatch)
    bot.cycle(now=spot.now)
    assert tg.texts("A-GRADE ·") == [] and bot.state["active_trades"] == []
    hb = tg.texts("times this hour")[0]
    assert "AI blocked 1" in hb
    bot.cycle(now=spot.now + 60)  # same closed 5m candle → rejected setup is not re-reviewed or sent
    assert tg.texts("A-GRADE ·") == [] and bot.state["active_trades"] == []


@pytest.mark.parametrize("reply,verdict", [("**REJECTED**: nope", "REJECTED"), ("  rejected: thin", "REJECTED"),
                                           ("> APPROVED: fine", "APPROVED"), ("Looks ok", "UNCLEAR"),
                                           ("`REJECTED` - risky", "REJECTED")])
def test_ai_parse_robust(reply, verdict):
    assert wr.parse_ai_reply(reply)[0] == verdict


def test_jrn13_no_key_ai_unavailable(cfg, tg, monkeypatch):
    bot, spot = alert_bot(cfg, monkeypatch)
    bot.cycle(now=spot.now)
    a = tg.texts("A-GRADE ·")
    assert len(a) == 1 and "AI unavailable" in a[0]


def test_empty_secrets_fall_back_to_defaults(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "")
    monkeypatch.setenv("LLM_MODEL", "  ")
    c = wr.Config()
    assert c.llm_url == "https://generativelanguage.googleapis.com/v1beta/openai/"
    assert c.llm_model == "gemini-3.5-flash-lite"


def test_jrn14_429_hang_none(cfg, tg, monkeypatch, ai_env):
    monkeypatch.setattr(wr, "AI_TIMEOUT_S", 1)
    monkeypatch.setattr(wr, "AI_BUDGET_S", 2.5)
    ai = wr.AIReviewer(ai_cfg(cfg))
    setup = {"symbol": "BTC/USDT", "dir": "LONG"}
    ai_env(ApiErr("Too many requests", 429), ApiErr("Too many requests", 429), ApiErr("Too many requests", 429))
    v, note = ai.review(setup)
    assert v == "UNAVAILABLE" and "AI unavailable" in note and ai.backoff_until > time.time() + 200
    ai2 = wr.AIReviewer(cfg)
    ai_env(lambda kw: time.sleep(5), lambda kw: time.sleep(5), lambda kw: time.sleep(5))
    t0 = time.time()
    v, note = ai2.review(setup)
    assert v == "UNAVAILABLE" and "AI unavailable" in note and time.time() - t0 < 3.5
    ai3 = wr.AIReviewer(cfg)
    ai_env(None, None, None)
    v, note = ai3.review(setup)
    assert v == "UNAVAILABLE" and "AI unavailable" in note


def test_jrn14_alert_still_sent_on_ai_error(cfg, tg, monkeypatch, ai_env):
    ai_env(ApiErr("boom", 500), ApiErr("boom", 500), ApiErr("boom", 500))
    bot, spot = alert_bot(ai_cfg(cfg), monkeypatch)
    bot.cycle(now=spot.now)
    a = tg.texts("A-GRADE ·")
    assert len(a) == 1 and "AI unavailable" in a[0] and len(bot.state["active_trades"]) == 1


def test_jrn15_defaults_and_model_fallback(cfg, ai_env, monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "")
    monkeypatch.setenv("LLM_MODEL", "")
    monkeypatch.setenv("LLM_API_KEY", "FAKEKEY")
    c = wr.Config()
    calls, made = ai_env(ApiErr("model not found", 404), TimeoutError("slow"), "APPROVED: fine")
    v, note = wr.AIReviewer(c).review({"symbol": "BTC/USDT"})
    assert made[0]["base_url"] == "https://generativelanguage.googleapis.com/v1beta/openai/"
    assert made[0]["timeout"] <= 20
    assert [k["model"] for k in calls] == ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-3.6-flash"]
    assert v == "APPROVED"


def test_fallback_chain_configurable_and_stops_on_success(cfg, ai_env, monkeypatch):
    monkeypatch.setenv("LLM_FALLBACK_MODELS", " m-two , m-three ,")
    monkeypatch.setenv("LLM_API_KEY", "FAKEKEY")
    c = wr.Config()
    assert c.llm_fallbacks == ["m-two", "m-three"]
    calls, _ = ai_env(ApiErr("boom", 500), "REJECTED: thin")
    assert wr.AIReviewer(c).review({})[0] == "REJECTED"
    assert [k["model"] for k in calls] == ["gemini-3.5-flash-lite", "m-two"]
    monkeypatch.setenv("LLM_FALLBACK_MODELS", "")
    assert wr.Config().llm_fallbacks == ["gemini-3.1-flash-lite", "gemini-3.6-flash"]
    assert wr.AI_BUDGET_S <= 30 and wr.AI_TIMEOUT_S <= 20


def test_reasoning_effort_rejected_retry_without(cfg, ai_env):
    calls, _ = ai_env(ApiErr("Invalid argument: reasoning_effort not supported", 400), "APPROVED: ok")
    ai = wr.AIReviewer(ai_cfg(cfg))
    assert ai.review({"symbol": "X"})[0] == "APPROVED"
    assert "reasoning_effort" in calls[0] and "reasoning_effort" not in calls[1]
    assert calls[1]["model"] == calls[0]["model"]


def test_ai_throttle(cfg, ai_env):
    ai_env("APPROVED: a", "APPROVED: b")
    ai = wr.AIReviewer(ai_cfg(cfg))
    assert ai.review({})[0] == "APPROVED"
    v, note = ai.review({}, start_by=time.time())
    assert v == "DEFER"  # own throttle never yields "AI unavailable"


def test_jrn68_throttle_waits_or_defers_never_skips(cfg, tg, monkeypatch, ai_env):
    monkeypatch.setattr(wr, "AI_MIN_GAP_S", 1)
    monkeypatch.setattr(wr, "CYCLE_AI_BUDGET_S", 2.5)
    calls, _ = ai_env(*(["APPROVED: fine"] * 5 + ["REJECTED: thin"] + ["APPROVED: fine"] * 2))
    pairs = [f"C{i}/USDT" for i in range(8)]
    bot, spot = alert_bot(ai_cfg(cfg), monkeypatch, pairs=pairs)
    bot.state["v10_live_sent"] = bot.state["guide_sent"] = True
    bot.state["last_heartbeat"] = spot.now
    bot.cycle(now=spot.now)
    first = len(calls)
    assert 2 <= first < 8 and len(bot.state["active_trades"]) == len(tg.texts("/100)"))
    for k in range(1, 5):
        bot.cycle(now=spot.now + 10 * k)  # same closed 5m candle
    assert len(calls) == 8
    alerts = tg.texts("/100)")
    assert len(alerts) == 7 and all("looks good" in a for a in alerts) and not tg.texts("AI unavailable")
    assert len(bot.state["active_trades"]) == 7 and bot.state["hour_stats"]["ai_rejected"] == 1


def test_jrn69_unavailable_only_for_no_key_or_backoff(cfg, tg, monkeypatch, ai_env):
    bot, spot = alert_bot(cfg, monkeypatch)
    bot.cycle(now=spot.now)
    assert "AI unavailable (no key)" in tg.texts("/100)")[0]
    ai_env("APPROVED: x")
    bot2, spot2 = alert_bot(ai_cfg(cfg), monkeypatch, pairs=["ETH/USDT"])
    bot2.ai.backoff_until = time.time() + 300
    bot2.cycle(now=spot2.now)
    assert "AI unavailable (rate-limited, pausing)" in tg.texts("/100)")[-1]


def _balanced(text):
    stack = []
    for m in re.finditer(r"<(/?)([a-z]+)>", text):
        if m.group(1):
            assert stack and stack.pop() == m.group(2)
        else:
            stack.append(m.group(2))
    return not stack


def test_jrn16_hostile_ai_text(cfg, tg, monkeypatch, ai_env):
    ai_env("APPROVED: " + HOSTILE)
    bot, spot = alert_bot(ai_cfg(cfg), monkeypatch)
    bot.cycle(now=spot.now)
    msg = tg.texts("A-GRADE ·")[0]
    assert "<script>" not in msg and "&lt;script&gt;" in msg and "a &lt; b &amp; c &gt; d" in msg
    assert len(msg) <= 4096 and _balanced(msg)
    assert tg.payloads[-2 if "BOT ALIVE" in tg.payloads[-1]["text"] else -1].get("parse_mode") == "HTML"
    big = "<b>" + wr.esc(HOSTILE) * 3 + "</b>"
    t = wr.truncate_html(big)
    assert len(t) <= 4096 and _balanced(t) and not re.search(r"&[a-z]*$", t.replace("…</b>", ""))


def test_jrn17_400_plain_fallback_and_429_retry(cfg, monkeypatch):
    sleeps = []
    monkeypatch.setattr(wr, "_sleep", lambda s: sleeps.append(s))
    rec = TGRecorder(script=[FakeResp(400, {"ok": False, "description": "Bad Request: can't parse entities"}),
                             FakeResp(200, {"ok": True})])
    monkeypatch.setattr(wr, "_http_post", rec)
    assert wr.Telegram(cfg).send("<b>hi &amp; bye</b>") is True
    assert rec.payloads[0]["parse_mode"] == "HTML" and "parse_mode" not in rec.payloads[1]
    assert rec.payloads[1]["text"] == "hi & bye"
    rec2 = TGRecorder(script=[FakeResp(429, {"ok": False, "description": "Too Many Requests",
                                             "parameters": {"retry_after": 3}}), FakeResp(200, {"ok": True})])
    monkeypatch.setattr(wr, "_http_post", rec2)
    assert wr.Telegram(cfg).send("hello") is True
    assert 3 in sleeps and len(rec2.payloads) == 2
    rec3 = TGRecorder(script=[FakeResp(500, {"ok": False})])
    monkeypatch.setattr(wr, "_http_post", rec3)
    assert wr.Telegram(cfg).send("x") is False


def test_dry_run_prints(cfg, capsys, monkeypatch):
    rec = TGRecorder()
    monkeypatch.setattr(wr, "_http_post", rec)
    cfg.dry_run = True
    assert wr.Telegram(cfg).send("<b>hello</b>")
    assert "[DRY_RUN]" in capsys.readouterr().out and rec.payloads == []


def test_jrn10_no_duplicates_60_cycles(cfg, tg, monkeypatch):
    bot, spot = alert_bot(cfg, monkeypatch)
    t0 = spot.now
    for i in range(60):
        spot.now = t0 + i * 60
        bot.cycle(now=spot.now)
    assert len(tg.texts("A-GRADE ·")) == 1 and len(bot.state["active_trades"]) == 1


def test_same_closed_candle_never_alerts_twice(cfg, tg, monkeypatch):
    bot, spot = alert_bot(cfg, monkeypatch)
    bot.cycle(now=spot.now)
    bot.state["active_trades"] = []
    bot.state["last_alert_time"] = {}
    bot.cycle(now=spot.now + 1)
    assert len(tg.texts("A-GRADE ·")) == 1


def test_jrn41_failed_delivery_not_recorded_then_retried(cfg, monkeypatch):
    rec = TGRecorder(fail=True)
    monkeypatch.setattr(wr, "_http_post", rec)
    bot, spot = alert_bot(cfg, monkeypatch)
    bot.cycle(now=spot.now)
    assert bot.state["active_trades"] == [] and bot.state["last_alert_time"] == {}
    rec.fail = False
    bot.cycle(now=spot.now + 60)
    bot.cycle(now=spot.now + 120)
    assert len(rec.texts("A-GRADE ·")) == 1 and len(bot.state["active_trades"]) == 1


def test_jrn53_ten_alerts_reviewed_or_deferred_and_throttled(cfg, tg, monkeypatch, ai_env):
    monkeypatch.setattr(wr, "TG_GAP_S", 1.05)
    monkeypatch.setattr(wr, "AI_MIN_GAP_S", 0)
    calls, _ = ai_env(*(["APPROVED: fine"] * 10))
    pairs = [f"C{i}/USDT" for i in range(10)]
    bot, spot = alert_bot(ai_cfg(cfg), monkeypatch, pairs=pairs)
    bot.state["v10_live_sent"] = bot.state["guide_sent"] = True
    bot.state["last_heartbeat"] = spot.now
    bot.cycle(now=spot.now)
    alerts = tg.texts("/100)")
    assert len(calls) == 8 and len(alerts) == 8 and all("looks good" in a for a in alerts)  # 8/min cap
    assert len(bot.state["active_trades"]) == 8 and not tg.texts("AI unavailable")  # other 2 deferred
    gaps = [b - a for a, b in zip(tg.times, tg.times[1:])]
    assert all(g >= 1.0 for g in gaps)


def test_h1_unclear_retried_then_dropped(cfg, tg, monkeypatch, ai_env):
    calls, _ = ai_env("Looks fine to me", "still not sure")
    bot, spot = alert_bot(ai_cfg(cfg), monkeypatch)
    bot.state["last_heartbeat"] = spot.now
    bot.cycle(now=spot.now)
    assert len(calls) == 2 and "EXACTLY" in calls[1]["messages"][0]["content"]
    assert tg.texts("/100)") == [] and bot.state["active_trades"] == []
    assert bot.state["hour_stats"]["ai_unclear"] == 1
    ai_env("Hmm", "APPROVED: ok now")
    bot2, spot2 = alert_bot(ai_cfg(cfg), monkeypatch, pairs=["ETH/USDT"])
    bot2.cycle(now=spot2.now)
    assert len(tg.texts("looks good")) == 1


def test_h1_migration_excludes_unreviewed_trades(cfg, tg, monkeypatch):
    st = wr.empty_state()
    st["active_trades"] = [dict(symbol="TRX/USDT", dir="SHORT", grade="A", entry=1.0, sl=1.01, tp1=0.99, tp2=0.98,
                                entry_time=int(time.time()) - 60, ai="UNAVAILABLE", exchange="BINANCE")]
    st["closed_trades"] = [dict(symbol="ETH/USDT", grade="A", net=1.5, ai="UNAVAILABLE"),
                           dict(symbol="SOL/USDT", grade="B", net=-1.0, ai="APPROVED", ai_note="ok"),
                           dict(symbol="XRP/USDT", grade="A", net=0.5, ai="UNAVAILABLE", ai_note="AI unavailable")]
    st["totals"] = {"A": {"w": 2, "l": 0, "net_sum": 2.0}, "B": {"w": 0, "l": 1, "net_sum": -1.0}}
    spot = FakeSpot(pairs=["TRX/USDT"])
    bot = wr.Bot(ai_cfg(cfg), state=wr.upgrade_state(st), spot=wr.SpotRouter(factories=[("BINANCE", lambda: spot)]),
                 deriv=wr.DerivRouter(sources=[("N", lambda s, c: {})]))
    bot.start()
    assert bot.state["active_trades"][0]["exclude_from_scoreboard"]
    assert bot.state["closed_trades"][0]["exclude_from_scoreboard"] and "exclude_from_scoreboard" not in bot.state["closed_trades"][2]
    assert bot.state["totals"]["A"] == {"w": 1, "l": 0, "net_sum": 0.5} and bot.state["totals"]["B"]["l"] == 1
    t = bot.state["active_trades"][0]
    e_ms = (t["entry_time"] * 1000 // 300_000 + 1) * 300_000
    spot.trade_candles["TRX/USDT"] = [[float(e_ms), 1, 1.02, 0.999, 1.01, 1]]
    spot.now = e_ms / 1000 + 400
    bot.check_trades(spot, {"TRX/USDT": 1.0}, spot.now)
    c = tg.texts("trade finished")[0]
    assert "not AI-checked" in c and bot.state["totals"]["A"]["l"] == 0
