"""Crash-watch replay 2026-01 → 2026-11 on Binance 5m spot archives for each month's point-in-time top 50 (pod only).
Uses crash_watch.py's own scan/commit; assumes a scan right after every 5m close and that every send succeeds.
Run: python crash_replay.py → private_reports/edge_v2/crash_replay.md (gitignored)."""
import json
import os
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

import crash_watch as cw
import edge_data as ed

D = os.path.join(ed.DIR, "spot", "5m_2026")
MONTHS = [f"2026-{m:02d}" for m in range(1, 12)]
START, END = ed.bt.ms("2026-01-01"), ed.END
TH = {"big": 4.0, "alt": 12.0, "market": 5.0, "count": 15, "oi": 5.0}


def fetch(base):
    out = os.path.join(D, f"{base}USDT.npz")
    if os.path.exists(out):
        return
    sym, parts = base + "USDT", []
    for m in MONTHS[:-1]:
        raw = ed.get(f"https://data.binance.vision/data/spot/monthly/klines/{sym}/5m/{sym}-5m-{m}.zip")
        if raw is not None:
            parts.append(ed.parse(raw))
    for d in ed.NOV_DAYS:
        raw = ed.get(f"https://data.binance.vision/data/spot/daily/klines/{sym}/5m/{sym}-5m-{d}.zip")
        if raw is not None:
            parts.append(ed.parse(raw))
    a = ed.clean(np.concatenate(parts), cw.FIVE_MIN) if parts else np.zeros((0, 6))
    os.makedirs(D, exist_ok=True)
    np.savez_compressed(out, t=a[:, 0].astype(np.int64), x=a[:, 1:6].astype(np.float32))


def main(over=None):
    th = dict(TH, **(over or {}))
    sets, _, _ = ed.load_sets()
    months = {m: sets[ed.bt.ms(m + "-01")]["top50"] for m in MONTHS}
    names = sorted({b.split("#")[0] for v in months.values() for b in v if "#" not in b})
    with ThreadPoolExecutor(32) as ex:
        list(ex.map(fetch, names))
    G = np.arange(START, END, cw.FIVE_MIN)
    O = np.full((len(names), len(G)), np.nan)
    C = np.full_like(O, np.nan)
    for i, b in enumerate(names):
        z = np.load(os.path.join(D, f"{b}USDT.npz"))
        ix = np.searchsorted(G, z["t"])
        ok = (ix < len(G)) & (G[np.clip(ix, 0, len(G) - 1)] == z["t"])
        O[i, ix[ok]], C[i, ix[ok]] = z["x"][ok, 0], z["x"][ok, 3]
    F = np.full_like(O, np.nan)
    F[:, 11:] = (C[:, 11:] / O[:, :-11] - 1) * 100
    win_ok = pd.DataFrame(np.isfinite(C).T).rolling(12).sum().to_numpy().T == 12  # 12 contiguous closed candles
    F[~win_ok] = np.nan
    st, per_month, kinds, coins_hit = cw.new_state(), Counter(), Counter(), Counter()
    month_of = pd.to_datetime(G + cw.FIVE_MIN, unit="ms").strftime("%Y-%m").to_numpy()
    for k in range(11, len(G)):
        col = F[:, k]
        cand = np.nonzero(np.isfinite(col) & (col <= -min(th["big"], th["market"])))[0]
        if not len(cand):
            continue
        top = set(months.get(month_of[k], []))
        now_ms = int(G[k] + cw.FIVE_MIN)
        falls = {names[i]: (float(col[i]), float(C[i, k]), now_ms) for i in cand if names[i] in top}
        res = cw.scan(st, now_ms / 1000 + 30, falls, {}, th, sorted(top))
        if res:
            cw.commit(st, res[0], res[1], now_ms / 1000 + 30, th)
            per_month[month_of[k]] += 1
            kinds[res[0]] += 1
            for it in res[1]:
                coins_hit[it["coin"]] += 1
    out = {"per_month": dict(sorted(per_month.items())), "kinds": dict(kinds), "top_coins": coins_hit.most_common(10),
           "coins": len(names), "max_month": max(per_month.values()) if per_month else 0}
    L = ["# Crash-watch replay 2026-01 → 2026-11-27 (private)", "",
         f"Binance 5m spot archives for each month's point-in-time top 50 ({len(names)} coins); default thresholds "
         "(BTC/ETH −4%, others −12%, market-wide 15 coins −5%), 6 h cooldown, max 3 per hour. A scan after every 5m "
         "close, every send succeeds, no open-interest data in the replay.", "",
         "| month | messages |", "|---|---|"] + [f"| {m} | {per_month.get(m, 0)} |" for m in MONTHS] + \
        ["", f"Total {sum(per_month.values())}: {dict(kinds)}; most-hit coins: {coins_hit.most_common(10)}.",
         f"Q-60 (≤ 30 a month): {'met' if out['max_month'] <= 30 else 'NOT met'} — busiest month {out['max_month']}."]
    if over:
        print(json.dumps({"over": over, **out}))
        return 0
    rp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "private_reports", "edge_v2")
    open(os.path.join(rp, "crash_replay.md"), "w").write("\n".join(L) + "\n")
    json.dump(out, open(os.path.join(rp, "crash_replay.json"), "w"), indent=1)
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main(json.loads(sys.argv[1]) if len(sys.argv) > 1 else None))
