#!/usr/bin/env python3
"""Phase 4 Part C research (private outputs): S6 funding factor, S7 CTREND-style trend factor, S8 vol-managed
momentum. Daily Binance candles from research.py's cache; same split, costs, PASS rules and stress as Phase 3.
Run: python factors.py → private_reports/factors/ (gitignored)."""
import json, os, sys, time, zlib

import numpy as np
import pandas as pd

import backtest as bt
import research as rs

OUT = os.path.join(rs.ROOT, "private_reports", "factors")
DAY = rs.TF["1d"]
N_RANDOM, SEED = 1000, 20261129
TARGET_VOL = 40.0  # % per year, pre-registered for S8
TRAIN, UNSEEN = (rs.START, rs.SPLIT), (rs.SPLIT, rs.END)
GRID = {
    "S6": [{"look": lk, "q": q} for lk in (3, 7, 14) for q in (3, 5)],
    "S7": [{"combine": c, "reb": r, "port": pt} for c in ("rank", "regress") for r in (7, 30) for pt in ("long", "ls")],
    "S8": [{"variant": v, "win": w, "reb": r} for v in ("long", "ls") for w in (28, 84) for r in (1, 7)],
}
SOURCES = {
    "S6": "Cross-sectional funding factor from the 2026-11-28 literature scan (long low-funding / short high-funding "
          "perps); related: Schmeling, Schrimpf & Todorov, 'Crypto Carry', BIS Working Paper 1087 (2023).",
    "S7": "Fieberg et al., 'A Trend Factor for the Cross Section of Cryptocurrency Returns', Journal of Financial and "
          "Quantitative Analysis (2024) — CTREND.",
    "S8": "Volatility-managed crypto momentum, Financial Markets and Portfolio Management (2025); method from Barroso & "
          "Santa-Clara, 'Momentum has its moments', Journal of Financial Economics (2015).",
}
DIFFS = {
    "S6": "We use Binance perps in 2 small sets (20 majors, today's top 50), 2 years only, weekly rebalancing, equal "
          "weights, real Binance funding (Hyperliquid/OKX fill the last month). The spec's rule is 7-day funding and thirds; "
          "the pre-registered grid also tries 3/14 days and fifths (the spec's exact setting is in the appendix).",
    "S7": "The paper combines 28 technical signals with rolling cross-sectional elastic-net/OLS on a broad universe over "
          "many years. We use 10 signals (price ÷ moving average for 3–200 days, volume averages), combined by a plain "
          "rank average (price signals only, sign fixed in advance) or a rolling 52-week Fama-MacBeth regression on "
          "ranks; quintiles of 20 or 50 coins are only 4 or 10 coins.",
    "S8": "The paper scales a broad cross-sectional momentum portfolio; we scale Phase 3's S5 (top-3, BTC filter) and a "
          "long-short thirds variant. Target volatility 40%/year, fixed in advance; exposure capped at 1.0. The paper sets the "
          "target relative to the strategy's own volatility: our long-short variant's volatility stays below 40%, so its "
          "scale is ≈ 1.0 and it is plain (unscaled) momentum; its 28- and 84-day windows give identical results.",
}


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def rng_for(*k):
    return np.random.default_rng([SEED, zlib.crc32(json.dumps(k).encode())])


# ---------------------------------------------------------------- data matrices (coins × days, past-only signals)
def matrices(coins):
    days, btc, sma50, O, C, FC = rs.s5_matrix(coins)
    V = np.full_like(O, np.nan)
    for ci, c in enumerate(coins):
        d = c.bars["1d"]
        ix = np.searchsorted(days, d[:, 0])
        m = (ix < len(days)) & (days[np.clip(ix, 0, len(days) - 1)] == d[:, 0])
        V[ci, ix[m]] = d[m, 5] * d[m, 4]  # quote volume
    Of = pd.DataFrame(O.T).ffill().to_numpy().T  # a coin that stops trading exits at its last open
    return {"coins": coins, "days": days, "btc": btc, "sma50": sma50, "O": O, "Of": Of, "C": C, "FC": np.nan_to_num(pd.DataFrame(FC.T).ffill().to_numpy().T),
            "V": V}


def funding_signal(mx, look):
    """Average 8h-equivalent funding settled in the `look` days up to each daily close (known at the close)."""
    out = np.full_like(mx["C"], np.nan)
    close = mx["days"] + DAY
    for ci, c in enumerate(mx["coins"]):
        if len(c.ft) < 20:
            continue
        cs = np.concatenate([[0.0], np.cumsum(c.fn)])
        i1, i0 = np.searchsorted(c.ft, close, "right"), np.searchsorted(c.ft, close - look * DAY, "right")
        n = i1 - i0
        out[ci] = np.where(n >= look, (cs[i1] - cs[i0]) / np.maximum(n, 1), np.nan)
    return out


def sma(x, n):
    return pd.DataFrame(x.T).rolling(n).mean().to_numpy().T


def xrank(x):
    """Cross-sectional percentile ranks per day (NaN stays NaN)."""
    return pd.DataFrame(x).rank(axis=0, pct=True).to_numpy()


def trend_signals(mx):
    C, V = mx["C"], mx["V"]
    price = [C / sma(C, n) - 1 for n in (3, 5, 10, 20, 50, 100, 200)]
    vol = [sma(V, n) / sma(V, 200) for n in (5, 20, 50)]
    return [xrank(s) for s in price], [xrank(s) for s in vol]


# ---------------------------------------------------------------- portfolio engine
def first_reb(days):
    return int(np.searchsorted(days, rs.START)) - 1


ANCHOR = rs.START - DAY  # rebalance days are fixed in calendar time (the live paper-watch uses the same days)
WARMUP = 210


def is_reb(days, d, step):
    return d >= WARMUP and days[d] >= ANCHOR and (days[d] - ANCHOR) % (step * DAY) == 0


def schedule(mx, decide, step):
    """Decisions at daily closes d (only data ≤ d); held from open d+1 to the open after the next rebalance."""
    days = mx["days"]
    return [(d, *decide(d)) for d in range(len(days) - step - 1) if is_reb(days, d, step)]


def weights(L, S, scale):
    w = {x: scale / len(L) * (0.5 if S else 1) for x in L}
    w.update({x: -scale / len(S) * 0.5 for x in S})
    return w


def period(mx, d, nxt, w, w_old, model, stress=False):
    """Net % of holding weights w from open d+1 to open nxt+1, after the turnover from w_old (fees + slippage) and,
    for futures (model F), funding (longs pay, shorts receive). Also used by the live paper-watch."""
    Of, FC = mx["Of"], mx["FC"]
    fee = (rs.COST[model]["long"] if model == "S" else rs.COST["F"]["long"]) * (rs.STRESS_FEE if stress else 1)
    slip = rs.STRESS_SLIP if stress else rs.SLIP
    turn = sum(abs(w.get(x, 0) - w_old.get(x, 0)) for x in set(w) | set(w_old))
    gross = sum(v * (Of[x, nxt + 1] / Of[x, d + 1] - 1) * 100 for x, v in w.items())
    fund = sum(v * (FC[x, nxt + 1] - FC[x, d + 1]) for x, v in w.items()) if model == "F" else 0.0
    return gross - fund - turn * (fee + slip), turn


def evaluate(mx, sched, step, model, stress=False, rand=None):
    """Periods with net % after turnover fees, slippage and (model F) funding. rand: matched random legs."""
    days = mx["days"]
    out, w_old, prev_s, prev_r = [], {}, (set(), set()), (set(), set())
    for d, L, S, scale, valid in sched:
        nxt = d + step
        if rand is not None:
            L, S = rand(L, S, valid, prev_s, prev_r)
            prev_s, prev_r = (set(rand.last_s[0]), set(rand.last_s[1])), (set(L), set(S))
        w = weights(L, S, scale)
        net, turn = period(mx, d, nxt, w, w_old, model, stress)
        out.append({"t": int(days[d + 1]), "t_end": int(days[nxt + 1]), "net": net, "in": bool(w), "turn": turn,
                    "scale": scale})
        w_old = w
    return out


class Matched:
    """Random legs that change as many coins as the strategy per rebalance (same turnover, exposure and cash days)."""
    def __init__(self, rng):
        self.rng, self.last_s = rng, (set(), set())

    def __call__(self, L, S, valid, prev_s, prev_r):
        self.last_s = (set(L), set(S))
        legs, taken = [], set()
        for leg, ps, pr in ((L, prev_s[0], prev_r[0]), (S, prev_s[1], prev_r[1])):
            k = len(leg)
            if not k:
                legs.append([])
                continue
            change = k if not pr else len(set(leg) - ps)
            keep = [x for x in self.rng.permutation(sorted(pr)) if x in valid and x not in taken][:max(0, k - change)]
            pool = [x for x in valid if x not in pr and x not in taken and x not in keep] or \
                   [x for x in valid if x not in taken and x not in keep]
            pick = keep + list(self.rng.choice(pool, min(len(pool), k - len(keep)), replace=False))
            taken |= set(pick)
            legs.append([int(x) for x in pick])
        return legs[0], legs[1]


# ---------------------------------------------------------------- the three families
def s6_decide(mx, p):
    fs = funding_signal(mx, p["look"])

    def decide(d):
        ok = np.nonzero(np.isfinite(fs[:, d]) & np.isfinite(mx["O"][:, d + 1]))[0]
        if len(ok) < 2 * p["q"]:
            return [], [], 0.0, set(ok.tolist())
        k = len(ok) // p["q"]
        order = ok[np.argsort(fs[ok, d], kind="stable")]
        return order[:k].tolist(), order[-k:].tolist(), 1.0, set(ok.tolist())  # long low funding, short high
    return decide, 7, "F"


def s7_decide(mx, p, sig):
    price, vol = sig
    X = np.stack(price + vol)  # signals × coins × days (ranks)
    C = mx["C"]

    def score(d):
        if p["combine"] == "rank":
            st = np.stack([s[:, d] for s in price])
            n = np.isfinite(st).sum(axis=0)
            return np.where(n > 0, np.nansum(st, axis=0) / np.maximum(n, 1), np.nan)
        betas = []
        for s_ in range(d - 7, max(d - 7 * 53, 210), -7):  # past cross-sections whose 7-day forward return ended ≤ d
            fwd = C[:, s_ + 7] / C[:, s_] - 1
            xs = X[:, :, s_].T
            m = np.isfinite(fwd) & np.isfinite(xs).all(axis=1)
            if m.sum() < 8:
                continue
            y = pd.Series(fwd[m]).rank(pct=True).to_numpy() - 0.5
            A = np.column_stack([np.ones(m.sum()), xs[m] - 0.5])
            betas.append(np.linalg.lstsq(A, y, rcond=None)[0][1:])
        if len(betas) < 26:
            return np.full(C.shape[0], np.nan)
        return (X[:, :, d].T - 0.5) @ np.mean(betas, axis=0)

    def decide(d):
        sc = score(d)
        ok = np.nonzero(np.isfinite(sc) & np.isfinite(mx["O"][:, d + 1]))[0]
        if len(ok) < 5:
            return [], [], 0.0, set(ok.tolist())
        k = max(1, len(ok) // 5)
        order = ok[np.argsort(-sc[ok], kind="stable")]
        return order[:k].tolist(), (order[-k:].tolist() if p["port"] == "ls" else []), 1.0, set(ok.tolist())
    return decide, p["reb"], ("S" if p["port"] == "long" else "F")


def s8_decide(mx, p):
    C, btc, sma50 = mx["C"], mx["btc"], mx["sma50"]

    def raw(d):
        mom = C[:, d] / C[:, d - 30] - 1
        ok = np.nonzero(np.isfinite(mom) & np.isfinite(mx["O"][:, d + 1]))[0]
        if p["variant"] == "long":
            top, _ = rs.s5_top(C, d, np.isfinite(mx["O"][:, d + 1]), btc, sma50, 3)
            return sorted(top), [], set(ok.tolist())
        if len(ok) < 6:
            return [], [], set(ok.tolist())
        k = len(ok) // 3
        order = ok[np.argsort(-mom[ok], kind="stable")]
        return order[:k].tolist(), order[-k:].tolist(), set(ok.tolist())

    # unscaled strategy's own daily close-to-close returns (holdings from its latest rebalance), for realised vol
    n = C.shape[1]
    ret = np.full(n, np.nan)
    hold = ([], [])
    for d in range(31, n - 1):
        if (mx["days"][d] - ANCHOR) % (p["reb"] * DAY) == 0:  # same calendar anchor as schedule()
            L, S, _ = raw(d)
            hold = (L, S)
        L, S = hold
        r = C[:, d + 1] / C[:, d] - 1
        lr = np.nanmean(r[L]) if L else 0.0
        ret[d + 1] = lr * (0.5 if S else 1) - (0.5 * np.nanmean(r[S]) if S else 0.0)

    def decide(d):
        L, S, ok = raw(d)
        past = ret[d - p["win"] + 1:d + 1]  # returns up to close d only
        rv = np.nanstd(past) * np.sqrt(365) * 100 if np.isfinite(past).sum() >= p["win"] // 2 else np.nan
        scale = float(min(1.0, TARGET_VOL / rv)) if rv and np.isfinite(rv) and rv > 0 else 0.0
        return L, S, scale, ok
    return decide, p["reb"], ("S" if p["variant"] == "long" else "F")


# ---------------------------------------------------------------- evaluation
def judge(mx, fam, p, decide, step, model, label):
    sched = schedule(mx, decide, step)
    ps = evaluate(mx, sched, step, model)
    other = evaluate(mx, sched, step, "F" if model == "S" else "S") if fam != "S6" and model == "S" else None
    st = lambda x, a, b: rs.stats5(x, a, b)
    r = {"combo": p, "model": model, "train": st(ps, *TRAIN), "unseen": st(ps, *UNSEEN),
         "half1": st(ps, rs.SPLIT, rs.HALF), "half2": st(ps, rs.HALF, rs.END),
         "stress": st(evaluate(mx, sched, step, model, stress=True), *UNSEEN),
         "other_model": st(other, *UNSEEN) if other else None,
         "avg_scale": float(np.mean([x[3] for x in sched if x[1] or x[2]] or [0]))}
    return r, sched


def random_pct(mx, sched, step, model, real, rng):
    unseen = [(i, x) for i, x in enumerate(sched) if mx["days"][x[0] + 1] >= rs.SPLIT]
    rets = []
    for _ in range(N_RANDOM):
        ps = evaluate(mx, sched, step, model, rand=Matched(rng))
        rets.append(rs.stats5(ps, *UNSEEN).get("ret_pct", 0.0))
    return float((np.array(rets) < real).mean() * 100), len(unseen)


def verdict(r, pct):
    u, f = r["unseen"], []
    if u.get("n", 0) < 20:
        f.append("< 20 unseen rebalances")
    if not u.get("n") or u["avg"] <= 0:
        f.append("expectancy ≤ 0")
    if u.get("n") and u["pf"] < 1.2:
        f.append("profit factor < 1.2")
    if not r["half1"].get("n") or r["half1"]["ret_pct"] <= 0 or not r["half2"].get("n") or r["half2"]["ret_pct"] <= 0:
        f.append("not positive in both halves")
    if pct is None or pct < 95:
        f.append("does not beat 95% of random portfolios")
    if not r["stress"].get("n") or r["stress"]["avg"] <= 0:
        f.append("not positive in the 1.5× cost stress case")
    return ("PASSED" if not f else "REJECTED"), f


def evaluate_set(coins, label):
    mx = matrices(coins)
    sig = trend_signals(mx)
    out = {"families": {}, "appendix": []}
    for fam in ("S6", "S7", "S8"):
        cands = []
        for p in GRID[fam]:
            decide, step, model = {"S6": lambda: s6_decide(mx, p), "S7": lambda: s7_decide(mx, p, sig),
                                   "S8": lambda: s8_decide(mx, p)}[fam]()
            r, sched = judge(mx, fam, p, decide, step, model, label)
            cands.append((r, sched, step, model))
            out["appendix"].append({"fam": fam, "combo": p, "model": model, "train": r["train"], "unseen": r["unseen"]})
            log(f"{label} {fam} {p}: train {r['train'].get('ret_pct', 0):+.1f}% unseen {r['unseen'].get('ret_pct', 0):+.1f}%")
        ok = [c for c in cands if c[0]["train"].get("n", 0) >= 10] or cands
        r, sched, step, model = max(ok, key=lambda c: c[0]["train"].get("ret_pct", -1e9))  # training only
        r["random_pct"], _ = random_pct(mx, sched, step, model, r["unseen"].get("ret_pct", 0.0),
                                        rng_for(label, fam, r["combo"]))
        r["verdict"], r["fails"] = verdict(r, r["random_pct"])
        out["families"][fam] = r
        log(f"{label} {fam} chosen {r['combo']}: {r['verdict']} {r['fails']}")
    return out


# ---------------------------------------------------------------- report
def f(x, d=2):
    return "—" if x is None else ("∞" if x == float("inf") else f"{x:.{d}f}")


def row(nm, s):
    if not s or not s.get("n"):
        return f"| {nm} | 0 | | | | | |"
    return (f"| {nm} | {s['n']} | {f(s['win'], 0)} | {f(s['avg'], 3)} | {f(s['pf'])} | {f(s['ret_pct'], 1)} | "
            f"{f(s['dd_pct'], 1)} |")


NAMES = {"S6": "Funding factor (long low / short high funding)", "S7": "CTREND-style trend factor",
         "S8": "Volatility-managed momentum"}


def report(res, secs):
    passed = [(lb, k, r) for lb, s in res.items() for k, r in s["families"].items() if r["verdict"] == "PASSED"]
    L = ["# Phase 4 Part C factor research (PRIVATE — gitignored)", ""]
    if passed:
        lb, k, r = max(passed, key=lambda x: x[2]["unseen"]["ret_pct"])
        L += [f"**{len(passed)} setting(s) passed.** Best: {NAMES[k]} on {lb} ({json.dumps(r['combo'])}).",
              f"- It would rebalance about {r['unseen']['per_week']:.1f} times per week.",
              f"- Unseen 8 months after costs: {r['unseen']['ret_pct']:+.1f}% ({r['unseen']['n']} rebalances), "
              f"profit factor {f(r['unseen']['pf'])}, max drop {r['unseen']['dd_pct']:.0f}%.",
              "- Recommendation: paper-watch first; no real money yet.", "- Past results don't guarantee future results."]
    else:
        L += ["**NOTHING passed.** None of the 3 literature factors (funding, CTREND trend, volatility-managed "
              "momentum) passed the honesty rules on the unseen 8 months after costs, on either coin set.",
              "- Best results are listed below, but each failed at least one rule, so none should be traded.",
              "- Recommendation: don't trade these; keep paper-watching and recording data.",
              "- Past results don't guarantee future results."]
    L += ["", f"_Run time {secs / 60:.1f} min. Training {bt.iso(rs.START)[:10]} → {bt.iso(rs.SPLIT)[:10]}, unseen → "
               f"{bt.iso(rs.END)[:10]}. Costs: futures 0.05%/side + funding (long-short, S6), spot 0.10%/side (long-only); "
               f"0.05% slippage per fill; per changed weight. Stress: 1.5× fees, 0.15% slippage. Random portfolios: "
               f"{N_RANDOM} runs, same number of coins per leg, same turnover (coins changed per rebalance), same "
               "exposure scale and cash days._", "", "## Verdicts", "",
          "| coin set | family | chosen setting (training) | fees | verdict | failing rules | unseen return % | rebalances | random pct | stress avg % |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for lb, s in res.items():
        for k, r in s["families"].items():
            L.append(f"| {lb} | {k} | {json.dumps(r['combo'])} | {r['model']} | **{r['verdict']}** | {'; '.join(r['fails'])} | "
                     f"{f(r['unseen'].get('ret_pct'), 1)} | {r['unseen'].get('n', 0)} | {f(r['random_pct'], 1)} | "
                     f"{f(r['stress'].get('avg'), 3)} |")
    for k in ("S6", "S7", "S8"):
        L += ["", f"## {k} — {NAMES[k]}", "", f"Source: {SOURCES[k]}", "", f"Differences from the paper: {DIFFS[k]}"]
        for lb, s in res.items():
            r = s["families"][k]
            L += ["", f"**{lb}: {json.dumps(r['combo'])} ({r['model']} fees) — {r['verdict']}**"
                      + (f" · average exposure scale {r['avg_scale']:.2f}" if k == "S8" else ""), "",
                  "| period | rebalances | win % | avg per period % | PF | total % | max drop % |",
                  "|---|---|---|---|---|---|---|"] + [row(nm, r[nm]) for nm in ("train", "unseen", "half1", "half2")] + \
                 [row("stress (unseen)", r["stress"])] + ([row("unseen, other fee model", r["other_model"])]
                                                          if r.get("other_model") else [])
    L += ["", "## Recommendation", "", "Paper-watch the passing setting for 4 weeks before any real money." if passed else
          "**Nothing passed — don't add any Part C factor to the bot.** Keep the bot simple (quiet paper mode, the two "
          "Phase 3 near misses on paper-watch, the recorder collecting data).",
          "", "## Known limits", "- Two years of data and small coin sets (20 / 50 coins): quintiles are 4 or 10 coins.",
          "- Today's top-50 list has survivorship bias (coins that fell out of the top 50 are missing).",
          "- Monthly rebalancing gives only ~8 unseen rebalances, so it can never meet the ≥ 20 rule; selection on "
          "training return still picked monthly S7 in both sets. The best weekly S7 settings fail too (majors rank/weekly/"
          "long-short: unseen +15.7%, second half −1.4%, random 90; top-50 regression/weekly/long-short: −29%).",
          "", "## Appendix — every combination (training only was used to choose)", "",
          "| coin set | family | setting | fees | train rebalances | train total % | unseen total % |", "|---|---|---|---|---|---|---|"]
    for lb, s in res.items():
        for a in s["appendix"]:
            L.append(f"| {lb} | {a['fam']} | {json.dumps(a['combo'])} | {a['model']} | {a['train'].get('n', 0)} | "
                     f"{f(a['train'].get('ret_pct'), 1)} | {f(a['unseen'].get('ret_pct'), 1)} |")
    return "\n".join(L)


def main():
    t0 = time.time()
    os.makedirs(OUT, exist_ok=True)
    res = {}
    coins, cv = rs.load_coins(rs.MAJORS, "majors")
    res["majors"] = evaluate_set([c for c in coins if cv[c.base]["full_history"]], "majors")
    top = [c.split("/")[0] for c in bt.build_coins(50)["coins"]]
    coins, _ = rs.load_coins(top, "top50")
    res["top50"] = evaluate_set(coins, "top50")
    json.dump(res, open(os.path.join(OUT, "factors_results.json"), "w"), default=str)
    open(os.path.join(OUT, "factors_report.md"), "w").write(report(res, time.time() - t0))
    log(f"done → {OUT}/factors_report.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
