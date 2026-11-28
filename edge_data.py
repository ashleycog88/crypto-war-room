"""Phase 5 data builder (pod only): free Binance archives 2020-01 → 2026-11 and monthly point-in-time coin sets.
Run: python edge_data.py [list|spot|perp|funding|sets|5m|all]. Cache: backtest_data/edge_v2/ (gitignored)."""
import io
import json
import os
import sys
import time
import zipfile
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import requests

import backtest as bt
import war_room_bot as wr

DIR = os.path.join(bt.DATA, "edge_v2")
S3 = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
H, DAY = 3_600_000, 86_400_000
FIRST, LAST_MONTH = "2020-01", "2026-10"  # complete monthly archives; November 2026 comes from daily archives
END = bt.ms("2026-11-28")
NOV_DAYS = [(datetime(2026, 11, 1) + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(27)]
TOP_MAJ, TOP_50 = 20, 50
_S = requests.Session()
_S.mount("https://", requests.adapters.HTTPAdapter(pool_connections=64, pool_maxsize=64))
T0 = time.time()


def log(*a):
    print(f"[{time.time() - T0:7.1f}s]", *a, flush=True)


def get(url):
    """bytes, or None for 404 / persistent failure (logged as missing, never invented)."""
    for k in range(4):
        try:
            r = _S.get(url, timeout=40)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.content
        except Exception:
            time.sleep(1 + 2 * k)
    return None


def s3_list(prefix, dirs=True):
    out, marker = [], ""
    while True:
        raw = get(f"{S3}?prefix={prefix}&delimiter=/&marker={marker}")
        if raw is None:
            return out
        root = ET.fromstring(raw)
        ns = {"s": root.tag.split("}")[0].strip("{")}
        tag = "s:CommonPrefixes/s:Prefix" if dirs else "s:Contents/s:Key"
        out += [e.text for e in root.findall(tag, ns)]
        if root.findtext("s:IsTruncated", namespaces=ns) != "true":
            return out
        marker = root.findtext("s:NextMarker", namespaces=ns) or out[-1]


def path(market, tf, sym):
    return os.path.join(DIR, market, tf, f"{sym}.npz")


def stock_tokens():
    """Tokenized stocks/ETFs carry Binance permission group TRD_GRP_261 (cached list; same rule as the live bot)."""
    f = os.path.join(DIR, "stock_tokens.json")
    if not os.path.exists(f):
        raw = get("https://data-api.binance.vision/api/v3/exchangeInfo?permissions=%5B%22" + wr.STOCK_TOKEN_GROUP + "%22%5D")
        os.makedirs(DIR, exist_ok=True)
        json.dump(sorted({x["baseAsset"] for x in json.loads(raw)["symbols"]}), open(f, "w"))
    return set(json.load(open(f)))


def symbols(market):
    """Every USDT symbol that ever had archives (later-delisted ones included), minus excluded bases."""
    f = os.path.join(DIR, f"symbols_{market}.json")
    if os.path.exists(f):
        return json.load(open(f))
    pre = "data/spot/monthly/klines/" if market == "spot" else "data/futures/um/monthly/klines/"
    syms = sorted(p[len(pre):].strip("/") for p in s3_list(pre))
    stocks = stock_tokens()
    keep = [s for s in syms if s.endswith("USDT") and not wr.is_excluded_base(base_of(s)) and base_of(s) not in stocks]
    os.makedirs(DIR, exist_ok=True)
    json.dump(keep, open(f, "w"))
    return keep


def base_of(sym):
    return sym[:-4]


def parse(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        return bt._parse_kline_csv(z.read(z.namelist()[0]))


def clean(a, tf_ms):
    """REQ-506: drop invalid candles (no invented prices); keep one row per open time, aligned to the grid."""
    if not len(a):
        return a
    a = a[np.argsort(a[:, 0], kind="stable")]
    a = a[np.concatenate([[True], np.diff(a[:, 0]) > 0])]
    o, h, l, c, v = a[:, 1], a[:, 2], a[:, 3], a[:, 4], a[:, 5]
    ok = (np.isfinite(a).all(axis=1) & (o > 0) & (l > 0) & (h >= np.maximum(o, c)) & (l <= np.minimum(o, c))
          & (v >= 0) & (a[:, 0] % tf_ms == 0))
    return a[ok]


def build_klines(market, sym, tf="1h", only=None):
    """All monthly archives FIRST..LAST_MONTH that exist + November 2026 daily; returns missing months (gaps)."""
    out = path(market, tf, sym)
    if os.path.exists(out):
        return None
    root = "data/spot" if market == "spot" else "data/futures/um"
    keys = s3_list(f"{root}/monthly/klines/{sym}/{tf}/", dirs=False)
    months = sorted(k.rsplit("-", 2)[-2] + "-" + k.rsplit("-", 1)[-1][:2] for k in keys if k.endswith(".zip"))
    months = [m for m in months if FIRST <= m <= LAST_MONTH and (only is None or m in only)]
    parts, missing = [], []
    for m in months:
        raw = get(f"https://data.binance.vision/{root}/monthly/klines/{sym}/{tf}/{sym}-{tf}-{m}.zip")
        if raw is None:
            missing.append(m)
        else:
            parts.append(parse(raw))
    if months and months[-1] == LAST_MONTH and (only is None or "2026-11" in only):  # November: daily archives
        for d in NOV_DAYS:
            raw = get(f"https://data.binance.vision/{root}/daily/klines/{sym}/{tf}/{sym}-{tf}-{d}.zip")
            if raw is not None:
                parts.append(parse(raw))
    tf_ms = {"1h": H, "5m": 300_000}[tf]
    a = clean(np.concatenate(parts), tf_ms) if parts else np.zeros((0, 6))
    a = a[a[:, 0] < END]
    if months and only is None:  # internal holes (months listed between first and last that are absent) are reported, not filled
        allm = pd.period_range(months[0], months[-1], freq="M").strftime("%Y-%m").tolist()
        missing += [m for m in allm if m not in months]
    os.makedirs(os.path.dirname(out), exist_ok=True)
    np.savez_compressed(out, t=a[:, 0].astype(np.int64), x=a[:, 1:6].astype(np.float32),
                        missing=np.array(sorted(set(missing))))
    return sym


def load(market, sym, tf="1h"):
    p = path(market, tf, sym)
    if not os.path.exists(p):
        return None
    z = np.load(p)
    if not len(z["t"]):
        return None
    return np.column_stack([z["t"].astype(float), z["x"].astype(float)])


def missing_months(market, sym, tf="1h"):
    p = path(market, tf, sym)
    return np.load(p)["missing"].tolist() if os.path.exists(p) else []


def build_funding(sym):
    out = os.path.join(DIR, "funding", f"{sym}.npz")
    if os.path.exists(out):
        return None
    keys = s3_list(f"data/futures/um/monthly/fundingRate/{sym}/", dirs=False)
    months = sorted(k.rsplit("-", 2)[-2] + "-" + k.rsplit("-", 1)[-1][:2] for k in keys if k.endswith(".zip"))
    rows = []
    for m in [m for m in months if FIRST <= m <= LAST_MONTH]:
        raw = get(f"https://data.binance.vision/data/futures/um/monthly/fundingRate/{sym}/{sym}-fundingRate-{m}.zip")
        if raw is None:
            continue
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            df = pd.read_csv(io.BytesIO(z.read(z.namelist()[0])))
        r, hrs = df.iloc[:, -1].astype(float), df.iloc[:, 1].astype(float).clip(lower=1)
        rows += list(zip(df.iloc[:, 0].astype(np.int64), r, r * 8 / hrs))
    rows = sorted(set(rows))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    np.savez_compressed(out, t=np.array([x[0] for x in rows], np.int64), fr=np.array([x[1] for x in rows]),
                        fn=np.array([x[2] for x in rows]), src=np.array(["binance"] * len(rows)))
    return sym


def top_up_funding(perp_bases):
    """November 2026 has no monthly funding archive yet: fill from Hyperliquid (hourly, 8h-equivalent) for the
    coins that need it; source recorded per settlement."""
    uni = bt.hl_universe()
    n = 0
    for b in perp_bases:
        sym = b + "USDT"
        f = os.path.join(DIR, "funding", f"{sym}.npz")
        if not os.path.exists(f):
            continue
        z = dict(np.load(f))
        if "hl" in set(z["src"].tolist()) or not len(z["t"]) or z["t"][-1] < bt.ms("2026-10-25"):
            continue
        name = uni.get(b) or uni.get(b.replace("1000", "k", 1) if b.startswith("1000") else b)
        if not name:
            continue
        ex = [(t, r / 8, r) for t, r in bt.hl_funding(name, int(z["t"][-1]) + 1, END)]
        if ex:
            z["t"] = np.concatenate([z["t"], [x[0] for x in ex]]).astype(np.int64)
            z["fr"], z["fn"] = np.concatenate([z["fr"], [x[1] for x in ex]]), np.concatenate([z["fn"], [x[2] for x in ex]])
            z["src"] = np.concatenate([z["src"], ["hl"] * len(ex)])
            np.savez_compressed(f, **z)
            n += 1
    return n


def load_funding(sym):
    f = os.path.join(DIR, "funding", f"{sym}.npz")
    if not os.path.exists(f):
        return None
    z = np.load(f)
    return (hour_round(z["t"]), z["fr"], z["fn"]) if len(z["t"]) else None


def hour_round(t):
    """Archives stamp settlements a few ms after the hour (e.g. 08:00:00.004): snap them to the hour."""
    return (np.asarray(t, np.int64) + H // 2) // H * H


# ---------------------------------------------------------------- point-in-time coin sets
def month_starts():
    return [bt.ms(f"{p}-01") for p in pd.period_range("2020-02", "2026-11", freq="M").strftime("%Y-%m")]


def build_sets(daily_qv):
    """daily_qv: {base: (day_open_ms array, quote volume array, first_ms)}. For each month start t0: rank coins that
    had traded ≥ 30 days before t0 and traded on ≥ 20 of the last 30 days by trailing 30-day quote volume."""
    sets = {}
    for t0 in month_starts():
        sc = []
        for b, (d, qv, first) in daily_qv.items():
            if first > t0 - 30 * DAY:
                continue
            m = (d >= t0 - 30 * DAY) & (d < t0)
            if m.sum() >= 20:
                sc.append((float(qv[m].sum()), b))
        sc.sort(reverse=True)
        sets[t0] = {"majors": [b for _, b in sc[:TOP_MAJ]], "top50": [b for _, b in sc[:TOP_50]]}
    return sets


def pit_violations(sets, first):
    """REQ-541: a coin selected in a month before it traded (first candle ≥ month start − 30 days)."""
    return sum(1 for t0, s in sets.items() for b in s["top50"] if first[b] > t0 - 30 * DAY)


def segments(a, name):
    """A ticker that stops trading for > 30 days, or for > 2 days with a price jump of more than 3× either way across
    the gap (relaunch or redenomination: LUNA → LUNA 2.0, COCOS ×1000, ...), continues as a different coin."""
    if a is None or not len(a):
        return []
    gap = np.diff(a[:, 0])
    ratio = a[1:, 1] / a[:-1, 4]
    cut = np.nonzero((gap > 30 * DAY) | ((gap > 2 * DAY) & ((ratio > 3) | (ratio < 1 / 3))))[0] + 1
    parts = np.split(a, cut)
    return [(name if k == 0 else f"{name}#{k + 1}", x) for k, x in enumerate(parts)]


def spot_segments(base, tf="1h"):
    return segments(load("spot", base + "USDT", tf), base)


def daily_qv(a):
    g = (a[:, 0] // DAY).astype(np.int64)
    qv = pd.Series(a[:, 5] * a[:, 4]).groupby(g).sum()
    return qv.index.to_numpy() * DAY, qv.to_numpy(), int(a[0, 0])


def sets_file():
    return os.path.join(DIR, "pit_sets.json")


def make_sets():
    qv = {}
    for s in symbols("spot"):
        for name, a in spot_segments(base_of(s)):
            qv[name] = daily_qv(a)
    sets = build_sets(qv)
    first = {b: r[2] for b, r in qv.items()}
    last = {b: int(r[0][-1]) for b, r in qv.items()}
    json.dump({"sets": {str(k): v for k, v in sets.items()}, "first": first, "last": last}, open(sets_file(), "w"))
    log("sets built:", len(sets), "months; PIT violations:", pit_violations(sets, first))
    return sets


def load_sets():
    z = json.load(open(sets_file()))
    return {int(k): v for k, v in z["sets"].items()}, z["first"], z["last"]


def run_pool(fn, items, label):
    done = 0
    with ThreadPoolExecutor(40) as ex:
        for _ in ex.map(fn, items):
            done += 1
            if done % 50 == 0:
                log(label, done, "/", len(items))
    log(label, "done", len(items))


def main(argv):
    step = argv[0] if argv else "all"
    if step in ("list", "all"):
        log("spot symbols", len(symbols("spot")), "perp symbols", len(symbols("perp")))
    if step in ("spot", "all"):
        run_pool(lambda s: build_klines("spot", s), symbols("spot"), "spot 1h")
    if step in ("perp", "all"):
        run_pool(lambda s: build_klines("perp", s), symbols("perp"), "perp 1h")
    if step in ("funding", "all"):
        run_pool(build_funding, symbols("perp"), "funding")
    if step in ("sets", "all"):
        make_sets()
    if step in ("5m", "all"):
        sets, _, _ = load_sets()
        need = {}  # 5m only for the months a coin was a major, plus the month before (threshold warm-up)
        for t0, s in sets.items():
            m = pd.Timestamp(t0, unit="ms").strftime("%Y-%m")
            prev = (pd.Timestamp(t0, unit="ms") - pd.DateOffset(months=1)).strftime("%Y-%m")
            for b in s["majors"]:
                need.setdefault(b.split("#")[0], set()).update({m, prev})
        log("ever-majors:", len(need), "coin-months:", sum(len(v) for v in need.values()))
        run_pool(lambda b: build_klines("spot", b + "USDT", "5m", need[b]), sorted(need), "spot 5m")
    if step in ("topup", "all"):
        sets, _, _ = load_sets()
        need = sorted({b for t0, s in sets.items() if t0 >= bt.ms("2026-10-01") for b in s["top50"]})
        perp = {base_of(s) for s in symbols("perp")}
        cand = [b for b in need if b in perp] + [f"1000{b}" for b in need if f"1000{b}" in perp]
        log("funding top-up (Hyperliquid, November):", top_up_funding(cand), "coins")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
