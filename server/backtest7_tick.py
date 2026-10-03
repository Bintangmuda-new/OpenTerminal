"""Backtest v7: tick microstructure at the real decision point.

Everything tested so far (v1-v6, 23 rules) used M1 aggregates and found no edge.
This is the one data source never touched: 42M raw ticks. It asks a different
question, the one the terminal actually faces:

  At T-8s before the candle closes, what do the ticks INSIDE the running candle
  say about the NEXT candle's direction?

M1 bars throw that away. A candle that closes +5 having spent 50 seconds falling
is a different animal from one that rose all minute, and both look identical as
OHLC.

Discipline unchanged from v6 -- a result only counts if it beats the shuffled
control, not just 50%:
  * walk-forward blocks, never a single split
  * per-symbol standardisation from TRAINING ticks only
  * shuffled-label control through the identical pipeline
  * Wilson 95% lower bound reported alongside the raw rate

ponytail: loads one symbol's ticks at a time; fine at this size, chunk by day if
the tick table ever outgrows memory.
"""
import math
import random
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "otc_research.sqlite3"

DECIDE_AT = 8          # seconds before close -- matches the terminal's fire point
TRAIN = 800
TEST = 250
L2 = 1.0
EPOCHS = 50
LR = 0.3

FEATURES = [
    "drift",        # where price sits in the candle's own tick range
    "late_push",    # last 8s move vs the whole candle's move
    "tick_skew",    # share of up-ticks minus down-ticks
    "accel",        # second-half velocity vs first-half
    "reversal",     # did the candle turn around mid-way
    "tick_rate",    # ticks this minute vs the 20-minute norm
    "range_ratio",  # this candle's tick range vs the 20-minute norm
    "prev_body",    # previous closed candle's body, normalised
    "prev2_body",   # the one before it
    "gap",          # open vs previous close
]


def wilson_low(w, n, z=1.96):
    if n == 0:
        return 0.0
    p = w / n
    d = 1 + z * z / n
    return ((p + z * z / (2 * n)) - z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** .5)) / d * 100


def minute_buckets(ticks):
    """Groups (time, price) into {minute_start: [(offset_seconds, price)]}."""
    out = defaultdict(list)
    for t, p in ticks:
        m = int(t // 60) * 60
        out[m].append((t - m, p))
    return out


def build(buckets):
    """One row per minute that has enough ticks before the decision point."""
    minutes = sorted(buckets)
    rows, targets = [], []
    rate_hist, range_hist = [], []

    for i in range(3, len(minutes) - 1):
        m = minutes[i]
        if minutes[i + 1] - m != 60 or m - minutes[i - 1] != 60:
            rate_hist.clear()
            range_hist.clear()
            continue

        seen = buckets[m]
        # Only ticks that existed at decision time. Using the full candle here
        # would be lookahead -- the single easiest way to fake an edge.
        visible = [(o, p) for o, p in seen if o <= 60 - DECIDE_AT]
        rate_hist.append(len(seen))
        if len(visible) < 6:
            range_hist.clear()
            continue

        prices = [p for _, p in visible]
        hi, lo = max(prices), min(prices)
        rng = hi - lo
        range_hist.append(rng)
        if len(rate_hist) < 21 or len(range_hist) < 21:
            continue
        if rng <= 0:
            continue

        open_p, now_p = prices[0], prices[-1]
        mid = len(prices) // 2
        late = [p for o, p in visible if o >= 60 - DECIDE_AT - 8] or prices[-2:]

        ups = sum(1 for a, b in zip(prices, prices[1:]) if b > a)
        downs = sum(1 for a, b in zip(prices, prices[1:]) if b < a)
        moves = max(ups + downs, 1)

        first_half = prices[mid] - prices[0]
        second_half = prices[-1] - prices[mid]
        body = now_p - open_p

        prev = [p for _, p in buckets[minutes[i - 1]]]
        prev2 = [p for _, p in buckets[minutes[i - 2]]]
        if len(prev) < 2 or len(prev2) < 2:
            continue

        norm_rate = sum(rate_hist[-21:-1]) / 20 or 1
        norm_range = sum(range_hist[-21:-1]) / 20 or 1e-12

        feats = [
            (now_p - lo) / rng * 2 - 1,
            (late[-1] - late[0]) / rng,
            (ups - downs) / moves,
            (second_half - first_half) / rng,
            -1.0 if (first_half > 0) != (second_half > 0) and second_half != 0 else 0.0,
            len(seen) / norm_rate - 1,
            rng / norm_range - 1,
            (prev[-1] - prev[0]) / norm_range,
            (prev2[-1] - prev2[0]) / norm_range,
            (open_p - prev[-1]) / norm_range,
        ]
        if any(math.isnan(x) or math.isinf(x) or abs(x) > 40 for x in feats):
            continue

        nxt = [p for _, p in buckets[minutes[i + 1]]]
        if len(nxt) < 2 or nxt[-1] == nxt[0]:
            continue
        rows.append(feats)
        targets.append(1 if nxt[-1] > nxt[0] else 0)
    return rows, targets


def train_logistic(X, y):
    d = len(X[0])
    n = len(X)
    mu = [sum(r[j] for r in X) / n for j in range(d)]
    sd = []
    for j in range(d):
        var = sum((r[j] - mu[j]) ** 2 for r in X) / n
        sd.append(math.sqrt(var) or 1.0)
    Xs = [[(r[j] - mu[j]) / sd[j] for j in range(d)] for r in X]
    w, b = [0.0] * d, 0.0
    for _ in range(EPOCHS):
        gw, gb = [0.0] * d, 0.0
        for row, label in zip(Xs, y):
            z = b + sum(w[j] * row[j] for j in range(d))
            p = 1 / (1 + math.exp(-max(-30, min(30, z))))
            e = p - label
            for j in range(d):
                gw[j] += e * row[j]
            gb += e
        for j in range(d):
            w[j] -= LR * (gw[j] / n + L2 * w[j] / n)
        b -= LR * gb / n
    return w, b, mu, sd


def predict(model, row):
    w, b, mu, sd = model
    z = b + sum(w[j] * ((row[j] - mu[j]) / sd[j]) for j in range(len(w)))
    return 1 / (1 + math.exp(-max(-30, min(30, z))))


GATES = (0.0, 0.02, 0.05, 0.08)


def walk_forward(X, y, shuffle=False):
    if shuffle:
        y = y[:]
        random.shuffle(y)
    stats = {g: [0, 0] for g in GATES}
    weights = [0.0] * len(FEATURES)
    start = models = 0
    while start + TRAIN + TEST <= len(X):
        Xtr, ytr = X[start:start + TRAIN], y[start:start + TRAIN]
        if len(set(ytr)) < 2:
            start += TEST
            continue
        model = train_logistic(Xtr, ytr)
        if not shuffle:
            for j, wj in enumerate(model[0]):
                weights[j] += wj
            models += 1
        for row, label in zip(X[start + TRAIN:start + TRAIN + TEST],
                              y[start + TRAIN:start + TRAIN + TEST]):
            p = predict(model, row)
            for g in GATES:
                if abs(p - .5) < g:
                    continue
                stats[g][1] += 1
                stats[g][0] += int((p > .5) == (label == 1))
        start += TEST
    return stats, weights, models


def run(limit=12):
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    symbols = [s for (s,) in con.execute(
        "SELECT symbol FROM ticks GROUP BY symbol ORDER BY COUNT(*) DESC LIMIT ?", (limit,))]

    real = defaultdict(lambda: [0, 0])
    ctrl = defaultdict(lambda: [0, 0])
    wsum = [0.0] * len(FEATURES)
    wn = 0
    random.seed(7)

    for sym in symbols:
        ticks = con.execute(
            "SELECT time, price FROM ticks WHERE symbol=? ORDER BY time", (sym,)).fetchall()
        X, y = build(minute_buckets(ticks))
        if len(X) < TRAIN + TEST:
            print(f"  {sym:<16} skipped ({len(X)} rows)")
            continue
        stats, weights, models = walk_forward(X, y)
        for g, (c, n) in stats.items():
            real[g][0] += c
            real[g][1] += n
        cstats, _, _ = walk_forward(X, y, shuffle=True)
        for g, (c, n) in cstats.items():
            ctrl[g][0] += c
            ctrl[g][1] += n
        if models:
            for j in range(len(wsum)):
                wsum[j] += weights[j] / models
            wn += 1
        c, n = stats[0.0]
        print(f"  {sym:<16} rows {len(X):>6,}  raw {c / n * 100:.2f}%" if n else f"  {sym:<16} no test")

    print(f"\ndecision point T-{DECIDE_AT}s | {wn} symbols | train {TRAIN} / test {TEST} minutes\n")
    print(f"{'confidence gate':<18}{'winrate':>9}{'95% low':>9}{'trades':>9}{'control':>9}   verdict")
    print("-" * 66)
    for g in sorted(real):
        c, n = real[g]
        cc, cn = ctrl[g]
        if n == 0:
            continue
        rate, low = c / n * 100, wilson_low(c, n)
        control = cc / cn * 100 if cn else 0
        verdict = "EDGE" if low > 50 and rate > control + 1 else "no edge"
        label = "none" if g == 0 else f"|p-0.5| >= {g}"
        print(f"{label:<18}{rate:>8.2f}%{low:>8.1f}%{n:>9,}{control:>8.2f}%   {verdict}")

    if wn:
        print("\nmean model weight per feature (sign = direction it pushes):")
        for name, w in sorted(zip(FEATURES, [x / wn for x in wsum]), key=lambda kv: -abs(kv[1])):
            print(f"  {name:<12}{w:+.4f}")
    print("\ncontrol = identical pipeline on shuffled labels. The real column must")
    print("beat it, not merely beat 50%.")
    con.close()


if __name__ == "__main__":
    run(limit=int(sys.argv[1]) if len(sys.argv) > 1 else 12)
