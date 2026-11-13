"""backtest9_walkforward.py — is the RSI-extreme edge real, or one lucky window?

backtest8 found rsi20/80 at 53.92% on the 60/40 holdout (n=994, Wilson lower
bound 50.82) but only 49.82% on train — a 4-point swing that smells like a
period artifact rather than an edge.

This script refuses to accept that. It:
  1. splits by DAY, so every day is scored out-of-sample of the other days,
  2. reports per-symbol concentration (one trending symbol can fake a rule),
  3. sweeps RSI thresholds densely around the promising zone,
  4. adds confirmation filters on top and checks each one survives day folds.
"""
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

DB = Path(__file__).resolve().parent.parent / "data" / "otc_research.sqlite3"
DAY = 86400


def rsi_series(v, p=14):
    d = np.diff(v, prepend=v[0])
    up = np.clip(d, 0, None)
    dn = np.clip(-d, 0, None)
    ag = np.zeros_like(v)
    al = np.zeros_like(v)
    ag[p] = up[1 : p + 1].mean()
    al[p] = dn[1 : p + 1].mean()
    for i in range(p + 1, len(v)):
        ag[i] = (ag[i - 1] * (p - 1) + up[i]) / p
        al[i] = (al[i - 1] * (p - 1) + dn[i]) / p
    out = np.full_like(v, 50.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(al > 0, 100 - 100 / (1 + np.where(al > 0, ag / np.maximum(al, 1e-12), 0)), 100.0)
    out[: p + 1] = 50.0
    return out


def sma(v, p):
    return np.convolve(v, np.ones(p) / p, mode="full")[: len(v)]


def wilson_low(w, n, z=1.96):
    if n == 0:
        return 0.0
    p = w / n
    d = 1 + z * z / n
    return ((p + z * z / (2 * n)) - z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)) / d * 100


def load():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    frames = []
    for (symbol,) in con.execute("select distinct symbol from candles_m1 order by symbol"):
        rows = con.execute(
            "select time,open,high,low,close from candles_m1 where symbol=? order by time", (symbol,)
        ).fetchall()
        if len(rows) < 400:
            continue
        a = np.array(rows, dtype=float)
        t, o, h, l, c = (a[:, i] for i in range(5))
        n = len(c)
        rng = h - l
        body = np.abs(c - o)
        dev = sma(np.abs(np.diff(c, prepend=c[0])), 20)
        f = {
            "symbol": symbol, "t": t, "o": o, "h": h, "l": l, "c": c, "n": n,
            "rsi": rsi_series(c, 14),
            "up": np.sign(c - o),
            "next_up": np.concatenate([np.sign(c - o)[1:], [0.0]]),
            "rng": rng, "body": body,
            "atr": sma(rng, 14), "dev": dev,
            "sma20": sma(c, 20),
        }
        f["z"] = np.where(dev > 0, (c - f["sma20"]) / dev, 0.0)
        ratio = np.where(c > 0, rng / c, 0.0)
        med = np.array([np.median(ratio[max(0, i - 59) : i + 1]) for i in range(n)])
        f["hi_vol"] = ratio > med
        f["day"] = (t // DAY).astype(int)
        f["body_atr"] = np.where(f["atr"] > 0, body / f["atr"], 0.0)
        frames.append(f)
    con.close()
    return frames


def collect(frames, rule):
    """rule(f) -> direction array. Returns list of (symbol, day, hit, direction)."""
    out = []
    for f in frames:
        d = rule(f)
        ok = (d != 0)
        ok[-1] = False
        ok &= f["next_up"] != 0
        idx = np.flatnonzero(ok)
        hit = (d[idx] == f["next_up"][idx]).astype(int)
        out.append((f["symbol"], f["day"][idx], hit))
    return out


def report(name, per_symbol, min_n=100):
    hits = np.concatenate([h for _, _, h in per_symbol])
    n = len(hits)
    if n == 0:
        print(f"{name:34} no signals")
        return None
    w = hits.sum()
    # day folds: every day judged against every other day
    by_day = defaultdict(lambda: [0, 0])
    for _, days, h in per_symbol:
        for d, x in zip(days, h):
            by_day[d][0] += x
            by_day[d][1] += 1
    folds = [(d, v[0] / v[1] * 100, v[1]) for d, v in sorted(by_day.items()) if v[1] >= 30]
    pos = sum(1 for _, r, _ in folds if r > 50)
    # symbol spread: is all of it one symbol?
    by_sym = defaultdict(lambda: [0, 0])
    for s, _, h in per_symbol:
        by_sym[s][0] += h.sum()
        by_sym[s][1] += len(h)
    sym_rates = sorted(((v[0] / v[1] * 100, s, v[1]) for s, v in by_sym.items() if v[1] >= 20), reverse=True)
    print(f"{name:34} {w / n * 100:6.2f}% n={n:6d} lb95={wilson_low(w, n):5.2f} "
          f"days>{50}: {pos}/{len(folds)}  symbols+ : {sum(1 for r, _, _ in sym_rates if r > 50)}/{len(sym_rates)}")
    if len(folds) <= 12:
        print("      per day:", " ".join(f"{r:.1f}({n2})" for _, r, n2 in folds))
    if sym_rates[:3]:
        print("      top symbols:", ", ".join(f"{s} {r:.1f}({n2})" for r, s, n2 in sym_rates[:3]))
    return w / n * 100


def main():
    frames = load()
    print(f"symbols={len(frames)} bars={sum(f['n'] for f in frames)}")

    def rsi_rule(lo, hi):
        def fn(f):
            return np.where(f["rsi"] < lo, 1.0, np.where(f["rsi"] > hi, -1.0, 0.0))
        return fn

    def z_rule(thr):
        def fn(f):
            return np.where(f["z"] < -thr, 1.0, np.where(f["z"] > thr, -1.0, 0.0))
        return fn

    def combo(lo, hi, vol=True, reversal=True):
        def fn(f):
            base = np.where(f["rsi"] < lo, 1.0, np.where(f["rsi"] > hi, -1.0, 0.0))
            if vol:
                base = np.where(base != 0, np.where(f["hi_vol"], base, 0.0), 0.0)
            if reversal:
                # the extreme candle must itself close against the extreme side
                agree = ((base > 0) & (f["up"] > 0)) | ((base < 0) & (f["up"] < 0))
                base = np.where(base != 0, np.where(agree, base, 0.0), 0.0)
            return base
        return fn

    print("\n--- RSI grid (day folds, out-of-sample by day)")
    for lo in (10, 12, 15, 18, 20, 22, 25):
        for hi in (75, 78, 80, 82, 85, 88, 90):
            report(f"rsi{lo}/{hi}", collect(frames, rsi_rule(lo, hi)))

    print("\n--- z grid")
    for thr in (2.0, 2.5, 3.0, 3.5):
        report(f"z>{thr}", collect(frames, z_rule(thr)))

    print("\n--- rsi20/80 with confirmation layers")
    report("rsi20/80", collect(frames, combo(20, 80, vol=False, reversal=False)))
    report("rsi20/80 +volHI", collect(frames, combo(20, 80, vol=True, reversal=False)))
    report("rsi20/80 +reversal", collect(frames, combo(20, 80, vol=False, reversal=True)))
    report("rsi20/80 +both", collect(frames, combo(20, 80, vol=True, reversal=True)))
    report("rsi15/85 +both", collect(frames, combo(15, 85, True, True)))
    report("rsi25/75 +both", collect(frames, combo(25, 75, True, True)))


if __name__ == "__main__":
    sys.exit(main())
