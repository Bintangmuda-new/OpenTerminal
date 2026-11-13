"""backtest12_loose_ml.py — loose ML, walk-forward by day.

backtest6_ml.py tested a 16-factor confluence score and found it equivalent to
plain EMA+RSI. That is not the same claim as "a small model has no edge": a
16-factor score saturates, a 4-factor logistic regression does not.

Model: logistic regression, fitted on every day except the scored one (true
walk-forward, no fold leakage), 4 normalised features:
    z (close vs SMA20 in average-step units)
    rsi (centred)
    close position inside the candle range
    one-bar return in ATR units
Direction = sign of the fitted probability minus a confidence margin. Every
margin is reported, so the reader can see whether raising confidence raises
accuracy or only shrinks the sample.
"""
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

DB = Path(__file__).resolve().parent.parent / "data" / "otc_research.sqlite3"
DAY = 86400
MAX_SYMBOLS = 20  # the walk-forward refits per day; keep the panel the same for train/test


def ema(v, p):
    k = 2 / (p + 1)
    out = np.empty_like(v, dtype=float)
    acc = v[0]
    for i, x in enumerate(v):
        acc = x * k + acc * (1 - k)
        out[i] = acc
    return out


def sma(v, p):
    return np.convolve(v, np.ones(p) / p, mode="full")[: len(v)]


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
    out = np.where(al > 0, 100 - 100 / (1 + np.where(al > 0, ag / np.maximum(al, 1e-12), 0)), 100.0)
    out[: p + 1] = 50.0
    return out


def wilson_low(w, n, z=1.96):
    if n == 0:
        return 0.0
    p = w / n
    d = 1 + z * z / n
    return ((p + z * z / (2 * n)) - z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)) / d * 100


def build_symbol(symbol, rows):
    a = np.array(rows, dtype=float)
    t, o, h, l, c = (a[:, i] for i in range(5))
    n = len(c)
    rng = h - l
    atr = np.maximum(sma(rng, 14), 1e-12)
    dev = np.maximum(sma(np.abs(np.diff(c, prepend=c[0])), 20), 1e-12)
    sma20 = sma(c, 20)
    z = (c - sma20) / dev
    rsi = rsi_series(c, 14)
    cls_pos = np.where(rng > 0, (c - l) / rng, 0.5)
    ret = np.concatenate([[0.0], np.diff(c)]) / atr
    X = np.column_stack([np.clip(z, -5, 5), (rsi - 50) / 50, cls_pos - 0.5, np.clip(ret, -3, 3)])
    y = np.concatenate([np.sign(c - o)[1:], [0.0]])  # next candle direction
    day = (t // DAY).astype(int)
    ok = y != 0
    ok[-1] = False
    return {"symbol": symbol, "day": day[ok], "X": X[ok], "y": (y[ok] > 0).astype(float)}


def fit(X, y, iters=250, lr=0.5, l2=1e-3):
    w = np.zeros(X.shape[1] + 1)
    Xa = np.column_stack([np.ones(len(X)), X])
    for _ in range(iters):
        p = 1 / (1 + np.exp(-Xa @ w))
        g = Xa.T @ (p - y) / len(X) + l2 * np.r_[0, w[1:]]
        w -= lr * g
    return w


def predict(w, X):
    Xa = np.column_stack([np.ones(len(X)), X])
    return 1 / (1 + np.exp(-Xa @ w))


def main():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    data = []
    for (symbol,) in con.execute("select distinct symbol from candles_m1 order by symbol limit ?", (MAX_SYMBOLS,)):
        rows = con.execute(
            "select time,open,high,low,close from candles_m1 where symbol=? order by time", (symbol,)
        ).fetchall()
        if len(rows) < 600:
            continue
        data.append(build_symbol(symbol, rows))
    con.close()
    X = np.vstack([d["X"] for d in data])
    y = np.concatenate([d["y"] for d in data])
    day = np.concatenate([d["day"] for d in data])
    print(f"symbols={len(data)} samples={len(X)} days={len(set(day))}")

    days = sorted(set(day))
    margins = [0.0, 0.02, 0.05, 0.08, 0.12, 0.2]
    tally = {m: [0, 0] for m in margins}
    per_day = defaultdict(lambda: {m: [0, 0] for m in margins})

    for i, d in enumerate(days):
        tr = day != d
        te = day == d
        if tr.sum() < 2000 or te.sum() < 100:
            continue
        w = fit(X[tr], y[tr])
        p = predict(w, X[te])
        yy = y[te]
        for m in margins:
            conf = np.abs(p - 0.5)
            sel = conf >= m
            if sel.sum() == 0:
                continue
            pred = (p[sel] > 0.5).astype(float)
            hit = (pred == yy[sel]).sum()
            tally[m][0] += int(hit)
            tally[m][1] += int(sel.sum())
            per_day[d][m][0] += int(hit)
            per_day[d][m][1] += int(sel.sum())

    print(f"\nwalk-forward by day (each day predicted by a model fitted on the other days)")
    print(f"{'margin':>7} {'winrate':>8} {'n':>7} {'lb95':>6} {'days>50':>8}")
    for m in margins:
        w_, n_ = tally[m]
        if n_ == 0:
            continue
        good = sum(1 for d in per_day if per_day[d][m][1] >= 30 and per_day[d][m][0] / per_day[d][m][1] > 0.5)
        tot = sum(1 for d in per_day if per_day[d][m][1] >= 30)
        print(f"{m:7.2f} {w_ / n_ * 100:7.2f}% {n_:7d} {wilson_low(w_, n_):6.2f} {good:5d}/{tot}")

    # what did the model actually learn? refit on everything for the weights
    w = fit(X, y)
    names = ["z", "rsi-50", "close_pos-0.5", "ret_atr"]
    print("\nfinal weights (all data, for inspection only):")
    for nm, ww in zip(names, w[1:]):
        print(f"  {nm:14} {ww:+.4f}")
    print(f"  {'intercept':14} {w[0]:+.4f}")


if __name__ == "__main__":
    sys.exit(main())
