#!/usr/bin/env python3
"""Phase 4 Step C1 data: stratified Hyperliquid wallet universe + fill histories (cached, rate-limited).
Run: python step_c_data.py wallets   (≈ 30-60 min, polite: ≤ ~1000 weight/min, cache in backtest_data/hl_fills/)"""
import gzip, json, os, sys, time

import numpy as np
import requests

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "backtest_data")
HL = "https://api.hyperliquid.xyz/info"
LB_URL = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
FILL_START = 1745712000000  # 2025-04-27 (30-day ranking window before the first selection on 2025-05-27)
PER_DECILE, SEED = 60, 4101
HLP = {"0xdfc24b077bc1425ad1dea75bcb6f8158e10df303", "0x010461c14e146ac35fe42271bdc1134ee31c703a"}
MIN_GAP_S = 1.3  # userFillsByTime weight 20 (+ per returned items) → ≤ ~1000 weight/min


def log(m):
    print(time.strftime("%H:%M:%S"), m, flush=True)


def universe():
    """Stratified random sample across all-time-PnL deciles (winners AND losers); exclusions are pre-registered."""
    path = os.path.join(DATA, "hl_leaderboard.json")
    if not os.path.exists(path):
        open(path, "wb").write(requests.get(LB_URL, timeout=120).content)
    rows = json.load(open(path))["leaderboardRows"]
    out, excl = [], {"vault/HLP": 0, "market-maker turnover": 0}  # emptied (blown-up) accounts are KEPT
    for r in rows:
        w = dict(r["windowPerformances"])
        av, vlm_m = float(r["accountValue"]), float(w["month"]["vlm"])
        name = (r.get("displayName") or "").lower()
        if r["ethAddress"].lower() in HLP or "vault" in name or "hlp" in name:
            excl["vault/HLP"] += 1
        elif vlm_m > 100e6 or (av >= 10 and vlm_m > 500 * av):  # market maker / HFT turnover
            excl["market-maker turnover"] += 1
        else:
            out.append({"addr": r["ethAddress"].lower(), "av": av, "pnl_all": float(w["allTime"]["pnl"]),
                        "vlm_m": vlm_m})
    out.sort(key=lambda x: x["pnl_all"])
    dec = np.array_split(np.arange(len(out)), 10)
    rng = np.random.default_rng(SEED)
    sample = []
    for d, ix in enumerate(dec):
        for i in rng.choice(ix, min(PER_DECILE, len(ix)), replace=False):
            sample.append(dict(out[i], decile=d + 1))
    return sample, {"leaderboard_rows": len(rows), "eligible": len(out), "excluded": excl,
                    "sampled": len(sample), "losing_share": float(np.mean([s["pnl_all"] < 0 for s in sample]))}


_last = [0.0]


def post(body, timeout=30):
    for attempt in range(6):
        wait = MIN_GAP_S - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
        try:
            r = requests.post(HL, json=body, timeout=timeout)
            if r.status_code == 429:
                time.sleep(20 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            time.sleep(5 * (attempt + 1))
    raise RuntimeError("Hyperliquid not answering")


def fills(addr, end_ms):
    """All fills the API still serves (≤ 10,000 most recent) from FILL_START; coverage start recorded honestly."""
    path = os.path.join(DATA, "hl_fills", f"{addr}.json.gz")
    if os.path.exists(path):
        return json.load(gzip.open(path))
    out, t = [], FILL_START
    for _ in range(6):
        page = post({"type": "userFillsByTime", "user": addr, "startTime": t, "endTime": end_ms})
        out += page
        if len(page) < 2000:
            break
        t = int(page[-1]["time"]) + 1
    keep = ("coin", "px", "sz", "side", "time", "startPosition", "dir", "closedPnl", "fee", "crossed")
    doc = {"addr": addr, "end": end_ms, "n": len(out), "capped": len(out) >= 10000,
           "first": int(out[0]["time"]) if out else None, "fills": [{k: f.get(k) for k in keep} for f in out]}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with gzip.open(path + ".tmp", "wt") as g:
        json.dump(doc, g)
    os.replace(path + ".tmp", path)
    return doc


EV_START = 1732665600000  # 2024-11-27


def _get(url, params):
    for attempt in range(5):
        try:
            r = requests.get(url, params=params, timeout=30)
            if r.status_code == 200:
                return r.json()
        except requests.RequestException:
            pass
        time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"{url} not answering")


def announcements():
    """Raw announcement titles + publish times (Binance CMS, OKX, KuCoin; Upbit is 403 from here)."""
    out = []
    for cat in (48, 161, 49):  # 49 = latest news: only its Monitoring Tag notices are kept
        for page in range(1, 200):
            arts = _get("https://www.binance.com/bapi/composite/v1/public/cms/article/list/query",
                        {"type": 1, "catalogId": cat, "pageNo": page, "pageSize": 50})["data"]["catalogs"][0]["articles"]
            out += [{"ex": "binance", "cat": cat, "t": int(a["releaseDate"]), "title": a["title"]} for a in arts
                    if cat != 49 or "monitoring tag" in a["title"].lower()]
            if not arts or arts[-1]["releaseDate"] < EV_START:
                break
            time.sleep(0.5)
    for typ in ("announcements-new-listings", "announcements-delistings"):
        for page in range(1, 100):
            d = _get("https://www.okx.com/api/v5/support/announcements", {"annType": typ, "page": page})["data"]
            det = d[0]["details"] if d else []
            out += [{"ex": "okx", "cat": typ, "t": int(a["pTime"]), "title": a["title"]} for a in det]
            if not det or page >= int(d[0].get("totalPage", 1)):
                break
            time.sleep(0.5)
    for typ in ("new-listings", "delistings"):
        for page in range(1, 100):
            d = _get("https://api.kucoin.com/api/v3/announcements",
                     {"annType": typ, "lang": "en_US", "pageSize": 50, "currentPage": page,
                      "startTime": EV_START, "endTime": int(time.time() * 1000)})["data"]
            out += [{"ex": "kucoin", "cat": typ, "t": int(a["cTime"]), "title": a["annTitle"]} for a in d["items"]]
            if page >= int(d.get("totalPage", 1)):
                break
            time.sleep(0.5)
    out = [x for x in out if x["t"] >= EV_START]
    json.dump(out, open(os.path.join(DATA, "announcements.json"), "w"))
    log(f"announcements: {len(out)}")


def main():
    if sys.argv[1:] == ["events"]:
        return announcements()
    sample, stats = universe()
    os.makedirs(os.path.join(DATA, "hl_fills"), exist_ok=True)
    json.dump({"sample": sample, "stats": stats}, open(os.path.join(DATA, "hl_universe.json"), "w"))
    log(f"universe: {stats}")
    end = int(os.environ.get("C_END_MS", "1795737600000"))  # 2026-11-27
    for k, s in enumerate(sample):
        d = fills(s["addr"], end)
        if k % 25 == 0:
            log(f"{k + 1}/{len(sample)} wallets · last n={d['n']} capped={d['capped']}")
    log("done")


if __name__ == "__main__":
    sys.exit(main())
