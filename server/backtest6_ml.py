"""Backtest v6: algorithmic / machine-learning approach, pure stdlib.

Rather than hand-crafting another indicator rule, this lets a logistic
regression search the feature space itself, then tests it the way a trading
model must be tested:

  * WALK-FORWARD, not a single split. Train on a window, predict the next
    block, roll forward. A single 60/40 split can get lucky once; twelve
    consecutive out-of-sample blocks cannot.
  * CONFIDENCE GATING. The model only trades when |p - 0.5| exceeds a
    threshold. A model with no edge degrades gracefully to "never trade"
    instead of pretending.
  * A SHUFFLED-LABEL CONTROL. The same pipeline is run against randomised
    outcomes. Whatever winrate that produces is the machinery's own bias, and
    the real result must beat it to mean anything.

Features are all scale-free (ratios, z-scores, normalised positions) so one
model generalises across symbols with different price levels.
"""
import math
import random
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "otc_research.sqlite3"

TRAIN_BARS = 1200      # rolling training window
TEST_BARS = 300        # out-of-sample block predicted after each window
L2 = 1.0               # ridge penalty; the feature count is small but noisy
EPOCHS = 60
LR = 0.3


# ---------------------------------------------------------------- features

def ema(v, p):
    out, k, acc = [], 2 / (p + 1), v[0]
    for x in v:
        acc = x * k + acc * (1 - k)
        out.append(acc)
    return out


def rsi_series(v, p=14):
    out, g, l = [50.0] * len(v), 0.0, 0.0
    for i in range(1, len(v)):
        d = v[i] - v[i - 1]
        up, dn = max(d, 0.0), max(-d, 0.0)
        if i <= p:
            g += up / p
            l += dn / p
        else:
            g = (g * (p - 1) + up) / p
            l = (l * (p - 1) + dn) / p
        out[i] = 100.0 if l == 0 else 100 - 100 / (1 + g / l)
    return out


def sma(v, p):
    out, run = [v[0]] * len(v), 0.0
    for i, x in enumerate(v):
        run += x
        if i >= p:
            run -= v[i - p]
        out[i] = run / min(i + 1, p)
    return out


FEATURE_NAMES = [
    "z20", "z50", "rsi", "rsi_slope", "ema_spread", "body_ratio", "upper_wick",
    "lower_wick", "range_atr", "streak", "ret1", "ret3", "ret5", "pos_in_range",
    "accel", "vol_ratio",
]


def featurise(seg):
    """Returns (rows, targets) where each row is scale-free."""
    o = [r[1] for r in seg]
    h = [r[2] for r in seg]
    lo = [r[3] for r in seg]
    c = [r[4] for r in seg]
    n = len(seg)
    e9, e21, e50 = ema(c, 9), ema(c, 21), ema(c, 50)
    rsi = rsi_series(c)
    s20, s50 = sma(c, 20), sma(c, 50)
    rng = [h[i] - lo[i] for i in range(n)]
    atr = sma(rng, 14)
    step = [abs(c[i] - c[i - 1]) if i else 0.0 for i in range(n)]
    dev20 = sma(step, 20)
    vol5 = sma(step, 5)

    rows, targets, index = [], [], []
    for i in range(60, n - 1):
        unit = dev20[i] or 1e-12
        a = atr[i] or 1e-12
        r = rng[i] or 1e-12
        price = c[i] or 1e-12
        streak = 0
        for j in range(i, max(i - 8, 0), -1):
            if (c[j] > o[j]) == (c[i] > o[i]):
                streak += 1
            else:
                break
        streak = streak if c[i] > o[i] else -streak
        feats = [
            (c[i] - s20[i]) / unit,
            (c[i] - s50[i]) / unit,
            (rsi[i] - 50) / 50,
            (rsi[i] - rsi[i - 3]) / 50,
            (e9[i] - e21[i]) / unit,
            (c[i] - o[i]) / r,
            (h[i] - max(c[i], o[i])) / r,
            (min(c[i], o[i]) - lo[i]) / r,
            rng[i] / a,
            streak / 8,
            (c[i] - c[i - 1]) / unit,
            (c[i] - c[i - 3]) / unit,
            (c[i] - c[i - 5]) / unit,
            (c[i] - lo[i]) / r,
            ((c[i] - c[i - 1]) - (c[i - 1] - c[i - 2])) / unit,
            vol5[i] / (dev20[i] or 1e-12),
        ]
        if any(math.isnan(x) or math.isinf(x) or abs(x) > 50 for x in feats):
            continue
        nxt = seg[i + 1]
        if nxt[4] == nxt[1]:
            continue
        rows.append(feats)
        targets.append(1 if nxt[4] > nxt[1] else 0)
        index.append(i)
    return rows, targets, index


# ---------------------------------------------------------------- model

def train_logistic(X, y, epochs=EPOCHS, lr=LR, l2=L2):
    d = len(X[0])
    w = [0.0] * d
    b = 0.0
    n = len(X)
    if n == 0:
        return w, b
    # Standardise using training statistics only — leaking test stats here
    # would silently inflate every result that follows.
    mu = [sum(row[j] for row in X) / n for j in range(d)]
    sd = []
    for j in range(d):
        var = sum((row[j] - mu[j]) ** 2 for row in X) / n
        sd.append(math.sqrt(var) or 1.0)
    Xs = [[(row[j] - mu[j]) / sd[j] for j in range(d)] for row in X]

    for _ in range(epochs):
        gw = [0.0] * d
        gb = 0.0
        for row, label in zip(Xs, y):
            z = b + sum(w[j] * row[j] for j in range(d))
            p = 1 / (1 + math.exp(-max(-30, min(30, z))))
            err = p - label
            for j in range(d):
                gw[j] += err * row[j]
            gb += err
        for j in range(d):
            w[j] -= lr * (gw[j] / n + l2 * w[j] / n)
        b -= lr * gb / n
    return (w, b, mu, sd)


def predict(model, row):
    w, b, mu, sd = model
    z = b + sum(w[j] * ((row[j] - mu[j]) / sd[j]) for j in range(len(w)))
    return 1 / (1 + math.exp(-max(-30, min(30, z))))


def wilson_low(w, n, z=1.96):
    if n == 0:
        return 0.0
    p = w / n
    d = 1 + z * z / n
    return ((p + z * z / (2 * n)) - z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)) / d * 100


# ---------------------------------------------------------------- walk-forward

def segments_of(rows, minlen=TRAIN_BARS + TEST_BARS + 120):
    out, seg = [], [rows[0]]
    for prev, cur in zip(rows, rows[1:]):
        if cur[0] - prev[0] == 60:
            seg.append(cur)
        else:
            if len(seg) >= minlen:
                out.append(seg)
            seg = [cur]
    if len(seg) >= minlen:
        out.append(seg)
    return out


def walk_forward(X, y, shuffle_labels=False, gates=(0.0, 0.02, 0.05, 0.08)):
    """Yields (gate, correct, traded) across every out-of-sample block."""
    if shuffle_labels:
        y = y[:]
        random.shuffle(y)
    stats = {g: [0, 0] for g in gates}
    start = 0
    blocks = 0
    while start + TRAIN_BARS + TEST_BARS <= len(X):
        Xtr = X[start:start + TRAIN_BARS]
        ytr = y[start:start + TRAIN_BARS]
        Xte = X[start + TRAIN_BARS:start + TRAIN_BARS + TEST_BARS]
        yte = y[start + TRAIN_BARS:start + TRAIN_BARS + TEST_BARS]
        if len(set(ytr)) < 2:
            start += TEST_BARS
            continue
        model = train_logistic(Xtr, ytr)
        for row, label in zip(Xte, yte):
            p = predict(model, row)
            for g in gates:
                if abs(p - 0.5) < g:
                    continue
                stats[g][1] += 1
                stats[g][0] += int((p > 0.5) == (label == 1))
        blocks += 1
        start += TEST_BARS
    return stats, blocks


def run(limit=None):
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    symbols = [s for (s,) in con.execute(
        "SELECT symbol FROM candles_m1 GROUP BY symbol HAVING COUNT(*) >= ? ORDER BY COUNT(*) DESC",
        (TRAIN_BARS + TEST_BARS + 200,))]
    if limit:
        symbols = symbols[:limit]

    real = defaultdict(lambda: [0, 0])
    ctrl = defaultdict(lambda: [0, 0])
    total_blocks = 0
    random.seed(11)

    for sym in symbols:
        rows = con.execute(
            "SELECT time, open, high, low, close FROM candles_m1 WHERE symbol=? ORDER BY time", (sym,)).fetchall()
        for seg in segments_of(rows):
            X, y, _ = featurise(seg)
            if len(X) < TRAIN_BARS + TEST_BARS:
                continue
            stats, blocks = walk_forward(X, y)
            total_blocks += blocks
            for g, (c, n) in stats.items():
                real[g][0] += c
                real[g][1] += n
            cstats, _ = walk_forward(X, y, shuffle_labels=True)
            for g, (c, n) in cstats.items():
                ctrl[g][0] += c
                ctrl[g][1] += n

    print(f"symbols {len(symbols)} | walk-forward blocks {total_blocks} "
          f"| train {TRAIN_BARS} / test {TEST_BARS} bars\n")
    print(f"{'confidence gate':<18}{'winrate':>9}{'95% low':>9}{'trades':>9}"
          f"{'control':>9}   verdict")
    print("-" * 66)
    for g in sorted(real):
        c, n = real[g]
        cc, cn = ctrl[g]
        if n == 0:
            continue
        rate = c / n * 100
        low = wilson_low(c, n)
        control = cc / cn * 100 if cn else 0
        verdict = "EDGE" if low > 50 and rate > control + 1 else "no edge"
        label = "none" if g == 0 else f"|p-0.5| >= {g}"
        print(f"{label:<18}{rate:>8.2f}%{low:>8.1f}%{n:>9,}{control:>8.2f}%   {verdict}")

    print("\ncontrol = same pipeline on shuffled labels; the real column must")
    print("beat it, not just beat 50%.")
    con.close()


if __name__ == "__main__":
    run(limit=int(sys.argv[1]) if len(sys.argv) > 1 else None)
