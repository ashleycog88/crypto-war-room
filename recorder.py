#!/usr/bin/env python3
"""Edge-data recorder (Phase 3b): records free public crypto data the bot can't get history for later.

Data goes only to the orphan `data` branch as new hourly chunk files data/YYYY/MM/DD/HH_<source>-<chunk>.jsonl.gz
(.enc when DATA_KEY is set). data/status.json is plaintext health only (per-source last OK time and counts).
Modes: (default) loop | --push-only (end-of-run push) | --probe (try every source once, print PASS/FAIL)
Runs on GitHub Actions or as a plain long-running process (RUN_MINUTES=0 = forever, REC_PUSH=0 = no git).
"""
import argparse, calendar, gzip, hashlib, json, os, re, shutil, signal, subprocess, sys, tempfile, threading, time
from datetime import datetime, timezone

import requests

import datalock
import war_room_bot as wr
from war_room_bot import dlog, env_num, env_str, err_text, log

HL = "https://api.hyperliquid.xyz/info"
HL_LEADERBOARD = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
HLP_VAULTS = {"0xdfc24b077bc1425ad1dea75bcb6f8158e10df303", "0x010461c14e146ac35fe42271bdc1134ee31c703a"}
OKX = "https://www.okx.com"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) WarRoomRecorder/1.0"}
FEEDS = {"coindesk": "https://www.coindesk.com/arc/outboundfeeds/rss/", "cointelegraph": "https://cointelegraph.com/rss",
         "decrypt": "https://decrypt.co/feed", "theblock": "https://www.theblock.co/rss.xml",
         "bitcoinmagazine": "https://bitcoinmagazine.com/.rss/full/", "cryptoslate": "https://cryptoslate.com/feed/"}
CATEGORIES = ["listing", "delisting", "hack/exploit", "regulation", "macro", "ETF/institutional", "partnership",
              "token unlock", "other"]
WALLET_RULE = ("Hyperliquid leaderboard: account value >= $100k, 30-day PnL > 0 and all-time ROI > 0; excluded: HLP/"
               "vault addresses, names containing 'vault'/'HLP', and market makers (30-day volume > 300x account "
               "value); ranked by 30-day PnL; top 100 kept")
EVERY = {"hl_leaderboard": 21600, "hl_market": 300, "hl_wallets": 900, "hl_fills": 86400, "news": 300, "news_ai": 900, "listings": 300,
         "listings_okx": 300, "listings_binance": 300, "listings_upbit": 300, "listings_kucoin": 300,
         "whales": 300, "liquidations": 300, "longshort": 900, "context": 3600}
OPTIONAL = {"listings_okx", "listings_binance", "listings_upbit", "listings_kucoin"}  # summarised by "listings"
DAY_MS = 86_400_000
HL_MARKET_TOP = 100  # most-traded Hyperliquid perps (keeps the data branch small)
FILLS_PER_WALLET = 300  # newest fills kept per wallet per day
FILLS_WALLETS_PER_TICK = 15
POS_FULL_S = 3600  # full position snapshot hourly; in between only wallets whose positions changed


def short_id(text):
    return hashlib.sha256(text.encode()).hexdigest()[:10]


def reason(e):
    """Health-only failure reason for logs/status.json: our own messages (HTTP codes) or just the error type."""
    return f"{type(e).__name__}: {str(e)[:60]}" if isinstance(e, RuntimeError) else type(e).__name__


def now_ms():
    return int(time.time() * 1000)


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


class RecConfig:
    def __init__(self, **over):
        gha = env_str("GITHUB_ACTIONS") == "true"
        self.repo_dir = env_str("GITHUB_WORKSPACE", os.path.dirname(os.path.abspath(__file__)))
        self.out_dir = env_str("REC_DIR", os.path.join(self.repo_dir, "rec_data"))
        self.run_minutes = env_num("RUN_MINUTES", 58)
        self.tick = env_num("REC_TICK_S", 20)
        self.dry_run = env_str("DRY_RUN") == "1"
        self.push = not self.dry_run and env_str("REC_PUSH", "1" if gha else "0") == "1"
        self.checkpoint_s = env_num("REC_CHECKPOINT_S", 900)
        self.data_key = env_str("DATA_KEY")
        self.llm_key = env_str("LLM_API_KEY")
        self.llm_url = env_str("LLM_BASE_URL", wr.DEFAULT_LLM_URL)
        self.ai_models = [env_str("REC_LLM_MODEL", "gemini-3.1-flash-lite")] + \
            [m.strip() for m in env_str("REC_LLM_FALLBACK", "gemini-3.5-flash-lite").split(",") if m.strip()]
        self.ai_cap = env_num("REC_AI_DAILY_CAP", 300, int)
        self.whale_top10 = env_num("WHALE_USD_TOP10", 100_000)
        self.whale_other = env_num("WHALE_USD_OTHER", 25_000)
        self.top_n = env_num("REC_TOP_N", 50, int)
        self.n_wallets = env_num("REC_WALLETS", 100, int)
        self.whale_stream = env_str("REC_WS", "1") == "1"
        self.gh_token, self.gh_repo = env_str("GITHUB_TOKEN"), env_str("GITHUB_REPOSITORY")
        self.gh_ref, self.gh_api = env_str("GITHUB_REF_NAME", "main"), env_str("GITHUB_API_URL", "https://api.github.com")
        self.chain_at_minute = env_num("CHAIN_AT_MINUTE", 50)
        self.__dict__.update(over)
        for v in (self.data_key, self.llm_key, self.gh_token):
            wr.register_secret(v)
            if gha and v and len(v) >= 5:
                print(f"::add-mask::{v}", flush=True)


# ---------------------------------------------------------------- storage
class Store:
    """Buffers records per source; flush() writes NEW chunk files only (an existing file is never rewritten)."""

    def __init__(self, root, key):
        self.root, self.key, self.buf = root, key, {}

    def add(self, source, rec, event_ts=None):
        r = {"obs": now_ms(), "src": source}
        if event_ts is not None:
            r["event_ts"] = int(event_ts)
        r.update(rec)
        self.buf.setdefault(source, []).append(r)

    def flush(self):
        files, chunk = [], now_ms()
        for source, recs in self.buf.items():
            by_hour = {}
            for r in recs:
                by_hour.setdefault(r["obs"] // 3_600_000, []).append(r)
            for hour, rs in by_hour.items():
                d = datetime.fromtimestamp(hour * 3600, timezone.utc)
                rel = f"data/{d:%Y/%m/%d}/{d:%H}_{source}-{chunk}.jsonl.gz" + (".enc" if self.key else "")
                path = os.path.join(self.root, rel)
                while os.path.exists(path):  # never overwrite an existing chunk
                    chunk += 1
                    rel = re.sub(r"-\d+\.jsonl", f"-{chunk}.jsonl", rel)
                    path = os.path.join(self.root, rel)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                blob = gzip.compress("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rs).encode())
                datalock.write_atomic(path, datalock.encrypt(self.key, blob) if self.key else blob)
                files.append(rel)
        self.buf = {}
        return files


class Cursor:
    """Small private memory (seen news, AI backlog, fill cursors, health) kept on the data branch, encrypted with a key."""

    def __init__(self, root, key):
        self.key, self.locked = key, False
        self.path = os.path.join(root, "data", "cursor.json" + (".enc" if key else ""))
        plain = os.path.join(root, "data", "cursor.json")
        self.d = {}
        try:
            if os.path.exists(self.path):
                self.d = json.loads(datalock.read_plain(self.path, key))
            elif key and os.path.exists(plain):
                self.d = json.loads(open(plain, encoding="utf-8").read())
        except (datalock.LockError, ValueError) as e:
            log(f"Recorder memory can't be unlocked ({type(e).__name__}); starting fresh, not overwriting it")
            self.locked = True
        for k, v in (("seen", []), ("pending_ai", []), ("ai", {}), ("fills_last", {}), ("liq_last", {}),
                     ("listing_seen", []), ("status", {}), ("hours", []), ("fills_day", "")):
            self.d.setdefault(k, v)

    def save(self):
        if self.locked:
            return
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        blob = json.dumps(self.d, separators=(",", ":")).encode()
        datalock.write_atomic(self.path, datalock.encrypt(self.key, blob) if self.key else blob)
        plain = os.path.join(os.path.dirname(self.path), "cursor.json")
        if self.key and os.path.exists(plain):
            os.remove(plain)


def status_doc(cursor, now, encrypted):
    hours = {h for h in cursor.d["hours"] if h > now // 3600 - 24}
    out = {}
    for name, s in cursor.d["status"].items():
        out[name] = {"last_ok": s.get("last_ok", 0), "n24": sum(n for t, n in s.get("counts", []) if t > now - 86400),
                     "every_s": EVERY.get(name, 300), "err": s.get("err", "")}
        for k in ("count", "disabled"):
            if k in s:
                out[name][k] = s[k]
        if name in OPTIONAL:
            out[name]["optional"] = True
    return {"updated": int(now), "encrypted": encrypted, "cursor_locked": cursor.locked, "hours_24": len(hours),
            "sources": out}


class DataBranch:
    """Orphan `data` branch in a git worktree; pushes add new files with fetch/rebase retries (main is untouched)."""

    def __init__(self, repo_dir, wt, branch="data"):
        self.repo, self.wt, self.branch = repo_dir, wt, branch

    def g(self, *a, cwd=None, check=True):
        return subprocess.run(["git", *wr.GIT_ID, *a], cwd=cwd or self.wt, capture_output=True, text=True,
                              timeout=120, check=check)

    def setup(self):
        if os.path.isdir(os.path.join(self.wt, ".git")) or os.path.isfile(os.path.join(self.wt, ".git")):
            return
        if self.g("fetch", "--quiet", "origin", self.branch, cwd=self.repo, check=False).returncode == 0:
            self.g("worktree", "add", "--quiet", "--force", "-B", self.branch, self.wt, f"origin/{self.branch}",
                   cwd=self.repo)
        else:
            self.g("worktree", "add", "--quiet", "--force", "--detach", self.wt, cwd=self.repo)
            self.g("checkout", "--quiet", "--orphan", self.branch)
            self.g("rm", "-rfq", "--ignore-unmatch", ".")
            for f in os.listdir(self.wt):
                if f != ".git":
                    p = os.path.join(self.wt, f)
                    shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)

    def push(self, attempts=5):
        if not os.path.isdir(os.path.join(self.wt, "data")):
            return True
        self.g("add", "-A", "data")
        if self.g("diff", "--cached", "--quiet", check=False).returncode != 0:
            self.g("commit", "--quiet", "-m", "recorder: new data")
        for i in range(attempts):
            if self.g("push", "--quiet", "origin", f"HEAD:{self.branch}", check=False).returncode == 0:
                log(f"data push ok (attempt {i + 1})")
                return True
            self.g("fetch", "--quiet", "origin", self.branch, check=False)
            if self.g("rebase", "--quiet", "-X", "theirs", f"origin/{self.branch}", check=False).returncode != 0:
                self.g("rebase", "--abort", check=False)
            time.sleep(min(2 ** i, 20))
        log("data push: giving up after retries")
        return False


# ---------------------------------------------------------------- pure helpers (tested)
def select_wallets(rows, n=100, min_value=100_000, max_turnover=300):
    out = []
    for r in rows:
        addr = (r.get("ethAddress") or "").lower()
        name = (r.get("displayName") or "").lower()
        perf = {w: p for w, p in r.get("windowPerformances", [])}
        m, a = perf.get("month") or {}, perf.get("allTime") or {}
        av, mp, mroi, aroi = _f(r.get("accountValue")), _f(m.get("pnl")), _f(m.get("roi")), _f(a.get("roi"))
        mv = _f(m.get("vlm")) or 0.0
        if not addr or None in (av, mp, aroi) or addr in HLP_VAULTS or "vault" in name or "hlp" in name:
            continue
        if av < min_value or mp <= 0 or aroi <= 0 or mv > max_turnover * av:
            continue
        out.append({"addr": addr, "account_value": av, "month_pnl": mp, "month_roi": mroi, "alltime_roi": aroi,
                    "alltime_pnl": _f(a.get("pnl")), "month_vlm": mv})
    out.sort(key=lambda x: -x["month_pnl"])
    for i, w in enumerate(out[:n], 1):
        w["rank"] = i
    return out[:n]


def listing_type(title):
    t = title.lower()
    if re.search(r"delist|remov|cease|suspend|deprecat", t):
        return "delisting"
    if re.search(r"list|launch|add|will support|perpetual|pre-market", t):
        return "listing"
    return "other"


def title_coins(title):
    return sorted(set(re.findall(r"\(([A-Z0-9]{2,12})\)", title)))


class WhaleAgg:
    """Per coin per 5 min: count and USD of aggressive buys/sells >= the coin's whale threshold."""

    def __init__(self, top10, big, small):
        self.top10, self.big, self.small = set(top10), big, small
        self.b, self.msgs, self.last_msg, self.lock = {}, 0, time.time(), threading.Lock()

    def on_trade(self, d):
        usd = float(d["p"]) * float(d["q"])
        coin = d["s"].upper()
        self.msgs, self.last_msg = self.msgs + 1, time.time()
        if usd < (self.big if coin in self.top10 else self.small):
            return
        side = "sell" if d.get("m") else "buy"  # buyer is maker → the aggressor sold
        key = (coin, int(d["T"]) // 300_000 * 300_000)
        with self.lock:
            a = self.b.setdefault(key, {"buy_n": 0, "buy_usd": 0.0, "sell_n": 0, "sell_usd": 0.0})
            a[f"{side}_n"] += 1
            a[f"{side}_usd"] = round(a[f"{side}_usd"] + usd, 2)

    def pop_done(self, now_ms_):
        cur = now_ms_ // 300_000 * 300_000
        with self.lock:
            done = {k: v for k, v in self.b.items() if k[1] < cur}
            for k in done:
                self.b.pop(k)
        return done


def parse_ai_ratings(text, n):
    m = re.search(r"\[.*\]", text or "", re.S)
    arr = json.loads(m.group(0)) if m else None
    if not isinstance(arr, list):
        raise ValueError("no JSON array")
    out = {}
    for o in arr:
        try:
            i, s = int(o["i"]), int(o["sentiment"])
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= i < n and -2 <= s <= 2:
            cat = o.get("category") if o.get("category") in CATEGORIES else "other"
            urg = o.get("urgency") if o.get("urgency") in ("low", "medium", "high") else "low"
            coins = [str(c).upper()[:12] for c in (o.get("coins") or []) if isinstance(c, str)][:10]
            out[i] = {"coins": coins, "sentiment": s, "category": cat, "urgency": urg}
    if not out:
        raise ValueError("no usable ratings")
    return out


def ai_prompt(titles):
    lines = "\n".join(f"{i}. {t[:200]}" for i, t in enumerate(titles))
    return ("Rate each crypto news headline. Reply ONLY with a JSON array, one object per headline: "
            '{"i": <number>, "coins": [tickers mentioned, e.g. "BTC"], "sentiment": -2..2, "category": one of '
            f"{json.dumps(CATEGORIES)}, \"urgency\": \"low\"|\"medium\"|\"high\"}}.\nHeadlines:\n{lines}")


def _ai_call(cfg, model, prompt):
    client = wr._make_openai_client(cfg.llm_key, cfg.llm_url, 25)
    r = client.chat.completions.create(model=model, messages=[{"role": "user", "content": prompt}], max_tokens=1500)
    return r.choices[0].message.content


# ---------------------------------------------------------------- recorder
class Recorder:
    def __init__(self, cfg, session=None, branch=None):
        self.cfg = cfg
        self.key = datalock.derive_key(cfg.data_key, datalock.read_salt()) if cfg.data_key else None
        self.branch = branch or (DataBranch(cfg.repo_dir, cfg.out_dir) if cfg.push else None)
        self.s = session or requests.Session()
        self.s.headers.update(UA)
        self.store = self.cursor = None
        self.last_run, self.wallets, self.coins, self.ctval = {}, [], [], {}
        self.pos_sig, self.pos_full, self.fills_todo, self.wallets_t = {}, 0.0, None, 0.0
        self.whales, self.ws_thread, self.stop = None, None, threading.Event()
        self.t0, self.last_ckpt, self.chained, self.ai_last = time.time(), time.time(), False, 0.0
        self.tasks = [("hl_market", self.hl_market), ("hl_wallets", self.hl_wallets), ("hl_fills", self.hl_fills),
                      ("news", self.news), ("news_ai", self.news_ai), ("listings", self.listings),
                      ("whales", self.whales_collect), ("liquidations", self.liquidations),
                      ("longshort", self.longshort), ("context", self.context)]

    # -- plumbing
    def get(self, url, **kw):
        r = self.s.get(url, timeout=kw.pop("timeout", 15), **kw)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
        return r

    def hl(self, body, timeout=15):
        r = self.s.post(HL, json=body, timeout=timeout)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
        return r.json()

    def start(self):
        if self.branch:
            self.branch.setup()
        os.makedirs(self.cfg.out_dir, exist_ok=True)
        self.store, self.cursor = Store(self.cfg.out_dir, self.key), Cursor(self.cfg.out_dir, self.key)
        try:
            self.coins = wr.build_coin_list(self.get(f"{wr.BINANCE_DATA}/api/v3/ticker/24hr").json(), self.cfg.top_n)
        except Exception as e:
            log(f"coin list failed ({err_text(e)}); using built-in list")
            self.coins = wr.BUILTIN_COINS[:self.cfg.top_n]
        syms = [c.replace("/", "") for c in self.coins]
        self.whales = WhaleAgg(syms[:10], self.cfg.whale_top10, self.cfg.whale_other)
        if self.cfg.whale_stream:
            self.ws_thread = threading.Thread(target=self._ws_loop, args=(syms,), daemon=True)
            self.ws_thread.start()

    def _ws_loop(self, syms):
        import websocket
        url = "wss://data-stream.binance.vision/stream?streams=" + "/".join(f"{s.lower()}@aggTrade" for s in syms)
        while not self.stop.is_set():
            try:
                ws = websocket.create_connection(url, timeout=30)
                while not self.stop.is_set():
                    m = json.loads(ws.recv())
                    if (m.get("data") or {}).get("e") == "aggTrade":
                        self.whales.on_trade(m["data"])
                ws.close()
            except Exception as e:
                log(f"whale stream reconnecting ({type(e).__name__})")
                self.stop.wait(5)

    def mark(self, name, ok, n=0, err="", **extra):
        s = self.cursor.d["status"].setdefault(name, {})
        if ok:
            s["last_ok"], s["err"] = int(time.time()), ""
            hour = int(time.time()) // 3600 * 3600
            c = [x for x in s.get("counts", []) if x[0] > time.time() - 90000]
            if c and c[-1][0] == hour:
                c[-1][1] += n
            else:
                c.append([hour, n])
            s["counts"] = c
        else:
            s["err"] = err[:80]
        s.update(extra)

    def run_task(self, name, fn):
        try:
            n = fn()
            if n is not None:
                self.mark(name, True, n)
            dlog(f"{name}: {n} records")
        except Exception as e:
            self.mark(name, False, err=reason(e))
            log(f"{name} failed: {reason(e)}")

    def tick(self, now=None):
        now = now or time.time()
        for name, fn in self.tasks:
            if now - self.last_run.get(name, 0) >= EVERY[name]:
                self.last_run[name] = now
                self.run_task(name, fn)
        if now - self.last_ckpt >= self.cfg.checkpoint_s:
            self.checkpoint()
        self.maybe_chain(now)

    def checkpoint(self, push=True, final=False):
        self.last_ckpt = time.time()
        files = self.store.flush()
        hours = set(self.cursor.d["hours"]) | {self._hour(f) for f in files}
        self.cursor.d["hours"] = sorted(h for h in hours if h > time.time() // 3600 - 48)
        if final or not self.branch:  # on the data branch the private cursor is rewritten once per run only
            self.cursor.save()
        doc = status_doc(self.cursor, time.time(), bool(self.key))
        datalock.write_atomic(os.path.join(self.cfg.out_dir, "data", "status.json"), json.dumps(doc, indent=1).encode())
        log(f"checkpoint: {len(files)} new files · today {self.size_today() // 1024} KB · hours 24h {doc['hours_24']}")
        if push and self.branch:
            self.branch.push()

    @staticmethod
    def _hour(rel):
        y, m, d, hh = rel.split("/")[1:5]
        return int(datetime(int(y), int(m), int(d), int(hh[:2]), tzinfo=timezone.utc).timestamp()) // 3600

    def size_today(self):
        d = os.path.join(self.cfg.out_dir, "data", datetime.now(timezone.utc).strftime("%Y/%m/%d"))
        return sum(os.path.getsize(os.path.join(d, f)) for f in os.listdir(d)) if os.path.isdir(d) else 0

    def maybe_chain(self, now):
        c = self.cfg
        if self.chained or c.dry_run or not (c.gh_token and c.gh_repo) or now - self.t0 < c.chain_at_minute * 60:
            return
        healthy = any(now - s.get("last_ok", 0) <= 600 for s in self.cursor.d["status"].values())
        if healthy:
            self.chained = True
            log(f"next recorder run: {wr.chain_dispatch(c, 'recorder.yml')}")

    def finish(self):
        self.stop.set()
        for name, fn in self.tasks:
            if name == "whales":
                self.run_task(name, fn)
        self.checkpoint(push=True, final=True)

    # -- sources
    def hl_market(self):
        meta, ctxs = self.hl({"type": "metaAndAssetCtxs"})
        live = [(u, c) for u, c in zip(meta["universe"], ctxs) if not u.get("isDelisted")]
        live.sort(key=lambda uc: -(_f(uc[1].get("dayNtlVlm")) or 0))
        n = 0
        for u, c in live[:HL_MARKET_TOP]:
            self.store.add("hl_market", {"coin": u["name"], "funding": _f(c.get("funding")), "oi": _f(c.get("openInterest")),
                                         "mark": _f(c.get("markPx")), "oracle": _f(c.get("oraclePx")),
                                         "premium": _f(c.get("premium")), "vol24": _f(c.get("dayNtlVlm"))})
            n += 1
        return n

    def load_wallets(self):
        rows = self.get(HL_LEADERBOARD, timeout=60).json().get("leaderboardRows", [])
        self.wallets, self.wallets_t = select_wallets(rows, self.cfg.n_wallets), time.time()
        for w in self.wallets:
            self.store.add("hl_leaderboard", dict(w, rule=WALLET_RULE))
        self.mark("hl_leaderboard", True, len(self.wallets))

    def hl_wallets(self):
        if not self.wallets or time.time() - self.wallets_t >= EVERY["hl_leaderboard"]:
            self.load_wallets()
        n = 0
        for w in self.wallets:
            st = self.hl({"type": "clearinghouseState", "user": w["addr"]})
            pos = [{"coin": p["position"]["coin"], "szi": _f(p["position"].get("szi")),
                    "entry": _f(p["position"].get("entryPx")), "lev": (p["position"].get("leverage") or {}).get("value"),
                    "upnl": _f(p["position"].get("unrealizedPnl")), "value": _f(p["position"].get("positionValue"))}
                   for p in st.get("assetPositions", [])]
            sig = sorted((p["coin"], p["szi"], p["entry"]) for p in pos)
            full = time.time() - self.pos_full >= POS_FULL_S
            if full or self.pos_sig.get(w["addr"]) != sig:
                self.store.add("hl_positions", {"addr": w["addr"], "full": full, "positions": pos,
                                                "account_value": _f((st.get("marginSummary") or {}).get("accountValue"))},
                               event_ts=min(int(st.get("time") or now_ms()), now_ms()))
                n += 1
            self.pos_sig[w["addr"]] = sig
            time.sleep(0.05)
        if time.time() - self.pos_full >= POS_FULL_S:
            self.pos_full = time.time()
        self.mark("hl_wallets", True, n, count=len(self.wallets))
        return None

    def hl_fills(self):
        """Daily: newest fills per wallet since the last run, a few wallets per tick so other sources keep flowing."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self.cursor.d["fills_day"] == today and not self.fills_todo:
            return 0
        if not self.wallets:
            self.load_wallets()
        if not self.fills_todo:
            self.fills_todo = [w["addr"] for w in self.wallets]
        n, last, errs = 0, self.cursor.d["fills_last"], []
        chunk = self.fills_todo[:FILLS_WALLETS_PER_TICK]
        del self.fills_todo[:FILLS_WALLETS_PER_TICK]
        if self.fills_todo:
            self.last_run.pop("hl_fills", None)  # continue with the next wallets on the next tick
        else:
            self.cursor.d["fills_day"] = today
        for addr in chunk:
            start = max(last.get(addr, 0), now_ms() - DAY_MS) + 1
            try:
                fills = self.hl({"type": "userFillsByTime", "user": addr, "startTime": start}, timeout=30)
            except Exception as e:  # one wallet failing must not stop the others (it is retried tomorrow)
                errs.append(reason(e))
                continue
            fills = sorted(fills, key=lambda f: f.get("time") or 0)
            for f in fills[-FILLS_PER_WALLET:]:
                self.store.add("hl_fills", {"addr": addr, "n_day": len(fills), **{k: f.get(k) for k in
                               ("coin", "px", "sz", "side", "dir", "closedPnl", "fee")}}, event_ts=f.get("time"))
            if fills:
                last[addr] = max(last.get(addr, 0), int(fills[-1].get("time") or 0))
            n += min(len(fills), FILLS_PER_WALLET)
            time.sleep(1.0)  # userFillsByTime is weight-heavy: stay far below 1,200/min
        if chunk and len(errs) == len(chunk):
            raise RuntimeError(f"all fill requests failed ({errs[0]})")
        return n

    def news(self):
        import feedparser
        seen, n, ok, errs = set(self.cursor.d["seen"]), 0, 0, []
        for feed, url in FEEDS.items():
            try:
                parsed = feedparser.parse(self.get(url, allow_redirects=True).content)
                ok += 1
            except Exception as e:
                errs.append(f"{feed} {type(e).__name__}")
                continue
            for e in parsed.entries[:50]:
                link, title = e.get("link"), (e.get("title") or "").strip()
                if not link or not title or short_id(link) in seen:
                    continue
                pub = e.get("published_parsed") or e.get("updated_parsed")
                ts = calendar.timegm(pub) * 1000 if pub else None
                if ts and ts > now_ms():
                    ts = now_ms()  # feeds sometimes post-date items; never record an event time after observation
                self.store.add("news", {"feed": feed, "title": title, "link": link, "ai": "unrated"}, event_ts=ts)
                self.cursor.d["pending_ai"].append({"link": link, "title": title})
                seen.add(short_id(link))
                self.cursor.d["seen"].append(short_id(link))
                n += 1
        self.cursor.d["seen"] = self.cursor.d["seen"][-2500:]
        self.cursor.d["pending_ai"] = self.cursor.d["pending_ai"][-300:]
        if not ok:
            raise RuntimeError("all feeds failed: " + ", ".join(errs))
        return n

    def news_ai(self):
        if not self.cfg.llm_key or self.cursor.locked:  # locked memory: don't re-rate duplicates every run
            self.mark("news_ai", True, 0, disabled=True)
            return None
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        ai = self.cursor.d["ai"]
        if ai.get("date") != day:
            ai.update(date=day, n=0)
        n, pend, failed = 0, self.cursor.d["pending_ai"], False
        while pend and ai["n"] < self.cfg.ai_cap and n < 60:
            batch = pend[:10]
            rated, model = None, None
            for model in self.cfg.ai_models:
                if ai["n"] >= self.cfg.ai_cap:
                    break
                time.sleep(max(0.0, self.ai_last + 4 - time.time()))
                self.ai_last, ai["n"] = time.time(), ai["n"] + 1
                try:
                    rated = parse_ai_ratings(_ai_call(self.cfg, model, ai_prompt([b["title"] for b in batch])), len(batch))
                    break
                except Exception as e:
                    log(f"news AI {model} failed: {type(e).__name__}")
            if not rated:
                failed = True
                break  # headlines stay in the backlog ("unrated") and are rated on a later run
            for i, b in enumerate(batch):
                if i in rated:
                    self.store.add("news_ai", dict(b, model=model, **rated[i]))
                    n += 1
            del pend[:len(batch)]
            pend.extend(b for i, b in enumerate(batch) if i not in rated)  # unrated ones go to the back
        if failed and not n:
            raise RuntimeError(f"AI rating failed ({len(pend)} unrated waiting)")
        self.mark("news_ai", True, n, disabled=False, backlog=len(pend), calls_today=ai["n"])
        return None

    def listings(self):
        seen, n, ok = set(self.cursor.d["listing_seen"]), 0, 0
        for ex, fn in (("okx", self._ls_okx), ("binance", self._ls_binance), ("upbit", self._ls_upbit),
                       ("kucoin", self._ls_kucoin)):
            try:
                items = fn()
                self.mark(f"listings_{ex}", True, len(items))
                ok += 1
            except Exception as e:
                self.mark(f"listings_{ex}", False, err=reason(e))
                continue
            for uid, title, ts in items:
                sid = short_id(f"{ex}:{uid}")
                if sid in seen:
                    continue
                seen.add(sid)
                self.cursor.d["listing_seen"].append(sid)
                self.store.add("listings", {"exchange": ex, "title": title, "coins": title_coins(title),
                                            "type": listing_type(title), "id": uid},
                               event_ts=min(int(ts), now_ms()) if ts else None)
                n += 1
        self.cursor.d["listing_seen"] = self.cursor.d["listing_seen"][-1500:]
        if not ok:
            raise RuntimeError("all listing sources failed")
        return n

    def _ls_okx(self):
        out = []
        for t in ("announcements-new-listings", "announcements-delistings"):
            for d in self.get(f"{OKX}/api/v5/support/announcements", params={"annType": t}).json()["data"][0]["details"]:
                out.append((d["url"], d["title"], d.get("pTime")))
        return out

    def _ls_binance(self):
        out = []
        for cat in (48, 161):
            j = self.get("https://www.binance.com/bapi/composite/v1/public/cms/article/list/query",
                         params={"type": 1, "catalogId": cat, "pageNo": 1, "pageSize": 20}).json()
            for a in j["data"]["catalogs"][0]["articles"]:
                out.append((a["code"], a["title"], a.get("releaseDate")))
        return out

    def _ls_upbit(self):
        j = self.get("https://api-manager.upbit.com/api/v1/announcements",
                     params={"os": "web", "page": 1, "per_page": 20, "category": "trade"}).json()
        items = (j.get("data") or {}).get("notices") or []
        return [(a["id"], a["title"], None) for a in items]

    def _ls_kucoin(self):
        j = self.get("https://api.kucoin.com/api/v3/announcements",
                     params={"annType": "new-listings", "lang": "en_US", "pageSize": 20}).json()
        return [(a["annId"], a["annTitle"], a.get("cTime")) for a in j["data"]["items"]]

    def whales_collect(self):
        if self.whales is None:
            return 0
        done = self.whales.pop_done(now_ms())
        for (coin, bucket), a in done.items():
            self.store.add("whales", dict(coin=coin, bucket=bucket, **a), event_ts=bucket + 300_000)
        if time.time() - self.whales.last_msg > 600:
            raise RuntimeError("no trades from the stream for 10 min")
        self.mark("whales", True, len(done), stream_msgs=self.whales.msgs)
        return None

    def liquidations(self):
        if not self.ctval:
            for i in self.get(f"{OKX}/api/v5/public/instruments", params={"instType": "SWAP"}).json()["data"]:
                if i.get("settleCcy") == "USDT":
                    self.ctval[i["uly"]] = _f(i["ctVal"])
        last, agg, fails = self.cursor.d["liq_last"], {}, 0
        for c in self.coins:
            uly = c.replace("/", "-")
            if uly not in self.ctval:
                continue
            try:
                j = self.get(f"{OKX}/api/v5/public/liquidation-orders",
                             params={"instType": "SWAP", "uly": uly, "state": "filled"}).json()
            except Exception:
                fails += 1
                continue
            for d in (j.get("data") or [{}])[0].get("details", []):
                ts = int(d["ts"])
                if ts <= last.get(uly, 0):
                    continue
                a = agg.setdefault((uly, ts // 300_000 * 300_000), {"long_usd": 0.0, "short_usd": 0.0, "n": 0, "max_ts": 0})
                a[f"{d.get('posSide', 'long')}_usd"] = round(a.get(f"{d.get('posSide', 'long')}_usd", 0.0)
                                                             + float(d["sz"]) * self.ctval[uly] * float(d["bkPx"]), 2)
                a["n"] += 1
                a["max_ts"] = max(a["max_ts"], ts)
            time.sleep(0.1)
        for (uly, bucket), a in agg.items():
            last[uly] = max(last.get(uly, 0), a["max_ts"])
            self.store.add("liquidations", {"coin": uly.split("-")[0], "bucket": bucket, **a},
                           event_ts=min(a.pop("max_ts"), now_ms()))
        if fails and fails >= len(self.ctval):
            raise RuntimeError("all liquidation requests failed")
        return len(agg)

    def longshort(self):
        n = 0
        for c in dict.fromkeys(["BTC", "ETH"] + [x.split("/")[0] for x in self.coins[:10]]):
            j = self.get(f"{OKX}/api/v5/rubik/stat/contracts/long-short-account-ratio",
                         params={"ccy": c, "period": "5m"}).json()
            if j.get("data"):
                ts, ratio = j["data"][0][:2]
                self.store.add("longshort", {"coin": c, "ratio": _f(ratio), "src_name": "okx"}, event_ts=ts)
                n += 1
            time.sleep(0.6)  # OKX rubik limit: 5 requests / 2 s
        return n

    def context(self):
        n, errs = 0, []
        try:
            d = self.get("https://api.alternative.me/fng/", params={"limit": 1}).json()["data"][0]
            self.store.add("context", {"kind": "fear_greed", "value": int(d["value"]),
                                       "label": d["value_classification"]}, event_ts=int(d["timestamp"]) * 1000)
            n += 1
        except Exception as e:
            errs.append(f"fng {type(e).__name__}")
        try:
            g = self.get("https://api.coingecko.com/api/v3/global").json()["data"]
            self.store.add("context", {"kind": "global", "btc_dominance": g["market_cap_percentage"]["btc"],
                                       "total_mcap_usd": g["total_market_cap"]["usd"]},
                           event_ts=min(int(g.get("updated_at", 0)) * 1000, now_ms()) or None)
            n += 1
        except Exception as e:
            errs.append(f"coingecko {type(e).__name__}")
        try:
            assets = self.get("https://stablecoins.llama.fi/stablecoins", params={"includePrices": "false"},
                              timeout=30).json()["peggedAssets"]
            total = sum(_f((a.get("circulating") or {}).get("peggedUSD")) or 0 for a in assets)
            self.store.add("context", {"kind": "stablecoins", "total_usd": round(total)})
            n += 1
        except Exception as e:
            errs.append(f"defillama {type(e).__name__}")
        if not n:
            raise RuntimeError("; ".join(errs))
        return n


def run(cfg):
    rec = Recorder(cfg)

    def on_term(signum, frame):
        raise SystemExit(f"signal {signum}")
    signal.signal(signal.SIGTERM, on_term)
    log(f"Recorder starting: run {cfg.run_minutes:g} min, push={'on' if cfg.push else 'off'}, "
        f"encryption={'on' if cfg.data_key else 'OFF (add DATA_KEY)'}, AI={'on' if cfg.llm_key else 'off'}")
    rec.start()
    end = time.time() + cfg.run_minutes * 60
    try:
        while not cfg.run_minutes or time.time() < end:
            t0 = time.time()
            rec.tick(t0)
            time.sleep(max(0.0, min(cfg.tick - (time.time() - t0), end - time.time() if cfg.run_minutes else cfg.tick)))
    finally:
        rec.finish()
        log("Recorder run finished")
    return rec


def probe(cfg):
    tmp = tempfile.mkdtemp(prefix="rec-probe-")
    rec = Recorder(RecConfig(**dict(cfg.__dict__, out_dir=tmp, push=False, dry_run=True)))
    rec.start()
    rec.cfg.n_wallets = 3
    time.sleep(20)
    for name, fn in rec.tasks:
        rec.run_task(name, fn)
    for name, s in sorted(rec.cursor.d["status"].items()):
        n = sum(x[1] for x in s.get("counts", []))
        print(f"{'PASS' if s.get('last_ok') and not s.get('err') else 'FAIL'}  {name}: {n} records {s.get('err', '')}",
              flush=True)
    rec.stop.set()
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="War Room edge-data recorder")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--push-only", action="store_true")
    g.add_argument("--probe", action="store_true")
    a = ap.parse_args(argv)
    cfg = RecConfig()
    try:
        if a.probe:
            return probe(cfg)
        if a.push_only:
            if cfg.push and os.path.isdir(cfg.out_dir):
                DataBranch(cfg.repo_dir, cfg.out_dir).push()
            return 0
        run(cfg)
        return 0
    except BaseException as e:
        if isinstance(e, SystemExit) and str(e).startswith("signal"):
            return 0
        log(f"FATAL: {err_text(e)}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
