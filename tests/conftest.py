import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import war_room_bot as wr  # noqa: E402

FAKE_TOKEN = "123456789:FAKEtokenFAKEtokenFAKEtoken123"
FAKE_KEY = "FAKEKEY"
FAKE_CHAT = "-1009876543210"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "LLM_FALLBACK_MODELS",
              "STATE_FILE",
              "DRY_RUN", "RUN_MINUTES", "SCAN_INTERVAL", "FEE_PCT_SPOT", "FEE_PCT_FUTURES", "MAX_HOLD_A_HOURS",
              "MAX_HOLD_B_HOURS", "TOP_N_COINS", "USE_DERIV_SCORE", "HEALTHCHECK_URL", "MSG_PREFIX", "TG_MAX_SENDS",
              "GITHUB_ACTIONS", "CHECKPOINT_PUSH", "GITHUB_WORKSPACE"]:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(wr, "TG_GAP_S", 0.0)
    yield


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", FAKE_TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", FAKE_CHAT)
    monkeypatch.setenv("STATE_FILE", str(tmp_path / "state.json"))
    c = wr.Config()
    c.min_tp1_fee_mult = 0  # fake candles have tiny ATR; the fee floor has its own test
    wr.mask_secrets(c)
    return c


@pytest.fixture
def tg(monkeypatch):
    from fakes import TGRecorder
    rec = TGRecorder()
    monkeypatch.setattr(wr, "_http_post", rec)
    return rec
