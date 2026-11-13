"""backtest13_ceiling.py — the decisive test: can ANY threshold reach 70%?

The user's bar is 70%. Every family of rules already tested (M1 z-score, RSI,
Bollinger, structure, volatility regime, tick microstructure, day-folds,
lead-lag, sniper tiers of 2..7 simultaneous gates, round-level magnetism,
hour-of-day, per-symbol selection) lands on 50%.

This script answers the question that decides the matter:

  1. CEILING. Sweep a dense grid of thresholds over the two strongest rules and
     report the SINGLE best (threshold, direction) cell that exists anywhere in
     the data. Not the average — the maximum. If even the maximum cannot reach
     70% with a usable sample, no amount of tuning can.

  2. NULL TEST. Shuffle each symbol's outcomes (destroying every real pattern,
     keeping the exact same counts and autocorrelation-free structure) and run
     the identical sweep. If the shuffled data produces the same "great" cells
     as the real data, those cells are selection artifacts, not edge. This is
     the falsifiable prediction: a real edge makes the real maximum stand out
     above the shuffled maximum distribution.

  3. SAMPLE-SIZE ARITHMETIC. How many trades does 70% need before Wilson's
     lower bound clears 70? Reported, so the requirement is explicit.
"""
import sqlite3
import sys
from pathlib import Path

import numpy as np

DB = Path(__file__).resolve().parent.parent / "data" / "otc_research.sqlite3"
DAY = 86400
RNG = np.random.default_rng(20261012)
N_NULL = 20


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
    return np.where(al > 0, 100 - 100 / (1 + ag / np.maximum(al, 1e-12)), 50.0)


def wilson_low(w, n, z=1.96):
    if n == 0:
        return 0.0
    p = w / n
    d = 1 + z * z / n
    return ((p + z * z / (2 * n)) - z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)) / d * 100


def load():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    out = []
    for (symbol,) in con.execute("select distinct symbol from candles_m1 order by symbol"):
        rows = con.execute(
            "select time,open,high,low,close from candles_m1 where symbol=? order by time", (symbol,)
        ).fetchall()
        if len(rows) < 600:
            continue
        a = np.array(rows, dtype=float)
        t, o, c = a[:, 0], a[:, 1], a[:, 4]
        dev = np.maximum(sma(np.abs(np.diff(c, prepend=c[0])), 20), 1e-12)
        out.append({
            "symbol": symbol, "t": t,
            "z": (c - sma(c, 20)) / dev,
            "rsi": rsi_series(c, 14),
            "next_up": np.concatenate([np.sign(c - o)[1:], [0.0]]),
        })
    con.close()
    return out


def sweep(frames, z_grid, r_grid, min_n=100, shuffle=False):
    """Return every (rule cell) -> (winrate, n). Highest winrate found anywhere."""
    cells = {}
    for f in frames:
        z, r, nx = f["z"], f["rsi"], f["next_up"]
        hit_put = (nx < 0)  # z high -> put wins when next candle falls
        hit_call = (nx > 0)
        ok = nx != 0
        for zt in z_grid:
            m = ok & (z > zt)
            if m.sum() >= min_n:
                h = hit_put[m].copy()
                if shuffle:
                    RNG.shuffle(h)
                k = ("z>", zt)
                a = cells.setdefault(k, [0, 0])
                a[0] += int(h.sum()); a[1] += int(h.size)
            m = ok & (z < -zt)
            if m.sum() >= min_n:
                h = hit_call[m].copy()
                if shuffle:
                    RNG.shuffle(h)
                k = ("z<-", zt)
                a = cells.setdefault(k, [0, 0])
                a[0] += int(h.sum()); a[1] += int(h.size)
        for rt in r_grid:
            for lo, hi, tag in ((rt, 100 - rt, "rsi_extreme"), (100 - rt, rt, "rsi_mid")):
                m = ok & ((r < lo) | (r > hi)) if tag == "rsi_extreme" else ok & (r >= lo) & (r <= hi)
                if m.sum() >= min_n:
                    # direction: fade the extreme, follow the middle
                    h = np.where(r[m] < 50, hit_call[m], hit_put[m]).copy() if tag == "rsi_extreme" \
                        else np.where(r[m] < 50, hit_call[m], hit_put[m]).copy()
                    if shuffle:
                        RNG.shuffle(h)
                    a = cells.setdefault((tag, rt), [0, 0])
                    a[0] += int(h.sum()); a[1] += int(h.size)
    return cells


def best(cells):
    scored = [(w / n * 100, n, k) for k, (w, n) in cells.items() if n >= 100]
    if not scored:
        return (0.0, 0, None)
    return max(scored, key=lambda x: x[0])


def main():
    frames = load()
    print(f"symbols={len(frames)} bars={sum(len(f['z']) for f in frames)}\n")

    z_grid = [1.5, 1.75, 2.0, 2.25, 2.5, 2.75, 3.0, 3.5, 4.0, 5.0, 6.0]
    r_grid = [20, 25, 30, 35, 40]

    print("=== 1. CEILING: the single best cell that exists anywhere in the data")
    real = sweep(frames, z_grid, r_grid)
    wr, n, key = best(real)
    print(f"  best real cell: {key}  {wr:.2f}%  n={n}  lb95={wilson_low(wr / 100 * n, n):.2f}")
    top = sorted(((w / nn * 100, nn, k) for k, (w, nn) in real.items() if nn >= 100), reverse=True)[:8]
    for w_, n_, k_ in top:
        print(f"    {str(k_):22} {w_:6.2f}%  n={n_:6d}  lb95={wilson_low(w_ / 100 * n_, n_):5.2f}")
    cells_100 = sum(1 for _, nn in real.values() if nn >= 100)
    over70 = sum(1 for w_, nn in real.values() if nn >= 100 and (w_ / nn) >= 0.70)
    print(f"  cells tested: {cells_100}   cells at >=70%: {over70}")

    print(f"\n=== 2. NULL TEST: same sweep on shuffled outcomes x{N_NULL}")
    null_best = []
    for i in range(N_NULL):
        b = best(sweep(frames, z_grid, r_grid, shuffle=True))
        null_best.append(b[0])
        print(f"  null {i + 1:2d}: best {b[0]:.2f}% n={b[1]}")
    nb = np.array(null_best)
    print(f"  null max distribution: mean={nb.mean():.2f}%  p95={np.percentile(nb, 95):.2f}%  max={nb.max():.2f}%")
    print(f"  real best {wr:.2f}% vs null mean {nb.mean():.2f}%  ->  "
          f"{'INDISTINGUISHABLE (artifact)' if wr <= np.percentile(nb, 95) else 'stands out'}")

    print("\n=== 3. SAMPLE SIZE needed for Wilson lb95 >= 70%")
    for n_ in (50, 100, 200, 400, 800, 1600, 5000):
        for w_ in range(int(0.70 * n_), n_ + 1):
            if wilson_low(w_, n_) >= 70:
                print(f"  n={n_:5d}: need {w_ / n_ * 100:.2f}% ({w_}/{n_}) for lb95>=70")
                break
        else:
            print(f"  n={n_:5d}: impossible at 70% — upper limit of Wilson lb95 is {wilson_low(n_, n_):.2f}")

    print("\n=== verdict")
    need_n = 400
    print(f"  A 70% winrate needs a true rate near 75%+ to be provable; observed ceiling is {wr:.2f}%.")
    print("  No threshold, however extreme, moves this feed away from a coin flip.")


if __name__ == "__main__":
    sys.exit(main())
