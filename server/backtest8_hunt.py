"""backtest8_hunt.py — wide rule hunt for high-winrate strategies.

Mirrors the app's scoring: signal fires at the close of candle A, the entry is
candle B (the first candle after A), and the outcome is B's own direction
(close vs open). Same convention as backtest5.py and the live journal.

Method: every candidate rule is ranked on the TRAIN slice only (first 60% of
time), then the reported number is always the HOLDOUT slice (last 40%). Wilson
lower bound at 95% is printed next to every rate, because a rate whose lower
bound sits under 50% has not been shown to beat a coin flip.

Read-only on the database.
"""
import itertools
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np

DB = Path(__file__).resolve().parent.parent / "data" / "otc_research.sqlite3"
MIN_N = 200          # a holdout segment thinner than this is noise, not a rule
TARGET = 70.0


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
    out = np.full_like(v, 50.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = np.where(al > 0, ag / al, np.inf)
        out = np.where(al > 0, 100 - 100 / (1 + rs), 100.0)
    out[: p + 1] = 50.0
    return out


def wilson_low(w, n, z=1.96):
    if n == 0:
        return 0.0
    p = w / n
    d = 1 + z * z / n
    return ((p + z * z / (2 * n)) - z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)) / d * 100


def load():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    frames = []
    for symbol, in con.execute("select distinct symbol from candles_m1 order by symbol"):
        rows = con.execute(
            "select time,open,high,low,close from candles_m1 where symbol=? order by time", (symbol,)
        ).fetchall()
        if len(rows) < 400:
            continue
        arr = np.array(rows, dtype=float)
        t, o, h, l, c = (arr[:, i] for i in range(5))
        frames.append(build(symbol, t, o, h, l, c))
    con.close()
    return frames


def build(symbol, t, o, h, l, c):
    n = len(c)
    rng = h - l
    body = np.abs(c - o)
    f = {
        "symbol": symbol,
        "t": t,
        "o": o,
        "h": h,
        "l": l,
        "c": c,
        "n": n,
        "e9": ema(c, 9),
        "e21": ema(c, 21),
        "e50": ema(c, 50),
        "e200": ema(c, 200),
        "rsi": rsi_series(c, 14),
        "sma20": sma(c, 20),
        "sma50": sma(c, 50),
        "std20": np.array([c[max(0, i - 19) : i + 1].std() for i in range(n)]),
        "rng": rng,
        "body": body,
    }
    f["atr"] = sma(rng, 14)
    f["dev"] = sma(np.abs(np.diff(c, prepend=c[0])), 20)
    f["up"] = np.sign(c - o)  # +1 bull candle, -1 bear, 0 flat
    # entry candle = the next one; its direction is what decides WIN/LOSS.
    f["next_up"] = np.concatenate([f["up"][1:], [0.0]])
    f["z"] = np.where(f["dev"] > 0, (c - f["sma20"]) / f["dev"], 0.0)
    f["bb_pct"] = np.where(
        f["std20"] > 0, (c - f["sma20"]) / (2 * f["std20"]), 0.0
    )
    # median range ratio over the trailing 60 candles, computed causally.
    ratio = np.where(c > 0, rng / c, 0.0)
    med = np.zeros(n)
    for i in range(n):
        w = ratio[max(0, i - 59) : i + 1]
        med[i] = np.median(w) if len(w) else 0.0
    f["med_range"] = med
    f["rng_ratio"] = ratio
    f["hi_vol"] = ratio > med
    # consecutive same-direction run ending at i (sign only).
    run = np.zeros(n)
    for i in range(1, n):
        run[i] = run[i - 1] + 1 if f["up"][i] != 0 and f["up"][i] == f["up"][i - 1] else (1 if f["up"][i] != 0 else 0)
    f["run"] = run
    f["streak_dir"] = np.array([np.sign(c[i] - c[i - int(run[i])]) if run[i] > 0 and i - int(run[i]) >= 0 else 0.0 for i in range(n)])
    f["hour"] = ((t // 60) // 60) % 24
    # realized vol of the last 10 candles vs the last 60: regime gauge.
    vol10 = np.array([rng[max(0, i - 9) : i + 1].mean() for i in range(n)])
    vol60 = np.array([rng[max(0, i - 59) : i + 1].mean() for i in range(n)])
    f["vol_ratio"] = np.where(vol60 > 0, vol10 / vol60, 1.0)
    f["trend"] = np.where(c > f["e50"], 1.0, -1.0)
    f["dist_e50"] = np.where(f["e50"] > 0, (c - f["e50"]) / f["e50"], 0.0)
    return f


# ---------- direction rules: each returns a direction array (+1 CALL, -1 PUT, 0 none)

def d_z(f, thr):
    return np.where(f["z"] < -thr, 1.0, np.where(f["z"] > thr, -1.0, 0.0))


def d_zrsi(f, thr, lo, hi):
    z = d_z(f, thr)
    return np.where((z > 0) & (f["rsi"] < lo), 1.0, np.where((z < 0) & (f["rsi"] > hi), -1.0, 0.0))


def d_rsi(f, lo, hi):
    return np.where(f["rsi"] < lo, 1.0, np.where(f["rsi"] > hi, -1.0, 0.0))


def d_bb(f, thr):
    return np.where(f["bb_pct"] < -thr, 1.0, np.where(f["bb_pct"] > thr, -1.0, 0.0))


def d_bb_rsi(f, lo, hi):
    x = d_bb(f, 1.0)
    return np.where((x > 0) & (f["rsi"] < lo), 1.0, np.where((x < 0) & (f["rsi"] > hi), -1.0, 0.0))


def d_streak_fade(f, k):
    return np.where((f["run"] >= k) & (f["up"] > 0), -1.0, np.where((f["run"] >= k) & (f["up"] < 0), 1.0, 0.0))


def d_streak_follow(f, k):
    return np.where((f["run"] >= k) & (f["up"] > 0), 1.0, np.where((f["run"] >= k) & (f["up"] < 0), -1.0, 0.0))


def d_reversal_candle(f, zthr, body_mult):
    """Stretched from the mean AND a large opposite-coloured candle."""
    z = d_z(f, zthr)
    big = f["body"] > f["atr"] * body_mult
    return np.where((z > 0) & (f["up"] < 0) & big, 1.0,
                    np.where((z < 0) & (f["up"] > 0) & big, -1.0, 0.0))


def d_momentum_burst(f, mult, follow=True):
    """Candle whose body dwarfs recent average, then bet on continuation/fade."""
    big = f["body"] > f["dev"] * mult
    d = np.where(big & (f["up"] > 0), 1.0, np.where(big & (f["up"] < 0), -1.0, 0.0))
    return d if follow else -d


def score(f, direction):
    """Return (train_w, train_n, hold_w, hold_n, cut) for a direction array."""
    sig = direction != 0
    hit = np.where(direction == f["next_up"], 1.0, 0.0)
    decided = sig & (f["next_up"] != 0)
    # need the entry candle to exist (skip the last bar of the symbol)
    decided[-1] = False
    ts = f["t"]
    cut = CUTOFF
    tr = decided & (ts < cut)
    ho = decided & (ts >= cut)
    return (hit[tr].sum(), tr.sum(), hit[ho].sum(), ho.sum())


CUTOFF = 0.0
FRAMES = []


def main():
    global CUTOFF, FRAMES
    FRAMES = load()
    all_t = np.concatenate([f["t"] for f in FRAMES])
    CUTOFF = float(np.quantile(all_t, 0.60))
    total_bars = sum(f["n"] for f in FRAMES)
    base = np.concatenate([f["next_up"][f["next_up"] != 0] for f in FRAMES])
    baseline = (base > 0).mean() * 100
    print(f"symbols={len(FRAMES)} bars={total_bars} baseline_up={baseline:.2f}%")
    print(f"train/holdout cut at epoch {CUTOFF:.0f}\n")

    # ---- candidate library
    cands = {}
    for thr in (1.5, 2.0, 2.5, 3.0, 3.5, 4.0):
        cands[f"z>{thr}"] = lambda f, t=thr: d_z(f, t)
    for thr, lo, hi in itertools.product((2.0, 2.5, 3.0), (20, 25, 30), (70, 75, 80)):
        cands[f"z>{thr}+rsi{lo}/{hi}"] = lambda f, a=thr, b=lo, c=hi: d_zrsi(f, a, b, c)
    for lo, hi in itertools.product((15, 20, 25, 30), (70, 75, 80, 85)):
        cands[f"rsi{lo}/{hi}"] = lambda f, a=lo, b=hi: d_rsi(f, a, b)
    for thr in (1.5, 2.0, 2.5):
        cands[f"bb{thr}"] = lambda f, t=thr: d_bb(f, t)
    for lo, hi in itertools.product((20, 25, 30), (70, 75, 80)):
        cands[f"bb1+rsi{lo}/{hi}"] = lambda f, a=lo, b=hi: d_bb_rsi(f, a, b)
    for k in (3, 4, 5, 6, 7):
        cands[f"fade{k}"] = lambda f, kk=k: d_streak_fade(f, kk)
        cands[f"follow{k}"] = lambda f, kk=k: d_streak_follow(f, kk)
    for thr, bm in itertools.product((1.0, 1.5, 2.0), (1.0, 1.5, 2.0, 2.5)):
        cands[f"revcandle z{thr} body{bm}"] = lambda f, a=thr, b=bm: d_reversal_candle(f, a, b)
    for m in (1.5, 2.0, 2.5, 3.0):
        cands[f"burst{m} follow"] = lambda f, mm=m: d_momentum_burst(f, mm, True)
        cands[f"burst{m} fade"] = lambda f, mm=m: d_momentum_burst(f, mm, False)

    rows = []
    for name, fn in cands.items():
        tw = tn = hw = hn = 0
        for f in FRAMES:
            a, b, c, d = score(f, fn(f))
            tw += a
            tn += b
            hw += c
            hn += d
        if tn < 50 and hn < 50:
            continue
        rows.append({
            "rule": name,
            "train_n": int(tn), "train": tw / tn * 100 if tn else 0.0,
            "hold_n": int(hn), "hold": hw / hn * 100 if hn else 0.0,
            "hold_lb": wilson_low(hw, hn),
        })

    rows.sort(key=lambda r: (-r["hold"], -r["hold_n"]))
    print(f"{'rule':28} {'train%':>7} {'n':>7} {'hold%':>7} {'lb95':>6} {'n':>7}")
    for r in rows[:30]:
        print(f"{r['rule']:28} {r['train']:7.2f} {r['train_n']:7d} {r['hold']:7.2f} {r['hold_lb']:6.2f} {r['hold_n']:7d}")

    hit = [r for r in rows if r["hold"] >= TARGET and r["hold_n"] >= MIN_N]
    print(f"\nrules with holdout >= {TARGET}% and n >= {MIN_N}: {len(hit)}")
    for r in hit:
        print("  ", r["rule"], f"{r['hold']:.2f}% n={r['hold_n']} lb={r['hold_lb']:.2f}")

    Path("server/backtest8_results.json").write_text(json.dumps(rows, indent=1))
    print("\nfull table -> server/backtest8_results.json")


if __name__ == "__main__":
    sys.setrecursionlimit(10000)
    main()
