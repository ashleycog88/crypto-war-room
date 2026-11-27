import os, re, json, time
from datetime import datetime, timezone
import ccxt, pandas as pd, numpy as np, requests
from openai import OpenAI

_raw_tok = os.getenv("TELEGRAM_BOT_TOKEN", "")
_m = re.search(r"(\d+:[A-Za-z0-9_-]+)", _raw_tok)
TG_TOKEN = _m.group(1) if _m else _raw_tok.strip()
TG_CHAT = re.sub(r"[^\d-]", "", os.getenv("TELEGRAM_CHAT_ID", ""))

LLM_KEY = os.getenv("LLM_API_KEY", "").strip()
LLM_URL = os.getenv("LLM_BASE_URL", "https://openai.com").strip()
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini").strip()

STATE_FILE = "state.json"
SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT", "DOGE/USDT", "AVAX/USDT", "LINK/USDT"]
COOLDOWN = 3600
MIN_SCORE = 75

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"active_trades": [], "closed_trades": [], "last_alert_time": {}}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

def send_tg(text):
    if not TG_TOKEN or not TG_CHAT:
        print(f"Missing TG creds: tok_len={len(TG_TOKEN)}, chat={TG_CHAT}")
        return
    try:
        r = requests.post(
            f"https://telegram.org{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=15
        )
        print("TG status:", r.status_code, r.text[:120])
    except Exception as e:
        print("TG error:", e)

def get_exchange():
    for name in ["kucoin", "okx", "kraken", "gateio", "bybit"]:
        try:
            ex = getattr(ccxt, name)({"enableRateLimit": True, "timeout": 15000})
            ex.load_markets()
            if "BTC/USDT" in ex.markets:
                return ex, name
        except Exception:
            continue
    raise RuntimeError("No public exchange reachable")

def add_ind(df):
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]
    df["ema9"] = c.ewm(span=9, adjust=False).mean()
    df["ema21"] = c.ewm(span=21, adjust=False).mean()
    df["ema50"] = c.ewm(span=50, adjust=False).mean()
    d = c.diff()
    gain = d.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    loss = (-d.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean().replace(0, np.nan)
    df["rsi"] = (100 - (100 / (1 + (gain / loss)))).fillna(100.0)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    df["atr"] = tr.ewm(alpha=1/14, adjust=False).mean()
    v_sum = v.rolling(24).sum().replace(0, np.nan)
    df["vwap"] = (((h + l + c) / 3) * v).rolling(24).sum() / v_sum
    df["vol_r"] = (v / v.rolling(20).mean().replace(0, np.nan)).fillna(1.0)
    return df

def scan_pair(ex, sym):
    if sym not in ex.markets:
        return None
    df5 = add_ind(pd.DataFrame(ex.fetch_ohlcv(sym, "5m", limit=100), columns=["ts","open","high","low","close","volume"]))
    df1h = add_ind(pd.DataFrame(ex.fetch_ohlcv(sym, "1h", limit=60), columns=["ts","open","high","low","close","volume"]))
    r5, r1 = df5.iloc[-1], df1h.iloc[-1]
    price, atr = float(r5["close"]), float(r5["atr"])
    if pd.isna(atr) or atr <= 0 or price <= 0:
        return None
    rsi5, rsi1h, vol_r = float(r5["rsi"]), float(r1["rsi"]), float(r5["vol_r"])

    ob_imb = 0.0
    try:
        ob = ex.fetch_order_book(sym, limit=10)
        bv = sum(float(b[1]) for b in ob["bids"][:10])
        av = sum(float(a[1]) for a in ob["asks"][:10])
        if (bv + av) > 0:
            ob_imb = (bv - av) / (bv + av)
    except Exception:
        pass

    ls, ss, reasons = 0, 0, []
    if r1["close"] > r1["ema21"] > r1["ema50"]:
        ls += 25; reasons.append("1H Bull EMA")
    elif r1["close"] < r1["ema21"] < r1["ema50"]:
        ss += 25; reasons.append("1H Bear EMA")

    if not pd.isna(r5["vwap"]):
        if r5["ema9"] > r5["ema21"] and price > r5["vwap"]:
            ls += 25; reasons.append("5M EMA+VWAP Bull")
        elif r5["ema9"] < r5["ema21"] and price < r5["vwap"]:
            ss += 25; reasons.append("5M EMA+VWAP Bear")

    if 52 <= rsi5 <= 68 and rsi1h > 50:
        ls += 20; reasons.append(f"RSI Bull {rsi5:.1f}")
    elif 32 <= rsi5 <= 48 and rsi1h < 50:
        ss += 20; reasons.append(f"RSI Bear {rsi5:.1f}")

    if vol_r >= 1.35:
        ls += 15; ss += 15; reasons.append(f"Vol {vol_r:.2f}x")

    if ob_imb >= 0.15:
        ls += 15; reasons.append(f"Bid Wall +{ob_imb*100:.0f}%")
    elif ob_imb <= -0.15:
        ss += 15; reasons.append(f"Ask Wall {ob_imb*100:.0f}%")

    if ls >= MIN_SCORE and ls > ss:
        return {"symbol": sym, "dir": "LONG", "score": ls, "price": round(price,6), "sl": round(price-1.8*atr,6), "tp1": round(price+2*atr,6), "tp2": round(price+3.6*atr,6), "rsi5": round(rsi5,1), "rsi1h": round(rsi1h,1), "vol": round(vol_r,2), "ob": round(ob_imb*100,1), "reasons": reasons}
    if ss >= MIN_SCORE and ss > ls:
        return {"symbol": sym, "dir": "SHORT", "score": ss, "price": round(price,6), "sl": round(price+1.8*atr,6), "tp1": round(price-2*atr,6), "tp2": round(price-3.6*atr,6), "rsi5": round(rsi5,1), "rsi1h": round(rsi1h,1), "vol": round(vol_r,2), "ob": round(ob_imb*100,1), "reasons": reasons}
    return None

def ai_review(setup):
    fb = f"Quant Score {setup['score']}/100 ({', '.join(setup['reasons'])}). 1:2 ATR R/R verified."
    if not LLM_KEY:
        return True, fb
    try:
        c = OpenAI(api_key=LLM_KEY, base_url=LLM_URL)
        res = c.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role":"user","content":f"5-Agent Crypto Quant War Room review: {json.dumps(setup)}. Start with APPROVED: or REJECTED: then 1 concise risk sentence."}],
            max_tokens=110,
            temperature=0.2
        )
        txt = res.choices[0].message.content.strip()
        return (not txt.upper().startswith("REJECTED")), txt
    except Exception:
        return True, fb

def check_trades(ex, state):
    open_t = []
    for t in state.get("active_trades", []):
        try:
            curr = float(ex.fetch_ticker(t["symbol"])["last"])
            is_l = t["dir"] == "LONG"
            pnl = ((curr - t["entry"]) / t["entry"] * 100) if is_l else ((t["entry"] - curr) / t["entry"] * 100)
            hit = "TP2 HIT" if (is_l and curr >= t["tp2"]) or (not is_l and curr <= t["tp2"]) else ("STOP LOSS" if (is_l and curr <= t["sl"]) or (not is_l and curr >= t["sl"]) else None)
            if hit:
                t.update({"exit": curr, "status": hit, "pnl": round(pnl, 2)})
                state["closed_trades"].append(t)
                wins = sum(1 for x in state["closed_trades"] if x.get("pnl", 0) > 0)
                tot = len(state["closed_trades"])
                send_tg(f"{'✅' if pnl>0 else '🛑'} <b>CLOSED {t['symbol']} ({t['dir']})</b>\n{hit}: <b>{pnl:+.2f}%</b> | Record: {wins}W/{tot-wins}L ({wins/tot*100:.0f}%)")
            else:
                open_t.append(t)
        except Exception:
            open_t.append(t)
    state["active_trades"] = open_t

def main():
    state = load_state()
    ex, ex_name = get_exchange()
    now = int(time.time())
    check_trades(ex, state)
    active = {t["symbol"] for t in state["active_trades"]}
    sent = 0
    for sym in SYMBOLS:
        if sym in active or now - state.get("last_alert_time", {}).get(sym, 0) < COOLDOWN:
            continue
        try:
            s = scan_pair(ex, sym)
            if not s:
                continue
            ok, note = ai_review(s)
            if not ok:
                continue
            send_tg(f"{'🟢' if s['dir']=='LONG' else '🔴'} <b>{s['symbol']} {s['dir']}</b> ({ex_name.upper()} | {s['score']}/100)\n🎯 Entry: <code>{s['price']}</code> | 🛑 SL: <code>{s['sl']}</code>\n💰 TP1: <code>{s['tp1']}</code> | 🚀 TP2: <code>{s['tp2']}</code>\n📈 RSI 5m/1h: {s['rsi5']}/{s['rsi1h']} | Vol: {s['vol']}x | OB: {s['ob']:+.0f}%\n🧠 {note}")
            state["active_trades"].append({"symbol": s["symbol"], "dir": s["dir"], "entry": s["price"], "sl": s["sl"], "tp1": s["tp1"], "tp2": s["tp2"]})
            state.setdefault("last_alert_time", {})[sym] = now
            sent += 1
        except Exception as e:
            print(sym, e)
    if not state.get("booted"):
        state["booted"] = True
        if sent == 0:
            send_tg(f"🤖 <b>Crypto Quant War Room v9 LIVE!</b>\nScanning {len(SYMBOLS)} pairs on <b>{ex_name.upper()}</b> every 5 mins.")
    save_state(state)

if __name__ == "__main__":
    main()
                
