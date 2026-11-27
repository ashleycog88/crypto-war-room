#!/usr/bin/env python3
"""Crypto War Room bot v10: graded Telegram trade alerts from Binance spot data (runs on GitHub Actions).

Modes: (default) scan loop | --watchdog | --diagnose | --commit-state | --test-messages
Pure functions (indicators, scoring, trade resolution, fees) are importable for the Phase 2 backtest.
"""
import os, re, sys, json, time, html, math, shutil, signal, hashlib, tempfile, argparse, threading, subprocess, traceback
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

try:  # heavy deps are optional so --commit-state / --watchdog still work if pip install failed
    import numpy as np
    import pandas as pd
except ImportError:  # pragma: no cover
    np = pd = None
try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

VERSION = "v10"
TF_MS = {"5m": 300_000, "1h": 3_600_000}
LIMIT_5M, LIMIT_1H, MIN_CLOSED = 310, 260, 200
BINANCE_DATA = "https://data-api.binance.vision"
FALLBACK_EXCHANGES = ["kucoin", "okx", "kraken", "gate", "bybit"]  # ccxt ids ("gate" = Gate.io)
DEFAULT_LLM_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
DEFAULT_LLM_MODEL = "gemini-3.5-flash-lite"
FALLBACK_LLM_MODELS = ["gemini-3.1-flash-lite", "gemini-3.6-flash"]
AI_TIMEOUT_S = 20
AI_BUDGET_S = 30
AI_MAX_PER_MIN = 8
AI_MIN_GAP_S = 4
AI_BACKOFF_S = 300
CYCLE_AI_BUDGET_S = 35  # latest second (from cycle start) at which an AI review may still start
TG_GAP_S = 1.05
TG_LIMIT = 4096
COOLDOWN_S = 3600
A_MIN, B_MIN = 80, 65
SL_ATR, TP1_ATR, TP2_ATR = 1.8, 2.0, 3.6
CHECKPOINT_S = 900
CLOSED_CAP = 1000
WATCHDOG_MAX_AGE_S = 7200
OB_PENALTY = int(float(os.environ.get("OB_PENALTY") or 15))  # points removed when the order book leans >=15% against the side (env OB_PENALTY, 0 = off)
MIN_TP1_FEE_MULT = 3  # TP1 distance must be >= this x round-trip fee (env MIN_TP1_FEE_MULT, 0 = off)
STATE_KEEP_S = 48 * 3600

ORIGINAL_8 = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT", "DOGE/USDT", "AVAX/USDT", "LINK/USDT"]
BUILTIN_COINS = ORIGINAL_8 + [f"{c}/USDT" for c in (
    "ADA TRX TON DOT LTC BCH NEAR SUI APT ARB OP PEPE SHIB UNI ATOM FIL ETC INJ SEI TIA WLD AAVE RENDER FET "
    "HBAR XLM ICP ENA TAO ONDO WIF BONK FLOKI JUP LDO CRV ALGO VET GALA SAND PENDLE TRUMP").split()]
STABLES = set("USDC FDUSD TUSD USDP DAI BUSD USDE USD1 PYUSD RLUSD EURI AEUR USDS FRAX LUSD GUSD SUSD UST PAX "
              "BFUSD XUSD USDD USDJ USDX ZUSD UU U RWUSD".split())
FIAT = set("EUR GBP TRY BRL AUD JPY RUB UAH ZAR NGN ARS PLN RON IDR MXN COP CZK BIDR BVND IDRT".split())
WRAPPED = set("WBTC WETH WBETH BETH WBNB STETH WSTETH CBETH BNSOL WSOL".split())
COMMODITY = set("XAUT PAXG".split())
STOCK_TOKEN_GROUP = "TRD_GRP_261"  # Binance permission group carried only by tokenized stocks/ETFs (NVDAB, QQQB, ...)
LEVERAGED_ROOTS = set("BTC ETH BNB XRP ADA DOT LINK LTC TRX XLM EOS FIL SUSHI UNI AAVE YFI SXP 1INCH BCH XTZ".split())


# ---------------------------------------------------------------- logging / secrets
_SECRETS = []


def register_secret(value):
    v = (value or "").strip()
    if len(v) >= 5 and v not in _SECRETS:
        _SECRETS.append(v)
        _SECRETS.sort(key=len, reverse=True)


def redact(text):
    s = str(text)
    for v in _SECRETS:
        s = s.replace(v, "***")
    return re.sub(r"bot\d{6,15}:[A-Za-z0-9_-]{20,}", "bot***", s)


def log(*parts):
    msg = " ".join(str(p) for p in parts)
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {redact(msg)}", flush=True)


def err_text(e):
    return redact(f"{type(e).__name__}: {e}")[:300]


# ---------------------------------------------------------------- config
def env_str(name, default=""):
    v = os.environ.get(name)
    v = v.strip() if v else ""
    return v or default


def env_num(name, default, cast=float):
    try:
        return cast(env_str(name, str(default)))
    except ValueError:
        return default


def clean_token(raw):
    m = re.search(r"(\d{6,15}:[A-Za-z0-9_-]{25,60})", raw or "")
    return m.group(1) if m else ""


def clean_chat(raw):
    return re.sub(r"[^\d-]", "", raw or "")


class Config:
    def __init__(self, **over):
        raw_tok = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.raw_token = raw_tok.strip()
        self.tg_token = clean_token(raw_tok)
        self.tg_chat = clean_chat(os.environ.get("TELEGRAM_CHAT_ID", ""))
        self.llm_key = env_str("LLM_API_KEY")
        self.llm_url = env_str("LLM_BASE_URL", DEFAULT_LLM_URL)
        self.llm_model = env_str("LLM_MODEL", DEFAULT_LLM_MODEL)
        fb = [m.strip() for m in env_str("LLM_FALLBACK_MODELS").split(",") if m.strip()]
        self.llm_fallbacks = fb or list(FALLBACK_LLM_MODELS)
        self.state_file = env_str("STATE_FILE", "state.json")
        self.dry_run = env_str("DRY_RUN") == "1"
        self.run_minutes = env_num("RUN_MINUTES", 58)
        self.scan_interval = env_num("SCAN_INTERVAL", 60)
        self.fee_spot = env_num("FEE_PCT_SPOT", 0.10)
        self.fee_fut = env_num("FEE_PCT_FUTURES", 0.05)
        self.max_hold_a = env_num("MAX_HOLD_A_HOURS", 24)
        self.max_hold_b = env_num("MAX_HOLD_B_HOURS", 4)
        self.top_n = env_num("TOP_N_COINS", 50, int)
        self.exclude_coins = {c.strip().upper().split("/")[0] for c in env_str("EXCLUDE_COINS").split(",") if c.strip()}
        self.use_deriv = env_str("USE_DERIV_SCORE") == "1"
        self.min_tp1_fee_mult = env_num("MIN_TP1_FEE_MULT", MIN_TP1_FEE_MULT)
        self.healthcheck_url = env_str("HEALTHCHECK_URL")
        self.msg_prefix = env_str("MSG_PREFIX")
        self.tg_max_sends = env_num("TG_MAX_SENDS", 0, int)
        self.repo_dir = env_str("GITHUB_WORKSPACE", os.path.dirname(os.path.abspath(__file__)))
        self.git_branch = env_str("GIT_BRANCH", "main")
        self.checkpoint = env_str("CHECKPOINT_PUSH", "1" if env_str("GITHUB_ACTIONS") == "true" else "0") == "1"
        self.__dict__.update(over)

    def max_hold(self, grade):
        return self.max_hold_a if grade == "A" else self.max_hold_b


def mask_secrets(cfg):
    vals = [cfg.raw_token, cfg.tg_token, cfg.tg_chat, cfg.llm_key, cfg.healthcheck_url]
    for v in vals:
        register_secret(v)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        for v in vals:
            if v and len(v) >= 5:
                print(f"::add-mask::{v}", flush=True)


# ---------------------------------------------------------------- indicators & scoring (pure)
COLS = ["ts", "open", "high", "low", "close", "volume"]


def to_df(rows):
    return pd.DataFrame([r[:6] for r in rows], columns=COLS).astype(float)


def add_indicators(df):
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]
    df["ema9"] = c.ewm(span=9, adjust=False).mean()
    df["ema21"] = c.ewm(span=21, adjust=False).mean()
    df["ema50"] = c.ewm(span=50, adjust=False).mean()
    d = c.diff()
    gain = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-d.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean().replace(0, np.nan)
    df["rsi"] = (100 - (100 / (1 + (gain / loss)))).fillna(100.0)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    df["atr"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    v_sum = v.rolling(24).sum().replace(0, np.nan)
    df["vwap"] = (((h + l + c) / 3) * v).rolling(24).sum() / v_sum
    df["vol_r"] = (v / v.rolling(20).mean().replace(0, np.nan)).fillna(1.0)
    return df


def validate_candles(rows, tf, now_ms, min_closed=MIN_CLOSED):
    """Return (closed_rows, None) or (None, reason). The still-forming candle is dropped."""
    if not rows:
        return None, "no candles"
    try:
        arr = np.array([r[:6] for r in rows], dtype=float)
    except (TypeError, ValueError):
        return None, "bad data (non-numeric)"
    if arr.ndim != 2 or arr.shape[1] < 6:
        return None, "bad data (shape)"
    if not np.isfinite(arr).all() or (arr[:, 1:5] <= 0).any():
        return None, "bad data (NaN/zero price)"
    arr = arr[np.argsort(arr[:, 0], kind="stable")]
    if len(np.unique(arr[:, 0])) != len(arr):
        return None, "duplicate candles"
    tf_ms = TF_MS[tf]
    if arr[-1, 0] < now_ms - 2 * tf_ms:
        return None, "stale candles"
    closed = arr[arr[:, 0] + tf_ms <= now_ms]
    if len(closed) < min_closed:
        return None, f"insufficient candles ({len(closed)} closed)"
    return closed.tolist(), None


def ob_imbalance(ob, depth=20):
    bv = sum(float(b[1]) for b in ob.get("bids", [])[:depth])
    av = sum(float(a[1]) for a in ob.get("asks", [])[:depth])
    return (bv - av) / (bv + av) if (bv + av) > 0 else 0.0


def score_sides(r5, r1, ob_imb, ob_penalty=None):
    """Base score (weights 25/25/20/15/15) from the last CLOSED 5m row r5 and 1h row r1 (dict-like).
    An order book leaning >=15% against a side costs that side ob_penalty points (floor 0)."""
    pen = OB_PENALTY if ob_penalty is None else ob_penalty
    ls = ss = 0
    rl, rs = [], []
    if r1["close"] > r1["ema21"] > r1["ema50"]:
        ls += 25; rl.append("1H Bull EMA")
    elif r1["close"] < r1["ema21"] < r1["ema50"]:
        ss += 25; rs.append("1H Bear EMA")
    vw = r5["vwap"]
    if vw is not None and not pd.isna(vw):
        if r5["ema9"] > r5["ema21"] and r5["close"] > vw:
            ls += 25; rl.append("5M EMA+VWAP Bull")
        elif r5["ema9"] < r5["ema21"] and r5["close"] < vw:
            ss += 25; rs.append("5M EMA+VWAP Bear")
    rsi5, rsi1h = float(r5["rsi"]), float(r1["rsi"])
    if 52 <= rsi5 <= 68 and rsi1h > 50:
        ls += 20; rl.append(f"RSI Bull {rsi5:.1f}")
    elif 32 <= rsi5 <= 48 and rsi1h < 50:
        ss += 20; rs.append(f"RSI Bear {rsi5:.1f}")
    vol_r = float(r5["vol_r"])
    if vol_r >= 1.35:
        ls += 15; ss += 15
        rl.append(f"Vol {vol_r:.2f}x"); rs.append(f"Vol {vol_r:.2f}x")
    if ob_imb >= 0.15:
        ls += 15; rl.append(f"Bid Wall +{ob_imb * 100:.0f}%")
    elif ob_imb <= -0.15:
        ss += 15; rs.append(f"Ask Wall {ob_imb * 100:.0f}%")
    if pen and ob_imb >= 0.15:
        ss = max(0, ss - pen); rs.append("⚠️ order book against this trade")
    elif pen and ob_imb <= -0.15:
        ls = max(0, ls - pen); rl.append("⚠️ order book against this trade")
    return ls, ss, rl, rs


def grade_for(score):
    if score >= A_MIN:
        return "A"
    if score >= B_MIN:
        return "B"
    return None


def btc_regime(df1h):
    if df1h is None or len(df1h) == 0:
        return "n/a"
    r = df1h.iloc[-1]
    if r["close"] > r["ema21"] > r["ema50"]:
        return "up"
    if r["close"] < r["ema21"] < r["ema50"]:
        return "down"
    return "flat"


def deriv_adjustment(direction, funding8h, oi_chg, px_chg_1h, regime):
    """Proposed derivatives adjustment (used only when USE_DERIV_SCORE=1). Range -10..+10."""
    adj = 0
    if funding8h is not None:
        if direction == "LONG" and funding8h <= 0:
            adj += 3
        if direction == "SHORT" and funding8h >= 0.0001:
            adj += 3
    if oi_chg is not None and px_chg_1h is not None and oi_chg >= 2.0:
        if (direction == "LONG" and px_chg_1h > 0) or (direction == "SHORT" and px_chg_1h < 0):
            adj += 4
    want = "up" if direction == "LONG" else "down"
    against = "down" if direction == "LONG" else "up"
    if regime == want:
        adj += 3
    elif regime == against:
        return -10
    return min(adj, 10)


def is_pegged(price, df1h):
    """Stable-like coin: price within 0.97-1.03 and the last 24 closed 1h candles span < 1.5%."""
    if not (0.97 <= price <= 1.03) or df1h is None or len(df1h) < 24:
        return False
    w = df1h.iloc[-24:]
    return (float(w["high"].max()) - float(w["low"].min())) / price * 100 < 1.5


def tp1_too_small(direction, entry, tp1, fee_spot, fee_fut, mult=None):
    m = MIN_TP1_FEE_MULT if mult is None else mult
    return bool(m) and abs(tp1 - entry) / entry * 100 < m * fee_round_trip(direction, fee_spot, fee_fut) - 1e-9


def sig(x, digits=8):
    return float(f"{x:.{digits}g}")


def make_levels(direction, entry, atr):
    s = 1 if direction == "LONG" else -1
    return sig(entry - s * SL_ATR * atr), sig(entry + s * TP1_ATR * atr), sig(entry + s * TP2_ATR * atr)


def evaluate_pair(df5, df1h, ob_imb, price, funding8h=None, oi_chg=None, regime="n/a", use_deriv=False):
    """Score one pair from CLOSED-candle indicator frames. Returns a result dict or None."""
    r5, r1 = df5.iloc[-1], df1h.iloc[-1]
    atr = float(r5["atr"])
    if not price or price <= 0 or pd.isna(atr) or atr <= 0:
        return None
    ls, ss, rl, rs = score_sides(r5, r1, ob_imb)
    px_chg = None
    if len(df1h) >= 2:
        px_chg = float(r1["close"] / df1h.iloc[-2]["close"] - 1) * 100
    adj_l = deriv_adjustment("LONG", funding8h, oi_chg, px_chg, regime)
    adj_s = deriv_adjustment("SHORT", funding8h, oi_chg, px_chg, regime)
    fl, fs = (max(0, min(100, ls + adj_l)), max(0, min(100, ss + adj_s))) if use_deriv else (ls, ss)
    if fl > fs:
        direction, score, base, adj, reasons = "LONG", fl, ls, adj_l, rl
    elif fs > fl:
        direction, score, base, adj, reasons = "SHORT", fs, ss, adj_s, rs
    else:
        direction, score, base, adj, reasons = None, fl, ls, 0, []
    out = {"dir": direction, "score": int(score), "base_score": int(base), "deriv_adj": int(adj),
           "grade": grade_for(score) if direction else None, "reasons": reasons,
           "price": sig(price), "atr": float(atr), "rsi5": round(float(r5["rsi"]), 1),
           "rsi1h": round(float(r1["rsi"]), 1), "vol": round(float(r5["vol_r"]), 2),
           "ob": round(ob_imb * 100, 1), "candle_ts": int(r5["ts"]), "funding8h": funding8h,
           "oi_chg_1h": oi_chg, "btc_regime": regime}
    if direction:
        out["sl"], out["tp1"], out["tp2"] = make_levels(direction, price, atr)
    return out


# ---------------------------------------------------------------- trade resolution & fees (pure)
def pct_move(direction, entry, price):
    return (price - entry) / entry * 100 if direction == "LONG" else (entry - price) / entry * 100


def fee_round_trip(direction, fee_spot=0.10, fee_fut=0.05):
    return 2 * fee_spot if direction == "LONG" else 2 * fee_fut


def net_pnl(direction, gross, fee_spot=0.10, fee_fut=0.05):
    return gross - fee_round_trip(direction, fee_spot, fee_fut)


def _close(t, reason, exit_px, gross, now_s, fee_spot, fee_fut):
    net = net_pnl(t["dir"], gross, fee_spot, fee_fut)
    t.update({"status": "CLOSED", "exit_reason": reason, "exit": sig(exit_px), "exit_time": int(now_s),
              "gross": round(gross, 4), "net": round(net, 4), "win": net > 0})
    return {"type": "CLOSE", "reason": reason, "exit": exit_px, "gross": gross, "net": net, "win": net > 0}


def _bar(t, hi, lo, now_s, fee_spot, fee_fut, events):
    """Apply one price bar. SL is assumed first when a bar touches SL and a TP; after TP1 the stop is entry."""
    d, e = t["dir"], t["entry"]
    long_ = d == "LONG"
    hit = (lambda lvl: hi >= lvl) if long_ else (lambda lvl: lo <= lvl)
    stop = (lambda lvl: lo <= lvl) if long_ else (lambda lvl: hi >= lvl)
    p1, p2 = pct_move(d, e, t["tp1"]), pct_move(d, e, t["tp2"])
    if not t.get("tp1_hit"):
        if stop(t["sl"]):
            events.append(_close(t, "STOP LOSS", t["sl"], pct_move(d, e, t["sl"]), now_s, fee_spot, fee_fut))
            return True
        if not hit(t["tp1"]):
            return False
        t["tp1_hit"], t["tp1_time"] = True, int(now_s)
        events.append({"type": "TP1", "price": t["tp1"], "gross_half": p1})
    if stop(e):
        events.append(_close(t, "BREAKEVEN after TP1", e, 0.5 * p1, now_s, fee_spot, fee_fut))
        return True
    if hit(t["tp2"]):
        events.append(_close(t, "TP2 HIT", t["tp2"], 0.5 * p1 + 0.5 * p2, now_s, fee_spot, fee_fut))
        return True
    return False


def resolve_trade(t, candles, live_price, now_s, max_hold_h, fee_spot=0.10, fee_fut=0.05, tf_ms=TF_MS["5m"]):
    """Advance an open trade using 5m candles opened at/after entry plus the live price. Mutates t; returns events."""
    events = []
    entry_ms = int(t["entry_time"] * 1000)
    deadline = t["entry_time"] + max_hold_h * 3600
    now_ms = now_s * 1000
    last_close = None
    for r in sorted(candles, key=lambda x: x[0]):
        ts = r[0]
        if ts < entry_ms or ts <= t.get("checked_until", 0) or ts / 1000 >= deadline:
            continue
        if _bar(t, float(r[2]), float(r[3]), now_s, fee_spot, fee_fut, events):
            return events
        if ts + tf_ms <= now_ms:
            t["checked_until"] = int(ts)
        last_close = float(r[4])
    if live_price and now_s < deadline:
        if _bar(t, live_price, live_price, now_s, fee_spot, fee_fut, events):
            return events
    if now_s >= deadline:
        px = last_close if (now_s - deadline > 600 and last_close) else live_price
        if not px:
            return events
        p_now = pct_move(t["dir"], t["entry"], px)
        gross = 0.5 * pct_move(t["dir"], t["entry"], t["tp1"]) + 0.5 * p_now if t.get("tp1_hit") else p_now
        events.append(_close(t, "TIME EXIT", px, gross, now_s, fee_spot, fee_fut))
    return events


def recompute_totals(closed):
    tot = {g: {"w": 0, "l": 0, "net_sum": 0.0} for g in ("A", "B")}
    for t in closed:
        if isinstance(t, dict) and "net" in t and t.get("grade") in tot and not t.get("exclude_from_scoreboard") \
                and not t.get("legacy"):
            tot[t["grade"]]["w" if t["net"] > 0 else "l"] += 1
            tot[t["grade"]]["net_sum"] = round(tot[t["grade"]]["net_sum"] + t["net"], 4)
    return tot


def record_line(totals):
    parts = []
    for g in ("A", "B"):
        w, l = totals[g]["w"], totals[g]["l"]
        pct = f"{w / (w + l) * 100:.0f}%" if (w + l) else "–"
        parts.append(f"{g}: {w}W/{l}L ({pct})")
    return " | ".join(parts)


# ---------------------------------------------------------------- coin universe (pure)
def is_excluded_base(base, extra=()):
    b = base.upper()
    if b in STABLES or b in FIAT or b in WRAPPED or b in COMMODITY or b in extra:
        return True
    if b.startswith("USD") and len(b) <= 5:
        return True
    return any(b.endswith(s) and b[:-len(s)] in LEVERAGED_ROOTS for s in ("UP", "DOWN", "BULL", "BEAR"))


def build_coin_list(tickers, n=50, tradable=None, exclude_symbols=(), extra_bases=()):
    rows = []
    for t in tickers or []:
        s = t.get("symbol", "")
        if not s.endswith("USDT") or (tradable is not None and s not in tradable) or s in exclude_symbols:
            continue
        base = s[:-4]
        if not base or is_excluded_base(base, extra_bases):
            continue
        try:
            qv = float(t.get("quoteVolume") or 0)
        except ValueError:
            continue
        if qv > 0:
            rows.append((qv, base))
    rows.sort(key=lambda x: -x[0])
    return [f"{b}/USDT" for _, b in rows[:n]]


def norm_base(b):
    b = b.split("/")[0].upper()
    if b == "XBT":
        return "BTC"
    m = re.match(r"^(1000000|100000|10000|1000|1M)([A-Z][A-Z0-9]*)$", b)
    return m.group(2) if m else b


# ---------------------------------------------------------------- state
def empty_state():
    return {"version": 10, "active_trades": [], "closed_trades": [], "last_alert_time": {},
            "last_alert_candle": {}, "ai_rejected_candle": {}, "booted": True, "v10_live_sent": False,
            "totals": {"A": {"w": 0, "l": 0, "net_sum": 0.0}, "B": {"w": 0, "l": 0, "net_sum": 0.0}},
            "coin_list": [], "coin_list_time": 0, "oi_snap": {}, "last_cycle_time": 0, "last_heartbeat": 0,
            "cycle_times": [], "near_miss": {}, "hour_stats": {"alerts": 0, "ai_rejected": 0}, "outbox": []}


def upgrade_state(raw, now=None):
    now = int(now or time.time())
    st = empty_state()
    for k, v in raw.items():
        st[k] = v
    for k, v in empty_state().items():
        if not isinstance(st.get(k), type(v)):
            st[k] = v
    for g in ("A", "B"):
        st["totals"].setdefault(g, {"w": 0, "l": 0, "net_sum": 0.0})
    trades = []
    for t in st["active_trades"]:
        if not isinstance(t, dict) or not {"symbol", "dir", "entry", "sl", "tp1", "tp2"} <= set(t):
            continue
        if "entry_time" not in t:
            t["entry_time"] = now
            t["legacy"] = True
        if t.get("grade") not in ("A", "B"):
            t["grade"] = grade_for(t["score"]) if isinstance(t.get("score"), (int, float)) else None
            t["grade"] = t["grade"] or "B"
        t.setdefault("score", None)
        t.setdefault("exchange", "UNKNOWN")
        t.setdefault("tp1_hit", False)
        t.setdefault("checked_until", 0)
        t.setdefault("status", "OPEN")
        t.setdefault("id", f"{t['symbol']}-{t['entry_time']}")
        for k in ("entry", "sl", "tp1", "tp2"):
            t[k] = float(t[k])
        trades.append(t)
    st["active_trades"] = trades
    for t in st["closed_trades"]:
        if isinstance(t, dict) and "net" not in t:
            t["legacy"] = True
    st["last_alert_time"] = {k: int(v) for k, v in st["last_alert_time"].items() if isinstance(v, (int, float))}
    st["closed_trades"] = st["closed_trades"][-CLOSED_CAP:]
    st["version"] = 10
    return st


def load_state(path, now=None):
    if not os.path.exists(path):
        return empty_state()
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            raise ValueError("state is not a JSON object")
        return upgrade_state(raw, now)
    except Exception as e:
        backup = f"{path}.corrupt-{int(time.time())}"
        try:
            shutil.copy2(path, backup)
        except OSError:
            backup = "(backup failed)"
        log(f"state file unreadable ({err_text(e)}); backup kept at {backup}; starting empty")
        st = empty_state()
        st["outbox"].append({"text": "⚠️ <b>state.json was unreadable</b> — bot started from an empty state "
                                     "(a backup copy was kept on the runner).", "tries": 0})
        return st


def save_state(path, state):
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".state-", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=1, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        try:
            dfd = os.open(d, os.O_RDONLY)
            os.fsync(dfd)
            os.close(dfd)
        except OSError:
            pass
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# ---------------------------------------------------------------- telegram
def _http_post(url, payload, timeout):
    return requests.post(url, json=payload, timeout=timeout)


def _http_get(url, timeout, params=None):
    return requests.get(url, timeout=timeout, params=params)


_sleep = time.sleep


def esc(v):
    return html.escape(str(v), quote=False)


def truncate_html(text, limit=TG_LIMIT):
    if len(text) <= limit:
        return text
    cut = text[:limit - 40]
    lt = cut.rfind("<")
    if lt > cut.rfind(">"):
        cut = cut[:lt]
    amp = cut.rfind("&")
    if amp != -1 and ";" not in cut[amp:]:
        cut = cut[:amp]
    stack = []
    for m in re.finditer(r"<(/?)([a-zA-Z]+)[^>]*>", cut):
        name = m.group(2).lower()
        if m.group(1):
            if stack and stack[-1] == name:
                stack.pop()
        else:
            stack.append(name)
    out = cut + "…" + "".join(f"</{t}>" for t in reversed(stack))
    return out[:limit]


def html_to_plain(text):
    return html.unescape(re.sub(r"<[^>]+>", "", text))[:TG_LIMIT]


class Telegram:
    def __init__(self, cfg):
        self.cfg = cfg
        self.last_send = 0.0
        self.sent = 0
        self.lock = threading.Lock()
        self.last_status = None

    def _once(self, text, html_mode):
        with self.lock:
            gap = self.last_send + TG_GAP_S - time.time()
            if gap > 0:
                _sleep(gap)
            payload = {"chat_id": self.cfg.tg_chat, "text": text, "disable_web_page_preview": True}
            if html_mode:
                payload["parse_mode"] = "HTML"
            try:
                r = _http_post(f"https://api.telegram.org/bot{self.cfg.tg_token}/sendMessage", payload, 15)
                self.last_send = time.time()
                self.sent += 1
                try:
                    body = r.json()
                except ValueError:
                    body = {}
                ok = r.status_code == 200 and bool(body.get("ok", True))
                desc = str(body.get("description", ""))[:150]
                ra = (body.get("parameters") or {}).get("retry_after")
                self.last_status = r.status_code
                log(f"Telegram sendMessage HTTP {r.status_code}{' ok' if ok else ' ' + desc}")
                return ok, r.status_code, desc, ra
            except Exception as e:
                self.last_send = time.time()
                self.last_status = "network error"
                log(f"Telegram send failed: {type(e).__name__}")
                return False, None, "", None

    def send(self, text, html_mode=True):
        if self.cfg.msg_prefix:
            text = f"🧪 <b>{esc(self.cfg.msg_prefix)}</b> — " + text
        text = truncate_html(text)
        if self.cfg.dry_run:
            print("----- [DRY_RUN] Telegram message -----\n" + redact(text) + "\n--------------------------------------",
                  flush=True)
            return True
        if not self.cfg.tg_token or not self.cfg.tg_chat:
            log("Telegram not configured (token or chat id missing): message not sent")
            return False
        if self.cfg.tg_max_sends and self.sent >= self.cfg.tg_max_sends:
            log("Telegram send cap (TG_MAX_SENDS) reached: message not sent")
            return False
        ok, status, desc, ra = self._once(text, html_mode)
        if ok:
            return True
        if status == 429:
            _sleep(min(float(ra or 3), 60))
            ok, status, desc, ra = self._once(text, html_mode)
            if ok:
                return True
        if status == 400 and html_mode and ("pars" in desc.lower() or "entit" in desc.lower() or "tag" in desc.lower()):
            ok, _, _, _ = self._once(html_to_plain(text), False)
        return ok


# ---------------------------------------------------------------- AI review
def _make_openai_client(api_key, base_url, timeout):
    from openai import OpenAI
    return OpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=0)


def parse_ai_reply(txt):
    clean = (txt or "").strip()
    head = re.sub(r"^[\s*_#`>\"'\-\[\(]+", "", clean).upper()
    verdict = "REJECTED" if head.startswith("REJECTED") else "APPROVED" if head.startswith("APPROVED") else "UNCLEAR"
    note = re.sub(r"[*_`#]+", "", clean).strip()
    return verdict, note


def build_ai_prompt(s):
    keys = ["symbol", "dir", "grade", "score", "price", "sl", "tp1", "tp2", "rsi5", "rsi1h", "vol", "ob", "reasons",
            "funding8h", "oi_chg_1h", "btc_regime"]
    data = {k: s.get(k) for k in keys}
    return ("You are a strict crypto futures risk reviewer. Review this intraday setup "
            f"(5m signals, 1h trend, ATR stops, max hold {s.get('max_hold_h', '?')}h): {json.dumps(data)}. "
            "Reply with ONE line that starts with APPROVED: or REJECTED: followed by one concise risk sentence.")


def _call_with_timeout(fn, timeout):
    box = {}

    def run():
        try:
            box["v"] = fn()
        except BaseException as e:
            box["e"] = e
    th = threading.Thread(target=run, daemon=True)
    th.start()
    th.join(timeout)
    if th.is_alive():
        raise TimeoutError(f"AI call exceeded {timeout:.0f}s")
    if "e" in box:
        raise box["e"]
    return box.get("v")


def _status_code(e):
    return getattr(e, "status_code", None) or getattr(getattr(e, "response", None), "status_code", None)


class AIReviewer:
    def __init__(self, cfg):
        self.cfg = cfg
        self.reasoning_ok = True
        self.backoff_until = 0.0
        self.calls = []
        self.stats = {"calls": 0, "ok": 0, "rejected": 0, "failed": 0}
        self.last = "no key" if not cfg.llm_key else "not used yet"

    def status(self):
        if not self.cfg.llm_key:
            return "unavailable (no key)"
        if time.time() < self.backoff_until:
            return "rate-limited, pausing"
        s = self.stats
        return f"{self.last} (calls {s['calls']}, ok {s['ok']}, rejected {s['rejected']}, failed {s['failed']})"

    def _call(self, model, prompt, timeout):
        client = _make_openai_client(self.cfg.llm_key, self.cfg.llm_url, timeout)
        kw = {"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": 512,
              "temperature": 0.2}
        if self.reasoning_ok:
            kw["reasoning_effort"] = "low"
        self.calls.append(time.time())
        self.stats["calls"] += 1
        try:
            res = _call_with_timeout(lambda: client.chat.completions.create(**kw), timeout)
        except Exception as e:
            if "reasoning_effort" in kw and _status_code(e) in (400, 422) and "reason" in str(e).lower():
                self.reasoning_ok = False
                kw.pop("reasoning_effort")
                res = _call_with_timeout(lambda: client.chat.completions.create(**kw), timeout)
            else:
                raise
        if not res or not getattr(res, "choices", None):
            return ""
        return (res.choices[0].message.content or "").strip()

    def throttle_wait(self, now):
        """Seconds until the bot's own AI throttle allows the next call (0 = now)."""
        self.calls = [t for t in self.calls if now - t < 60]
        wait = 0.0
        if self.calls:
            wait = max(wait, self.calls[-1] + AI_MIN_GAP_S - now)
        if len(self.calls) >= AI_MAX_PER_MIN:
            wait = max(wait, self.calls[-AI_MAX_PER_MIN] + 60 - now)
        return max(0.0, wait)

    def review(self, setup, start_by=None):
        """Return (verdict, note). verdict: APPROVED | REJECTED | UNCLEAR | UNAVAILABLE | DEFER.
        The bot's own throttle never skips a review: it waits for the next slot, or returns DEFER when that
        slot is later than start_by (the caller retries next cycle)."""
        if not self.cfg.llm_key:
            return "UNAVAILABLE", "AI unavailable (no key)"
        now = time.time()
        if now < self.backoff_until:
            return "UNAVAILABLE", "AI unavailable (rate-limited, pausing)"
        wait = self.throttle_wait(now)
        if wait > 0:
            if start_by is not None and now + wait > start_by:
                return "DEFER", "waiting for an AI slot"
            _sleep(wait)
            now = time.time()
        deadline = now + AI_BUDGET_S
        prompt = build_ai_prompt(setup)
        rate_limited = False
        models = list(dict.fromkeys([self.cfg.llm_model] + list(self.cfg.llm_fallbacks)))
        for model in models:
            remaining = deadline - time.time()
            if remaining < 1:
                break
            try:
                txt = self._call(model, prompt, min(AI_TIMEOUT_S, remaining))
                if txt and parse_ai_reply(txt)[0] == "UNCLEAR" and deadline - time.time() > 1:
                    txt = self._call(model, prompt + " Reply with EXACTLY 'APPROVED: <reason>' or 'REJECTED: "
                                     "<reason>' and nothing else.", min(AI_TIMEOUT_S, deadline - time.time())) or txt
                if txt:
                    verdict, note = parse_ai_reply(txt)
                    self.stats["ok"] += 1
                    self.stats["rejected"] += verdict == "REJECTED"
                    self.last = f"ok ({model})"
                    return verdict, note[:600]
                self.last = f"empty reply ({model})"
                log(f"AI {model}: empty reply")
            except Exception as e:
                rate_limited |= _status_code(e) == 429 or "RateLimit" in type(e).__name__
                self.last = f"error {type(e).__name__}"
                log(f"AI {model} failed: {err_text(e)}")
        self.stats["failed"] += 1
        if rate_limited:
            self.backoff_until = time.time() + AI_BACKOFF_S
        return "UNAVAILABLE", "AI unavailable"


# ---------------------------------------------------------------- spot data sources
class RateLimited(Exception):
    pass


class BinanceSpot:
    name = "BINANCE"

    def __init__(self, base=BINANCE_DATA):
        self.base = base
        self.s = requests.Session()
        ad = requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=16)
        self.s.mount("https://", ad)
        self.markets = set()
        self.stock_tokens = set()
        self.used_weight = 0
        self.max_weight = 0
        self.pause_until = 0.0
        self.http_429 = 0
        self.http_451 = 0

    def _get(self, path, params=None):
        wait = self.pause_until - time.time()
        if wait > 0:
            time.sleep(min(wait, 30))
        r = self.s.get(self.base + path, params=params, timeout=10)
        w = r.headers.get("x-mbx-used-weight-1m")
        if w and w.isdigit():
            self.used_weight = int(w)
            self.max_weight = max(self.max_weight, self.used_weight)
            if self.used_weight > 4800:
                self.pause_until = (time.time() // 60 + 1) * 60 + 1
        if r.status_code in (418, 429):
            self.http_429 += 1
            ra = int(r.headers.get("Retry-After", "60") or 60)
            self.pause_until = time.time() + ra
            raise RateLimited(f"Binance HTTP {r.status_code}, retry after {ra}s")
        if r.status_code == 451:
            self.http_451 += 1
        r.raise_for_status()
        return r.json()

    def connect(self):
        info = self._get("/api/v3/exchangeInfo", {"permissions": "SPOT", "symbolStatus": "TRADING"})
        syms = [s for s in info.get("symbols", []) if s.get("status") == "TRADING" and s.get("quoteAsset") == "USDT"]
        self.markets = {s["symbol"] for s in syms}
        self.stock_tokens = {s["symbol"] for s in syms
                             if any(STOCK_TOKEN_GROUP in grp for grp in s.get("permissionSets") or [])}
        if "BTCUSDT" not in self.markets:
            raise RuntimeError("BTCUSDT not listed")

        try:
            t0 = time.time()
            server = self._get("/api/v3/time")["serverTime"]
            self.time_offset_ms = int(server - (t0 + time.time()) / 2 * 1000)
        except Exception:
            self.time_offset_ms = 0

    def has(self, sym):
        return sym.replace("/", "") in self.markets

    def ohlcv(self, sym, tf, limit, since=None):
        p = {"symbol": sym.replace("/", ""), "interval": tf, "limit": min(int(limit), 1000)}
        if since:
            p["startTime"] = int(since)
        return [[float(x) for x in r[:6]] for r in self._get("/api/v3/klines", p)]

    def order_book(self, sym, limit=20):
        d = self._get("/api/v3/depth", {"symbol": sym.replace("/", ""), "limit": limit})
        return {"bids": d["bids"], "asks": d["asks"]}

    def prices(self, syms):
        m = {x["symbol"]: float(x["price"]) for x in self._get("/api/v3/ticker/price")}
        return {s: m[s.replace("/", "")] for s in syms if s.replace("/", "") in m}

    def tickers_24h(self):
        return self._get("/api/v3/ticker/24hr", {"type": "MINI"})


class CcxtSpot:
    def __init__(self, exid):
        import ccxt
        self.name = exid.upper()
        self.ex = getattr(ccxt, exid)({"enableRateLimit": True, "timeout": 10000})
        self.http_429 = 0
        self.used_weight = None
        self.max_weight = None

    def connect(self):
        self.ex.load_markets()
        if "BTC/USDT" not in self.ex.markets:
            raise RuntimeError("BTC/USDT not listed")

    def has(self, sym):
        m = (self.ex.markets or {}).get(sym)
        return bool(m) and m.get("spot", True) and m.get("active") is not False

    def ohlcv(self, sym, tf, limit, since=None):
        return [[float(x) for x in r[:6]] for r in self.ex.fetch_ohlcv(sym, tf, since=since, limit=int(limit))]

    def order_book(self, sym, limit=20):
        return self.ex.fetch_order_book(sym, limit=limit)

    def prices(self, syms):
        try:
            t = self.ex.fetch_tickers(syms)
        except Exception:
            t = {s: self._safe_ticker(s) for s in syms}
        return {s: float(t[s]["last"]) for s in syms if t.get(s) and t[s].get("last")}

    def _safe_ticker(self, s):
        try:
            return self.ex.fetch_ticker(s)
        except Exception:
            return None

    def tickers_24h(self):
        return None


def default_spot_factories():
    return [("BINANCE", BinanceSpot)] + [(x.upper(), (lambda x=x: CcxtSpot(x))) for x in FALLBACK_EXCHANGES]


class SpotRouter:
    def __init__(self, factories=None):
        self.factories = factories or default_spot_factories()
        self.src = None
        self.last_primary_try = time.time()

    def select(self):
        for name, f in self.factories:
            try:
                s = f()
                s.connect()
                self.src = s
                log(f"Spot data source: {name}")
                return s
            except Exception as e:
                log(f"Spot source {name} unavailable: {err_text(e)}")
        self.src = None
        return None

    def maybe_return_to_primary(self):
        if not self.src or self.src.name == self.factories[0][0] or time.time() - self.last_primary_try < 600:
            return
        self.last_primary_try = time.time()
        try:
            s = self.factories[0][1]()
            s.connect()
            self.src = s
            log(f"Back on primary spot source {s.name}")
        except Exception as e:
            log(f"Primary spot source still unavailable: {err_text(e)}")


# ---------------------------------------------------------------- derivatives sources
def _f(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def deriv_hyperliquid(s, coins):
    meta, ctxs = s.post("https://api.hyperliquid.xyz/info", json={"type": "metaAndAssetCtxs"}, timeout=10).json()
    data = {}
    for u, c in zip(meta["universe"], ctxs):
        if u.get("isDelisted"):
            continue
        name = u["name"]
        if re.match(r"^k[A-Z0-9]+$", name):
            name = name[1:]
        fr = _f(c.get("funding"))
        data[norm_base(name)] = {"funding8h": fr * 8 if fr is not None else None, "oi": _f(c.get("openInterest"))}
    return data


def deriv_okx(s, coins):
    fr = s.get("https://www.okx.com/api/v5/public/funding-rate", params={"instId": "ANY"}, timeout=10).json()["data"]
    oi = s.get("https://www.okx.com/api/v5/public/open-interest", params={"instType": "SWAP"}, timeout=10).json()["data"]
    data = {}
    for x in fr:
        if not x.get("instId", "").endswith("-USDT-SWAP"):
            continue
        rate = _f(x.get("fundingRate"))
        try:
            hrs = (int(x["nextFundingTime"]) - int(x["fundingTime"])) / 3.6e6 or 8
        except (KeyError, ValueError, TypeError):
            hrs = 8
        data[norm_base(x["instId"].split("-")[0])] = {"funding8h": rate * 8 / hrs if rate is not None else None,
                                                       "oi": None}
    for x in oi:
        if x.get("instId", "").endswith("-USDT-SWAP"):
            b = norm_base(x["instId"].split("-")[0])
            if b in data:
                data[b]["oi"] = _f(x.get("oiCcy"))
    return data


def deriv_gate(s, coins):
    data = {}
    for c in s.get("https://api.gateio.ws/api/v4/futures/usdt/contracts", timeout=15).json():
        if c.get("in_delisting") or not c.get("name", "").endswith("_USDT"):
            continue
        rate, iv = _f(c.get("funding_rate")), _f(c.get("funding_interval")) or 28800
        ps, mult = _f(c.get("position_size")), _f(c.get("quanto_multiplier")) or 1
        data[norm_base(c["name"][:-5])] = {"funding8h": rate * 28800 / iv if rate is not None else None,
                                           "oi": ps * mult if ps is not None else None}
    return data


def deriv_kucoin(s, coins):
    data = {}
    for c in s.get("https://api-futures.kucoin.com/api/v1/contracts/active", timeout=15).json()["data"]:
        if not c.get("symbol", "").endswith("USDTM") or c.get("status") != "Open":
            continue
        rate, gran = _f(c.get("fundingFeeRate")), _f(c.get("fundingRateGranularity")) or 28_800_000
        oi, mult = _f(c.get("openInterest")), _f(c.get("multiplier")) or 1
        data[norm_base(c.get("baseCurrency", ""))] = {"funding8h": rate * 28_800_000 / gran if rate is not None else None,
                                                      "oi": oi * mult if oi is not None else None}
    return data


def deriv_binance_fapi(s, coins):
    r = s.get("https://fapi.binance.com/fapi/v1/premiumIndex", timeout=10)
    r.raise_for_status()
    data, sym_of = {}, {}
    for x in r.json():
        sym = x.get("symbol", "")
        if sym.endswith("USDT"):
            b = norm_base(sym[:-4])
            data[b] = {"funding8h": _f(x.get("lastFundingRate")), "oi": None}
            sym_of[b] = sym

    def oi(b):
        try:
            return b, _f(s.get("https://fapi.binance.com/fapi/v1/openInterest", params={"symbol": sym_of[b]},
                               timeout=8).json().get("openInterest"))
        except Exception:
            return b, None
    with ThreadPoolExecutor(6) as p:
        for b, v in p.map(oi, [c for c in coins if c in sym_of]):
            data[b]["oi"] = v
    return data


DERIV_SOURCES = [("HYPERLIQUID", deriv_hyperliquid), ("OKX", deriv_okx), ("GATEIO", deriv_gate),
                 ("KUCOIN_FUTURES", deriv_kucoin), ("BINANCE_FUTURES", deriv_binance_fapi)]


class DerivRouter:
    def __init__(self, sources=None, session=None):
        self.sources = sources or DERIV_SOURCES
        self.s = session or requests.Session()
        self.primary = None
        self.universes = {}
        self.status = {}
        self.sec_cache = {}

    def _fetch(self, name, fn, coins):
        try:
            data = fn(self.s, coins)
            if not data:
                raise RuntimeError("empty response")
            self.universes[name] = set(data)
            self.status[name] = f"ok ({len(data)} markets)"
            return data
        except Exception as e:
            self.status[name] = f"fail: {err_text(e)[:120]}"
            return None

    def probe(self, coins):
        with ThreadPoolExecutor(len(self.sources)) as p:
            res = list(p.map(lambda nf: (nf[0], self._fetch(nf[0], nf[1], coins)), self.sources))
        for name, data in res:
            if data:
                self.sec_cache[name] = (time.time(), data)
        log("Derivatives probe: " + "; ".join(f"{n} {self.status.get(n)}" for n, _ in self.sources))

    def fetch(self, coins):
        order = sorted(self.sources, key=lambda nf: nf[0] != self.primary)
        result, used, fetched = {}, None, 0
        for name, fn in order:
            if used and all(c in result for c in coins):
                break
            if fetched >= 3:
                break
            cached = self.sec_cache.get(name)
            if used and cached and time.time() - cached[0] < 300:
                data = cached[1]
            else:
                data = self._fetch(name, fn, coins)
                fetched += 1
                if not data:
                    if name == self.primary:
                        log(f"Derivatives source {name} failed; trying next")
                        self.primary = None
                    continue
                self.sec_cache[name] = (time.time(), data)
            if used is None:
                used = name
                if self.primary is None:
                    self.primary = name
                    log(f"Derivatives source in use: {name}")
            for c in coins:
                if c not in result and c in data:
                    result[c] = dict(data[c], src=name)
        return result, used

    def known_no_perp(self, coin):
        known = [u for u in self.universes.values() if u]
        return len(known) >= 2 and all(coin not in u for u in known)


def oi_change(snaps, now, cur_oi, src):
    if cur_oi is None:
        return None
    best = None
    for ts, oi, s in snaps or []:
        if s == src and now - 4200 <= ts <= now - 3000 and oi:
            if best is None or abs(ts - (now - 3600)) < abs(best[0] - (now - 3600)):
                best = (ts, oi)
    return round((cur_oi - best[1]) / best[1] * 100, 2) if best else None


# ---------------------------------------------------------------- message formatting
def fmt_funding(f):
    return "n/a" if f is None else f"{f * 100:+.4f}%/8h"


def fmt_oi(o):
    return "n/a" if o is None else f"{o:+.1f}% 1h"


def market_label(direction, unverified=False):
    if direction == "LONG":
        return "Spot or Futures"
    return "Futures only" + (" (perp listing unverified)" if unverified else "")


def fmt_price(x):
    """Human price: thousands separators, decimals by size (3,450.20 · 141.23 · 1.2345 · 0.0004567)."""
    x = float(x)
    if x >= 100:
        return f"{x:,.2f}"
    if x >= 1:
        return f"{x:,.4f}"
    if x <= 0:
        return f"{x:g}"
    return f"{x:.{3 - math.floor(math.log10(x))}f}"


def coin_name(sym):
    return sym.split("/")[0]


PLAIN_REASONS = [("1H Bull", "1h uptrend"), ("1H Bear", "1h downtrend"), ("5M EMA+VWAP Bull", "5m momentum up"),
                 ("5M EMA+VWAP Bear", "5m momentum down"), ("RSI Bull", "healthy buying strength"),
                 ("RSI Bear", "healthy selling pressure"), ("Vol", "high volume"),
                 ("Bid Wall", "more buyers than sellers waiting"), ("Ask Wall", "more sellers than buyers waiting")]


def plain_reasons(reasons):
    out = []
    for r in reasons:
        out.append(next((p for k, p in PLAIN_REASONS if r.startswith(k)), r))
    return ", ".join(dict.fromkeys(out))


def ai_line(verdict, note):
    if verdict == "UNAVAILABLE":
        return f"🤖 {esc(note)} — use your own judgement"
    text = re.sub(r"^\s*(AI:\s*)?(APPROVED|REJECTED)\s*[:\-–]?\s*", "", note or "", flags=re.I).strip()
    if verdict == "APPROVED":
        return f"🤖 AI: ✅ looks good — “{esc(text)}”"
    return f"🤖 AI: 🤔 “{esc(text)}”"


def format_alert(s, exchange, ai_verdict, ai_note, max_hold_h, deriv_src):
    long_ = s["dir"] == "LONG"
    g, coin, e = s["grade"], esc(coin_name(s["symbol"])), s["price"]
    mood = "strong setup" if g == "A" else "okay setup"
    head = f"🟢 <b>BUY {coin}</b>" if long_ else f"🔴 <b>SHORT (SELL) {coin}</b>"
    where = ("Binance Spot or Futures" if long_ else "Binance Futures only (you profit if the price falls)")
    if not long_ and "unverified" in s.get("market", ""):
        where += " — check the coin has futures"
    pc = lambda px: f"{(px - e) / e * 100:+.1f}%"
    verb = "sell" if long_ else "close"
    details = [f"data {esc(exchange.title())}"] if exchange != "BINANCE" else []
    details += [f"Funding {esc(fmt_funding(s.get('funding8h')))}", f"OI {esc(fmt_oi(s.get('oi_chg_1h')))}",
                f"BTC trend {esc(s.get('btc_regime', 'n/a'))}" + (f" via {esc(deriv_src.title())}" if deriv_src else "")]
    if s.get("deriv_applied") and s.get("deriv_adj"):
        details.append(f"deriv bonus {s['deriv_adj']:+d}")
    lines = [
        f"{head} · {g}-GRADE · {mood} ({s['score']}/100)",
        f"Where: {where}",
        f"1️⃣ {'Buy' if long_ else 'Short'} now near: <b>{fmt_price(e)}</b>",
        f"2️⃣ Stop loss: <b>{fmt_price(s['sl'])}</b> ({pc(s['sl'])}) — "
        f"{'sell if price drops here' if long_ else 'close if price RISES here'}",
        f"3️⃣ Take profit 1: <b>{fmt_price(s['tp1'])}</b> ({pc(s['tp1'])}) → {verb} HALF",
        f"4️⃣ Take profit 2: <b>{fmt_price(s['tp2'])}</b> ({pc(s['tp2'])}) → {verb} the REST",
        f"⏰ Close the trade after {max_hold_h:g} hours if nothing hits",
        ai_line(ai_verdict, ai_note),
        f"ℹ️ Why: {esc(plain_reasons(s['reasons']))}",
        f"<i>{' · '.join(details)}</i>",
    ]
    return "\n".join(lines)


def format_tp1(t, ev):
    long_ = t["dir"] == "LONG"
    return (f"✅ <b>TP1 HIT</b> · {esc(coin_name(t['symbol']))} reached Take profit 1!\n"
            f"👉 {'Sell' if long_ else 'Close'} HALF now ({ev['gross_half']:+.2f}%).\n"
            f"👉 Move your stop loss to <b>{fmt_price(t['entry'])}</b> (your entry price) — this trade can't lose "
            f"money now.\nNext target: <b>{fmt_price(t['tp2'])}</b> (Take profit 2)")


PLAIN_EXIT = {"STOP LOSS": "hit stop loss", "TP2 HIT": "hit Take profit 2",
              "BREAKEVEN after TP1": "came back to entry after TP1 (half was already sold at TP1)",
              "TIME EXIT": "time limit reached"}


def scoreboard(totals):
    parts = []
    for g in ("A", "B"):
        w, l = totals[g]["w"], totals[g]["l"]
        pct = f"{w / (w + l) * 100:.0f}%" if (w + l) else "no trades yet"
        parts.append(f"{g}-grade: {w} win{'s' * (w != 1)} · {l} loss{'es' * (l != 1)} ({pct})")
    return " | ".join(parts)


def format_close(t, ev, totals):
    side = "buy" if t["dir"] == "LONG" else "short"
    res = f"✅ WIN {ev['net']:+.2f}%" if ev["win"] else f"❌ LOSS {ev['net']:+.2f}%"
    return (f"🏁 <b>{esc(coin_name(t['symbol']))} {side} trade finished — {res}</b> after fees ({t['grade']}-GRADE)\n"
            f"Reason: {esc(PLAIN_EXIT.get(ev['reason'], ev['reason']))}\n"
            + ("⚠️ This trade was not AI-checked, so it is not counted on the scoreboard.\n"
               if t.get("exclude_from_scoreboard") else "")
            + f"📊 Scoreboard: {esc(scoreboard(totals))}")


def fmt_age(sec):
    sec = int(max(0, sec))
    return f"{sec // 3600}h {(sec % 3600) // 60}m"


def format_heartbeat(info):
    side = lambda d: "buy" if d == "LONG" else "short"
    opens = ", ".join(f"{esc(coin_name(t['symbol']))} {side(t['dir'])}" for t in info["open_trades"]) or "none"
    nm = ", ".join(f"{esc(coin_name(x[0]))} {side(x[1])} {x[2]}" + (f" ({esc(x[3])})" if len(x) > 3 else "")
                   for x in info["near_misses"]) or "none"
    ex = info["exchange"]
    data = ("✅ Data: Binance" if ex == "BINANCE" else f"⚠️ Data: {esc(ex.title())} (Binance down)" if ex != "none"
            else "❌ Data: no exchange reachable")
    der = f"✅ Derivatives: {esc(info['deriv_src'].title())}" if info["deriv_src"] else "❌ Derivatives: none"
    ai = info["ai_status"]
    ai_s = ("❌ AI: no key" if "no key" in ai else "❌ AI: rate-limited, pausing" if "rate-limited" in ai
            else "❌ AI: failing" if ai.startswith(("error", "empty")) else "✅ AI: working")
    ok = ex == "BINANCE" and info["deriv_src"] and ai_s.startswith("✅")
    lines = [f"💓 <b>BOT ALIVE</b> — {'working fine' if ok else 'working, but see the ❌/⚠️ below'} ({VERSION})",
             f"🔍 Checked {info['coins']} coins {info['cycles']} times this hour",
             f"📨 Alerts sent: {info['alerts']} (AI blocked {info['ai_rejected']}"
             + (f", AI unclear {info['ai_unclear']}" if info.get("ai_unclear") else "") + ")",
             f"📂 Open trades ({len(info['open_trades'])}): {opens}",
             f"🎯 Almost alerts (need {B_MIN}): {nm}",
             f"🏆 Scoreboard: {esc(scoreboard(info['totals']))}",
             f"{data} · {der} · {ai_s}"]
    if info["unavailable"]:
        lines.append(f"⚠️ Not available on {esc(ex.title())}: {esc(', '.join(coin_name(p) for p in info['unavailable']))}")
    if info["skipped"]:
        lines.append(f"⚠️ Skipped (bad data): {esc(', '.join(coin_name(p) for p in info['skipped']))}")
    return "\n".join(lines)


def watchdog_message(state, now, max_age=WATCHDOG_MAX_AGE_S):
    last = int((state or {}).get("last_cycle_time") or 0)
    if last and now - last <= max_age:
        return None
    if not last:
        return ("⚠️ <b>BOT DOWN</b> — I haven't finished a single market check since v10 was installed. "
                "Open GitHub → Actions to see why.")
    return (f"⚠️ <b>BOT DOWN</b> — I haven't checked the market for {fmt_age(now - last)}. "
            "Open GitHub → Actions to see why.")


GUIDE_TEXT = "\n".join([
    "📘 <b>HOW TO READ MY MESSAGES</b>",
    "🟢 <b>BUY</b> = I think the price will go UP. You can use Binance Spot or Futures.",
    "🔴 <b>SHORT (SELL)</b> = I think the price will go DOWN. Binance Futures only — you profit if the price falls.",
    f"⭐ <b>A-GRADE</b> = strong setup ({A_MIN}+ points). <b>B-GRADE</b> = okay setup ({B_MIN}–{A_MIN - 1}), riskier.",
    "1️⃣ Entry: the price to get in near.",
    "2️⃣ Stop loss: if the price gets there, get out. It keeps the loss small.",
    "3️⃣ Take profit 1: sell (or close) HALF there, then move your stop loss to your entry price — "
    "after that the trade can't lose money.",
    "4️⃣ Take profit 2: sell (or close) the REST there.",
    "⏰ Time limit: if nothing is hit, close the trade after the hours shown.",
    "✅ <b>TP1 HIT</b> = do step 3 now. 🏁 Trade finished = the result after Binance fees + the scoreboard.",
    "💓 <b>BOT ALIVE</b> = my hourly “I'm working” message. ⚠️ <b>BOT DOWN</b> = I stopped — open GitHub → Actions.",
    "🧪 Important: paper-test first (write the trades down, no real money). These rules are NOT proven to make "
    "money yet — don't risk real money.",
])


# ---------------------------------------------------------------- git persistence
GIT_ID = ["-c", "user.name=WarRoomBot", "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com"]


def git_sync(repo_dir, state_path, branch="main", attempts=5, message="bot: update state.json", backoff=2.0):
    """Push the bot's state.json: fetch → reset --hard origin/branch → restore bot state.json → commit → push."""
    if not os.path.exists(state_path):
        log("git sync: no state file to push")
        return False
    tmpd = tempfile.mkdtemp(prefix="wr-state-")
    saved = os.path.join(tmpd, "state.json")
    shutil.copy2(state_path, saved)
    target = os.path.join(repo_dir, "state.json")

    def g(*args, check=True):
        return subprocess.run(["git", *GIT_ID, *args], cwd=repo_dir, capture_output=True, text=True, timeout=90,
                              check=check)
    try:
        for i in range(attempts):
            try:
                g("fetch", "--quiet", "origin", branch)
                g("reset", "--quiet", "--hard", f"origin/{branch}")
                shutil.copy2(saved, target)
                g("add", "state.json")
                if g("diff", "--cached", "--quiet", check=False).returncode == 0:
                    log("git sync: state.json already up to date on remote")
                    return True
                g("commit", "--quiet", "-m", message)
                g("push", "--quiet", "origin", f"HEAD:{branch}")
                log(f"git sync: state.json pushed (attempt {i + 1})")
                return True
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
                log(f"git sync attempt {i + 1} failed: {redact(getattr(e, 'stderr', '') or e).strip()[:240]}")
                time.sleep(min(backoff * 2 ** i, 30))
        log("git sync: giving up after retries")
        return False
    finally:
        shutil.copy2(saved, target) if os.path.abspath(state_path) == os.path.abspath(target) else None
        shutil.rmtree(tmpd, ignore_errors=True)


def file_hash(path):
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


# ---------------------------------------------------------------- bot
class Bot:
    def __init__(self, cfg, state=None, spot=None, deriv=None, tg=None, ai=None):
        self.cfg = cfg
        self.state = state if state is not None else load_state(cfg.state_file)
        self.spot = spot or SpotRouter()
        self.deriv = deriv or DerivRouter()
        self.tg = tg or Telegram(cfg)
        self.ai = ai or AIReviewer(cfg)
        self.cache = {}
        self.coins = []
        self.coin_source = "?"
        self.unavailable = []
        self.skipped = {}
        self.regime = "n/a"
        self.deriv_used = None
        self.last_push = 0.0
        self.last_push_hash = None
        self.cycle_stats = []
        self.cycle_t0 = time.time()
        self.pegged = set()

    def save(self):
        save_state(self.cfg.state_file, self.state)

    # -- setup
    def start(self):
        if self.spot.src is None:
            self.spot.select()
        self.refresh_coins()
        try:
            self.deriv.probe([norm_base(c) for c in self.coins])
        except Exception as e:
            log(f"Derivatives probe error: {err_text(e)}")
        self.migrate_ai_gate()
        self.save()

    def migrate_ai_gate(self):
        """One-time: trades opened unreviewed by the pre-fix version (key set, ai UNAVAILABLE) leave the scoreboard."""
        if self.state.get("ai_gate_migrated"):
            return
        if self.cfg.llm_key:
            bad = lambda t: t.get("ai") == "UNAVAILABLE" and not t.get("ai_note")
            for t in self.state["active_trades"] + self.state["closed_trades"]:
                if isinstance(t, dict) and bad(t):
                    t["exclude_from_scoreboard"] = True
            self.state["totals"] = recompute_totals(self.state["closed_trades"])
        self.state["ai_gate_migrated"] = True

    def prune(self, now):
        for k, scale in (("last_alert_time", 1), ("last_alert_candle", 1000), ("ai_rejected_candle", 1000)):
            self.state[k] = {s: v for s, v in self.state[k].items() if now - v / scale <= STATE_KEEP_S}

    def refresh_coins(self):
        coins, src = None, self.spot.src
        if src is not None and src.name == "BINANCE":
            try:
                coins = build_coin_list(src.tickers_24h(), self.cfg.top_n, tradable=src.markets,
                                        exclude_symbols=getattr(src, "stock_tokens", set()),
                                        extra_bases=self.cfg.exclude_coins)
                if len(coins) < min(10, self.cfg.top_n):
                    coins = None
            except Exception as e:
                log(f"Top-coin list fetch failed: {err_text(e)}")
        if coins:
            self.state["coin_list"], self.state["coin_list_time"], self.coin_source = coins, int(time.time()), "live"
        elif self.state.get("coin_list"):
            coins, self.coin_source = self.state["coin_list"][:self.cfg.top_n], "saved list"
        else:
            coins, self.coin_source = BUILTIN_COINS[:self.cfg.top_n], "built-in list"
        self.coins = coins
        log(f"Coin list ({self.coin_source}): {len(coins)} coins: {', '.join(c.split('/')[0] for c in coins)}")

    # -- data
    def _fetch_one(self, src, sym, now_ms):
        out = {}
        for tf, limit in (("1h", LIMIT_1H), ("5m", LIMIT_5M)):
            key = (src.name, sym, tf)
            c = self.cache.get(key)
            if c is None or now_ms >= c["last_ts"] + 2 * TF_MS[tf]:
                try:
                    rows = src.ohlcv(sym, tf, limit)
                except Exception as e:
                    out["err"] = f"{tf} candles: {err_text(e)[:80]}"
                    return out
                closed, reason = validate_candles(rows, tf, now_ms)
                if reason:
                    self.cache.pop(key, None)
                    out["err"] = f"{tf} {reason}"
                    return out
                c = {"last_ts": closed[-1][0], "df": add_indicators(to_df(closed))}
                self.cache[key] = c
                out[f"fetched_{tf}"] = True
            out["df" + tf] = c["df"]
        try:
            out["ob_imb"] = ob_imbalance(src.order_book(sym, 20))
        except Exception as e:
            log(f"{sym}: order book error: {err_text(e)}")
            out["ob_imb"] = 0.0
        return out

    def fetch_market(self, src, syms, now_ms):
        workers = 8 if src.name == "BINANCE" else 1  # ccxt instances are not thread-safe
        with ThreadPoolExecutor(workers) as p:
            res = dict(zip(syms, p.map(lambda s: self._safe_fetch(src, s, now_ms), syms)))
        self.skipped = {s: r["err"] for s, r in res.items() if r.get("err")}
        for s, e in self.skipped.items():
            log(f"{s}: skipped this scan: {e}")
        return res

    def _safe_fetch(self, src, sym, now_ms):
        try:
            return self._fetch_one(src, sym, now_ms)
        except Exception as e:
            return {"err": f"error {err_text(e)[:80]}"}

    # -- trades
    def check_trades(self, src, prices, now):
        for t in list(self.state["active_trades"]):
            sym = t["symbol"]
            if t.get("exchange") not in (src.name, "UNKNOWN", None):
                log(f"{sym}: entered on {t['exchange']}, data now from {src.name}: keeping open until it is back")
                continue
            try:
                if not src.has(sym):
                    raise RuntimeError(f"{sym} not listed on {src.name}")
                since = max(int(t["entry_time"] * 1000), int(t.get("checked_until", 0)) + 1)
                rows = src.ohlcv(sym, "5m", 1000, since=since)
                rows = [r for r in rows if all(math.isfinite(float(x)) and float(x) > 0 for x in r[1:5])]
                events = resolve_trade(t, rows, prices.get(sym), now, self.cfg.max_hold(t["grade"]),
                                       self.cfg.fee_spot, self.cfg.fee_fut)
            except Exception as e:
                log(f"{sym}: trade check failed, keeping open: {err_text(e)}")
                continue
            for ev in events:
                if ev["type"] == "TP1":
                    self.notify(format_tp1(t, ev))
                elif not t.get("exclude_from_scoreboard"):
                    tot = self.state["totals"].setdefault(t["grade"], {"w": 0, "l": 0, "net_sum": 0.0})
                    tot["w" if ev["win"] else "l"] += 1
                    tot["net_sum"] = round(tot["net_sum"] + ev["net"], 4)
                if ev["type"] == "CLOSE":
                    self.state["closed_trades"] = (self.state["closed_trades"] + [t])[-CLOSED_CAP:]
                    self.notify(format_close(t, ev, self.state["totals"]))
            if t.get("status") == "CLOSED":
                self.state["active_trades"] = [x for x in self.state["active_trades"] if x is not t]
            if events:
                self.save()

    def notify(self, text):
        if not self.tg.send(text):
            box = self.state.setdefault("outbox", [])
            box.append({"text": text, "tries": 1})
            self.state["outbox"] = box[-30:]

    def flush_outbox(self):
        keep = []
        for m in self.state.get("outbox", []):
            if keep or not self.tg.send(m["text"]):
                m["tries"] = m.get("tries", 0) + 1
                if m["tries"] <= 10:
                    keep.append(m)
                else:
                    log("Dropping undeliverable message after 10 tries")
        self.state["outbox"] = keep

    # -- alerts
    def scan_and_alert(self, src, market, prices, deriv_data, now):
        open_syms = {t["symbol"] for t in self.state["active_trades"]}
        cands = []
        for sym in self.coins:
            m = market.get(sym) or {}
            price = prices.get(sym)
            if "df5m" not in m or "df1h" not in m or not price:
                continue
            base = norm_base(sym)
            d = deriv_data.get(base) or {}
            oic = oi_change(self.state["oi_snap"].get(base), now, d.get("oi"), d.get("src")) if d else None
            res = evaluate_pair(m["df5m"], m["df1h"], m.get("ob_imb", 0.0), price, d.get("funding8h"), oic,
                                self.regime, self.cfg.use_deriv)
            if not res or not res["dir"]:
                continue
            res.update(symbol=sym, deriv_src=d.get("src"), deriv_applied=self.cfg.use_deriv)
            if not res["grade"]:
                nm = self.state["near_miss"].get(sym)
                if not nm or res["score"] > nm[1]:
                    self.state["near_miss"][sym] = [res["dir"], res["score"], int(now)]
                continue
            if sym in open_syms or now - self.state["last_alert_time"].get(sym, 0) < COOLDOWN_S:
                continue
            if is_pegged(price, m["df1h"]):
                log(f"{sym}: skipped, pegged/stable-like price")
                self.pegged.add(sym)
                continue
            if tp1_too_small(res["dir"], res["price"], res["tp1"], self.cfg.fee_spot, self.cfg.fee_fut,
                             self.cfg.min_tp1_fee_mult):
                self.state["near_miss"][sym] = [res["dir"], res["score"], int(now), "target too small vs fees"]
                continue
            if self.state["last_alert_candle"].get(sym) == res["candle_ts"]:
                continue
            if self.state["ai_rejected_candle"].get(sym) == res["candle_ts"]:
                continue
            if res["dir"] == "SHORT":
                if self.deriv.known_no_perp(base):
                    log(f"{sym}: SHORT setup suppressed (no perpetual market known)")
                    continue
                res["market"] = market_label("SHORT", unverified=not d)
            else:
                res["market"] = market_label("LONG")
            cands.append(res)
        for res in sorted(cands, key=lambda r: -r["score"]):
            self._alert(src, res, now)

    def _alert(self, src, res, now):
        sym, hold = res["symbol"], self.cfg.max_hold(res["grade"])
        res["max_hold_h"] = hold
        verdict, note = self.ai.review(res, start_by=self.cycle_t0 + CYCLE_AI_BUDGET_S)
        if verdict == "DEFER":
            log(f"{sym}: AI review deferred to next cycle (AI throttle); nothing recorded")
            return False
        if verdict in ("REJECTED", "UNCLEAR"):
            log(f"{sym}: alert dropped by AI review ({verdict})")
            self.state["ai_rejected_candle"][sym] = res["candle_ts"]
            key = "ai_rejected" if verdict == "REJECTED" else "ai_unclear"
            self.state["hour_stats"][key] = self.state["hour_stats"].get(key, 0) + 1
            self.save()
            return False
        text = format_alert(res, src.name, verdict, note, hold, res.get("deriv_src"))
        if not self.tg.send(text):
            log(f"{sym}: alert not delivered; trade NOT recorded, will retry next cycle")
            return False
        trade = {"id": f"{sym}-{res['candle_ts']}", "symbol": sym, "dir": res["dir"], "grade": res["grade"],
                 "score": res["score"], "base_score": res["base_score"], "deriv_adj": res["deriv_adj"],
                 "entry": res["price"], "sl": res["sl"], "tp1": res["tp1"], "tp2": res["tp2"],
                 "entry_time": int(now), "candle_ts": res["candle_ts"], "exchange": src.name,
                 "market": res["market"], "funding8h": res.get("funding8h"), "oi_chg_1h": res.get("oi_chg_1h"),
                 "btc_regime": res.get("btc_regime"), "ai": verdict, "ai_note": note[:200], "tp1_hit": False, "checked_until": 0,
                 "status": "OPEN"}
        self.state["active_trades"].append(trade)
        self.state["last_alert_time"][sym] = int(now)
        self.state["last_alert_candle"][sym] = res["candle_ts"]
        self.state["hour_stats"]["alerts"] += 1
        self.save()
        log(f"{sym}: {res['grade']}-GRADE {res['dir']} {res['score']}/100 alert delivered and trade recorded")
        return True

    # -- derivatives bookkeeping
    def update_oi(self, deriv_data, now):
        snaps = self.state["oi_snap"]
        for coin, d in deriv_data.items():
            if d.get("oi") is None:
                continue
            lst = snaps.setdefault(coin, [])
            if not lst or now - lst[-1][0] >= 240:
                lst.append([int(now), sig(d["oi"], 7), d["src"]])
            snaps[coin] = [x for x in lst if now - x[0] <= 5400]
        for coin in list(snaps):
            if not snaps[coin] or now - snaps[coin][-1][0] > 5400:
                snaps.pop(coin)

    # -- heartbeat / live
    def heartbeat_info(self, now):
        nm = sorted(((s, v[0], v[1]) + tuple(v[3:4]) for s, v in self.state["near_miss"].items()
                     if v[1] < B_MIN or len(v) > 3), key=lambda x: -x[2])[:3]
        src = self.spot.src
        return {"cycles": len([t for t in self.state["cycle_times"] if now - t <= 3600]), "coins": len(self.coins),
                "near_misses": nm, "exchange": src.name if src else "none", "deriv_src": self.deriv_used,
                "regime": self.regime, "open_trades": self.state["active_trades"], "totals": self.state["totals"],
                "ai_status": self.ai.status(), "alerts": self.state["hour_stats"]["alerts"],
                "ai_rejected": self.state["hour_stats"]["ai_rejected"],
                "ai_unclear": self.state["hour_stats"].get("ai_unclear", 0), "unavailable": self.unavailable,
                "skipped": sorted(set(self.skipped) | self.pegged)}

    def maybe_heartbeat(self, now):
        if now - self.state.get("last_heartbeat", 0) < 3600:
            return
        self.notify(format_heartbeat(self.heartbeat_info(now)))  # undelivered → outbox; counters reset either way
        self.state["last_heartbeat"] = int(now)
        self.state["near_miss"] = {}
        self.state["hour_stats"] = {"alerts": 0, "ai_rejected": 0, "ai_unclear": 0}
        self.pegged = set()

    def maybe_live_message(self):
        if self.state.get("v10_live_sent"):
            return
        src = self.spot.src
        text = (f"🤖 <b>Crypto War Room {VERSION} LIVE</b>\n"
                f"Scanning top {len(self.coins)} Binance USDT coins ({esc(self.coin_source)}) every "
                f"{self.cfg.scan_interval:g}s on <b>{esc(src.name if src else 'none')}</b> data.\n"
                f"Grades: A ≥{A_MIN}, B {B_MIN}–{A_MIN - 1}. Results shown after fees "
                f"(LONG {2 * self.cfg.fee_spot:.2f}%, SHORT {2 * self.cfg.fee_fut:.2f}% round trip).\n"
                f"Max hold: A {self.cfg.max_hold_a:g}h, B {self.cfg.max_hold_b:g}h. AI: {esc(self.ai.status())}.")
        if self.tg.send(text):
            self.state["v10_live_sent"] = True
            self.save()

    def maybe_guide(self):
        if not self.state.get("guide_sent") and self.tg.send(GUIDE_TEXT):
            self.state["guide_sent"] = True
            self.save()

    def healthcheck(self):
        if not self.cfg.healthcheck_url:
            return
        try:
            _http_get(self.cfg.healthcheck_url, 5)
        except Exception as e:
            log(f"Healthcheck ping failed: {type(e).__name__}")

    def maybe_checkpoint(self, now, force=False):
        if not self.cfg.checkpoint or (not force and now - self.last_push < CHECKPOINT_S):
            return
        h = file_hash(self.cfg.state_file)
        if h == self.last_push_hash:
            return
        self.last_push = now
        if git_sync(self.cfg.repo_dir, self.cfg.state_file, self.cfg.git_branch, attempts=3):
            self.last_push_hash = h

    def _safe(self, name, fn, *a):
        try:
            return fn(*a)
        except Exception as e:
            log(f"{name} error (continuing): {err_text(e)}")
            log(redact(traceback.format_exc(limit=4)))
            return None

    # -- one cycle
    def cycle(self, now=None):
        t0 = time.perf_counter()
        self.cycle_t0 = time.time()
        src = self.spot.src or self.spot.select()
        if src is None:
            log("No spot exchange reachable this cycle")
            return False
        now = now or time.time() + getattr(src, "time_offset_ms", 0) / 1000
        now_ms = int(now * 1000)
        self.prune(now)
        self.spot.maybe_return_to_primary()
        src = self.spot.src
        if not self.coins:
            self.refresh_coins()
        self._safe("v10 live message", self.maybe_live_message)
        self._safe("guide message", self.maybe_guide)
        scan = list(dict.fromkeys(self.coins + [t["symbol"] for t in self.state["active_trades"]]))
        avail = [s for s in scan if src.has(s)]
        self.unavailable = [s for s in scan if not src.has(s)]
        with ThreadPoolExecutor(1) as dp:
            dfut = dp.submit(self.deriv.fetch, [norm_base(s) for s in self.coins])
            try:
                prices = src.prices(avail)
            except Exception as e:
                log(f"Price fetch failed on {src.name}: {err_text(e)}")
                prices = {}
            market = self.fetch_market(src, avail, now_ms)
            try:
                deriv_data, self.deriv_used = dfut.result(timeout=40)
            except Exception as e:
                log(f"Derivatives fetch error: {err_text(e)}")
                deriv_data, self.deriv_used = {}, None
        ok = sum(1 for s in avail if "df5m" in market.get(s, {}))
        if not prices or (len(avail) >= 5 and ok < len(avail) / 2):
            log(f"{src.name} looks unreachable (prices={len(prices)}, ok pairs={ok}/{len(avail)}); reconnecting")
            self.cache.clear()
            self.spot.select()
            if not prices:
                return False
        btc = market.get("BTC/USDT", {}).get("df1h")
        self.regime = btc_regime(btc) if btc is not None else self.regime
        self._safe("OI snapshot", self.update_oi, deriv_data, now)
        self._safe("trade check", self.check_trades, src, prices, now)
        self._safe("scan", self.scan_and_alert, src, market, prices, deriv_data, now)
        self._safe("outbox", self.flush_outbox)
        self.state["last_cycle_time"] = int(now)
        self.state["cycle_times"] = [t for t in self.state["cycle_times"] if now - t <= 3600] + [int(now)]
        self.save()
        self._safe("heartbeat", self.maybe_heartbeat, now)
        self.save()
        self.healthcheck()
        dt = time.perf_counter() - t0
        n1h = sum(1 for s in avail if market.get(s, {}).get("fetched_1h"))
        n5 = sum(1 for s in avail if market.get(s, {}).get("fetched_5m"))
        self.cycle_stats.append({"secs": round(dt, 2), "pairs_ok": ok, "pairs": len(avail), "src": src.name,
                                 "deriv": self.deriv_used, "fetched_1h": n1h, "fetched_5m": n5,
                                 "weight": getattr(src, "used_weight", None), "http_429": getattr(src, "http_429", 0)})
        log(f"Cycle done in {dt:.1f}s · {src.name} · pairs ok {ok}/{len(avail)} · 1h refetched {n1h} · "
            f"5m refetched {n5} · deriv {self.deriv_used or 'none'} · BTC {self.regime} · "
            f"open trades {len(self.state['active_trades'])} · weight {getattr(src, 'used_weight', '-')}")
        self._safe("checkpoint", self.maybe_checkpoint, now)
        return True


# ---------------------------------------------------------------- modes
def run_loop(cfg, bot=None):
    bot = bot or Bot(cfg)

    def on_term(signum, frame):
        raise SystemExit(f"signal {signum}")
    try:
        signal.signal(signal.SIGTERM, on_term)
        signal.signal(signal.SIGINT, on_term)
    except ValueError:
        pass
    try:
        bot.start()
        end = time.time() + cfg.run_minutes * 60
        while True:
            t0 = time.time()
            try:
                bot.cycle()
            except Exception as e:
                log(f"Cycle error (continuing): {err_text(e)}")
                log(redact(traceback.format_exc(limit=6)))
            nxt = t0 + cfg.scan_interval
            if nxt >= end:
                break
            time.sleep(max(0.0, nxt - time.time()))
    finally:
        bot.save()
        log(f"Run finished: {len(bot.cycle_stats)} cycles")
    return bot


def run_watchdog(cfg):
    try:
        with open(cfg.state_file, "r", encoding="utf-8") as f:
            state = json.load(f)
    except Exception as e:
        log(f"Watchdog: state unreadable: {err_text(e)}")
        state = {}
    msg = watchdog_message(state, time.time())
    if msg is None:
        log("Watchdog: bot is healthy (last cycle "
            f"{fmt_age(time.time() - int(state.get('last_cycle_time', 0)))} ago)")
        return 0
    log("Watchdog: BOT DOWN detected; alerting")
    Telegram(cfg).send(msg)
    return 0


def run_diagnose(cfg):
    def line(ok, name, detail=""):
        print(f"{'PASS' if ok else 'FAIL'}  {name}: {redact(detail)[:200]}", flush=True)
    try:
        country = _http_get("https://ipinfo.io/json", 8).json().get("country", "?")
        print(f"INFO  runner country: {country}", flush=True)
    except Exception as e:
        print(f"INFO  runner country: unknown ({type(e).__name__})", flush=True)
    for name, f in default_spot_factories():
        try:
            s = f()
            s.connect()
            rows = s.ohlcv("BTC/USDT", "5m", 5)
            line(True, f"spot {name}", f"BTC/USDT last close {rows[-1][4]}")
        except Exception as e:
            line(False, f"spot {name}", err_text(e))
    try:
        r = _http_get("https://api.binance.com/api/v3/ping", 8)
        line(r.status_code == 200, "info api.binance.com (not used)", f"HTTP {r.status_code}")
    except Exception as e:
        line(False, "info api.binance.com (not used)", err_text(e))
    s = requests.Session()
    for name, fn in DERIV_SOURCES:
        try:
            d = fn(s, ["BTC", "ETH"])
            b = d.get("BTC", {})
            line(bool(d), f"derivatives {name}", f"{len(d)} markets, BTC funding {fmt_funding(b.get('funding8h'))}")
        except Exception as e:
            line(False, f"derivatives {name}", err_text(e))
    if not cfg.tg_token:
        line(False, "telegram", "token missing or malformed")
    else:
        try:
            r = _http_get(f"https://api.telegram.org/bot{cfg.tg_token}/getMe", 10)
            line(r.status_code == 200, "telegram getMe", f"HTTP {r.status_code}")
            if env_str("DIAG_SEND_TEST") == "1":
                ok = Telegram(cfg).send("🧪 <b>TEST</b> · diagnostics message from the War Room bot")
                line(ok, "telegram test message", "sent" if ok else "failed")
        except Exception as e:
            line(False, "telegram getMe", f"{type(e).__name__}")
    if not cfg.llm_key:
        print("SKIP  gemini: skipped: no key", flush=True)
    else:
        ai = AIReviewer(cfg)
        v, note = ai.review({"symbol": "BTC/USDT", "dir": "LONG", "grade": "B", "score": 70, "price": 1, "sl": 0.98,
                             "tp1": 1.02, "tp2": 1.036, "reasons": ["diagnostic"]})
        line(v != "UNAVAILABLE", f"gemini {cfg.llm_model}", f"{v}: {note[:120]} · {ai.status()}")
    return 0


def sample_messages(cfg, ai_review=False):
    """Example of every message type (used by --test-messages and the tests)."""
    a = {"symbol": "ETH/USDT", "dir": "LONG", "grade": "A", "score": 85, "price": 3450.2, "sl": 3401.0,
         "tp1": 3505.0, "tp2": 3550.0, "market": market_label("LONG"), "funding8h": 0.0001, "oi_chg_1h": 3.0,
         "btc_regime": "up", "deriv_adj": 0, "rsi5": 60.1, "rsi1h": 57.3, "vol": 1.62, "ob": 0.0,
         "reasons": ["1H Bull EMA", "5M EMA+VWAP Bull", "Vol 1.62x"]}
    b = {"symbol": "PEPE/USDT", "dir": "SHORT", "grade": "B", "score": 70, "price": 0.0004567, "sl": 0.0004649,
         "tp1": 0.0004476, "tp2": 0.0004403, "market": market_label("SHORT"), "funding8h": 0.00025,
         "oi_chg_1h": -1.2, "btc_regime": "down", "deriv_adj": 0, "rsi5": 41.0, "rsi1h": 44.0, "vol": 1.4, "ob": 0.0,
         "reasons": ["1H Bear EMA", "5M EMA+VWAP Bear", "RSI Bear 41.0"]}
    verdict, note = "APPROVED", "APPROVED: trend and volume agree; keep the stop tight."
    if ai_review and cfg.llm_key:
        verdict, note = AIReviewer(cfg).review(dict(a, max_hold_h=cfg.max_hold_a))
        log(f"Test AI review: {verdict}")
    t = {"symbol": "ETH/USDT", "dir": "LONG", "grade": "A", "entry": 3450.2, "tp1": 3505.0, "tp2": 3550.0,
         "sl": 3401.0}
    totals = {"A": {"w": 3, "l": 1}, "B": {"w": 1, "l": 2}}
    info = {"cycles": 58, "coins": 50, "near_misses": [("BTC/USDT", "SHORT", 60), ("XRP/USDT", "SHORT", 60),
            ("INIT/USDT", "SHORT", 60)], "exchange": "BINANCE", "deriv_src": "HYPERLIQUID", "regime": "down",
            "open_trades": [{"symbol": "TRX/USDT", "dir": "SHORT"}, {"symbol": "ETH/USDT", "dir": "SHORT"},
                            {"symbol": "NIGHT/USDT", "dir": "LONG"}], "totals": totals,
            "ai_status": "ok (gemini-3.5-flash-lite)", "alerts": 2, "ai_rejected": 1, "unavailable": [], "skipped": []}
    return {"guide": GUIDE_TEXT,
            "alert_long": format_alert(a, "BINANCE", verdict, note, cfg.max_hold_a, "HYPERLIQUID"),
            "alert_short": format_alert(b, "BINANCE", "UNAVAILABLE", "AI unavailable (no key)", cfg.max_hold_b,
                                        "HYPERLIQUID"),
            "tp1": format_tp1(t, {"price": 3505.0, "gross_half": pct_move("LONG", 3450.2, 3505.0)}),
            "close": format_close(t, {"reason": "TP2 HIT", "exit": 3550.0, "gross": 2.8, "net": 2.6, "win": True},
                                  totals),
            "heartbeat": format_heartbeat(info),
            "bot_down": watchdog_message({"last_cycle_time": int(time.time()) - 2 * 3600 - 900}, time.time())}


def run_test_messages(cfg):
    """Send TEST samples (TEST_MESSAGES=comma list of sample names, default: all) through the real send path."""
    cfg.msg_prefix = cfg.msg_prefix or "TEST"
    msgs = sample_messages(cfg, ai_review=True)
    names = [n.strip() for n in env_str("TEST_MESSAGES", ",".join(msgs)).split(",") if n.strip() in msgs]
    tg = Telegram(cfg)
    results = [tg.send(msgs[n]) for n in names]
    log(f"Test messages delivered: {sum(results)}/{len(results)}")
    return 0 if all(results) else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="Crypto War Room bot v10")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--watchdog", action="store_true")
    g.add_argument("--diagnose", action="store_true")
    g.add_argument("--commit-state", action="store_true")
    g.add_argument("--test-messages", action="store_true")
    args = ap.parse_args(argv)
    cfg = Config()
    mask_secrets(cfg)
    try:
        if args.watchdog:
            return run_watchdog(cfg)
        if args.diagnose:
            return run_diagnose(cfg)
        if args.commit_state:
            git_sync(cfg.repo_dir, cfg.state_file, cfg.git_branch)
            return 0
        if args.test_messages:
            return run_test_messages(cfg)
        log(f"War Room {VERSION} starting: run {cfg.run_minutes:g} min, scan every {cfg.scan_interval:g}s, "
            f"dry_run={cfg.dry_run}, telegram={'configured' if cfg.tg_token and cfg.tg_chat else 'MISSING'}, "
            f"ai={'key set' if cfg.llm_key else 'no key'}, deriv_score={'on' if cfg.use_deriv else 'off'}")
        run_loop(cfg)
        return 0
    except BaseException as e:
        if isinstance(e, SystemExit) and str(e).startswith("signal"):
            log(f"Stopped by {e}")
            return 0
        log(f"FATAL: {err_text(e)}")
        log(redact(traceback.format_exc(limit=8)))
        return 1


if __name__ == "__main__":
    sys.exit(main())
