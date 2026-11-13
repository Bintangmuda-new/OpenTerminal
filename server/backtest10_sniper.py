"""backtest10_sniper.py — hunt for a rule strict enough to clear 70%.

backtest8/9 closed the ordinary search: z-score, RSI, Bollinger, streaks,
momentum bursts, day-folds — everything flattens onto 50%.

This script tests the mechanisms that had NOT been examined, plus the extreme
"every gate must agree" tier the user asked for:

  A. Round-level magnetism. OTC feeds are generated, and generated prices often
     mean-revert at quotable levels. Distance to the nearest round number,
     measured in ATR, has never been scored here.
  B. Higher-timeframe confluence. M5/M15 direction as a gate on an M1 entry.
  C. Hour-of-day selection (broker generation is not necessarily stationary).
  D. Sniper tiers: 4, 5, 6 and 7 simultaneous gates. If selectivity alone can
     buy accuracy, the hit rate must climb monotonically with the gate count.
     That monotonic climb is the falsifiable prediction — and it is checked.
  E. Per-symbol selection: rank symbols on train, keep only the top decile,
     then score on holdout. This is what a trader does when they say "this rule
     works on XAUUSD".
"""
import itertools
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

DB = Path(__file__).resolve().parent.parent / "data" / "otc_research.sqlite3"
DAY = 86400


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


def round_step(price):
    """The 'quotable' level spacing for an instrument at this price."""
    mag = 10 ** np.floor(np.log10(max(price, 1e-9)))
    return mag / 10.0


def load():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    frames = []
    for (symbol,) in con.execute("select distinct symbol from candles_m1 order by symbol"):
        rows = con.execute(
            "select time,open,high,low,close from candles_m1 where symbol=? order by time", (symbol,)
        ).fetchall()
        if len(rows) < 600:
            continue
        a = np.array(rows, dtype=float)
        t, o, h, l, c = (a[:, i] for i in range(5))
        n = len(c)
        rng = h - l
        body = np.abs(c - o)
        atr = sma(rng, 14)
        dev = sma(np.abs(np.diff(c, prepend=c[0])), 20)
        f = {
            "symbol": symbol, "t": t, "o": o, "h": h, "l": l, "c": c, "n": n,
            "rsi": rsi_series(c, 14), "atr": atr, "dev": dev,
            "up": np.sign(c - o),
            "next_up": np.concatenate([np.sign(c - o)[1:], [0.0]]),
            "rng": rng, "body": body, "sma20": sma(c, 20),
            "e21": ema(c, 21), "e50": ema(c, 50),
            "day": (t // DAY).astype(int),
            "hour": (((t // 60) // 60) % 24).astype(int),
        }
        f["z"] = np.where(dev > 0, (c - f["sma20"]) / dev, 0.0)
        f["body_atr"] = np.where(atr > 0, body / atr, 0.0)
        ratio = np.where(c > 0, rng / c, 0.0)
        f["hi_vol"] = ratio > np.array([np.median(ratio[max(0, i - 59) : i + 1]) for i in range(n)])
        # A. distance from the running close to the nearest round level, in ATR.
        step = np.array([round_step(x) for x in c])
        f["level_dist"] = np.where(atr > 0, np.abs((c / step) - np.round(c / step)) * step / atr, 9.0)
        # B. higher-timeframe direction: close vs its own M5/M15 smoothed mean.
        m5c, m15c = [], []
        for i in range(n):
            m5c.append(c[max(0, i - 4) : i + 1].mean())
            m15c.append(c[max(0, i - 14) : i + 1].mean())
        f["m5"] = np.sign(c - np.array(m5c))
        f["m15"] = np.sign(c - np.array(m15c))
        # last candle's own colour vs the candle before: a two-bar turn.
        f["turn"] = np.where((f["up"] > 0) & (np.roll(f["up"], 1) < 0), 1.0,
                             np.where((f["up"] < 0) & (np.roll(f["up"], 1) > 0), -1.0, 0.0))
        # close position inside the candle's own range (0 = at low, 1 = at high)
        f["cls_pos"] = np.where(rng > 0, (c - l) / rng, 0.5)
        frames.append(f)
    con.close()
    return frames


def gates(f):
    """Each gate returns bool array; a sniper trade needs the picked ones true."""
    return {
        "z2.5": f["z"] > 2.5,          # bearish stretch
        "z-2.5": f["z"] < -2.5,        # bullish stretch
        "rsi70": f["rsi"] > 70,
        "rsi30": f["rsi"] < 30,
        "volHI": f["hi_vol"],
        "bodyBig": f["body_atr"] > 1.0,
        "clsHigh": f["cls_pos"] > 0.8,
        "clsLow": f["cls_pos"] < 0.2,
        "m5dn": f["m5"] < 0,
        "m5up": f["m5"] > 0,
        "m15dn": f["m15"] < 0,
        "m15up": f["m15"] > 0,
        "levelNear": f["level_dist"] < 0.25,
        "bearTurn": f["turn"] < 0,
        "bullTurn": f["turn"] > 0,
    }


def sniper(frames, call_gates, put_gates, report_name=None, collect=False):
    """CALL when every call_gate holds, PUT when every put_gate holds."""
    out = []
    for f in frames:
        g = gates(f)
        call = np.ones(f["n"], bool)
        put = np.ones(f["n"], bool)
        for name in call_gates:
            call &= g[name]
        for name in put_gates:
            put &= g[name]
        d = np.where(call, 1.0, np.where(put, -1.0, 0.0))
        ok = (d != 0)
        ok[-1] = False
        ok &= f["next_up"] != 0
        idx = np.flatnonzero(ok)
        if collect:
            out.append((f["symbol"], f["day"][idx], (d[idx] == f["next_up"][idx]).astype(int)))
        elif len(idx):
            hit = (d[idx] == f["next_up"][idx])
            out.append((f["symbol"], f["day"][idx], hit.astype(int)))
    return out


def summarize(name, per_symbol):
    hits = np.concatenate([h for _, _, h in per_symbol]) if per_symbol else np.array([])
    n = len(hits)
    if n == 0:
        print(f"{name:46}     0 signals")
        return (0.0, 0, 0.0)
    w = int(hits.sum())
    by_sym = defaultdict(lambda: [0, 0])
    for s, _, h in per_symbol:
        by_sym[s][0] += int(h.sum())
        by_sym[s][1] += len(h)
    syms_pos = sum(1 for v in by_sym.values() if v[1] >= 15 and v[0] / v[1] > 0.5)
    syms_n = sum(1 for v in by_sym.values() if v[1] >= 15)
    print(f"{name:46} {w / n * 100:6.2f}% n={n:6d} lb95={wilson_low(w, n):5.2f} sym+ {syms_pos}/{syms_n}")
    return (w / n * 100, n, wilson_low(w, n))


def main():
    frames = load()
    print(f"symbols={len(frames)} bars={sum(f['n'] for f in frames)}\n")

    print("=== D. sniper tier: how many gates must agree before accuracy moves?")
    tiers = [
        ([], []),
        (["z2.5"], ["z-2.5"]),
        (["z2.5", "rsi70"], ["z-2.5", "rsi30"]),
        (["z2.5", "rsi70", "volHI"], ["z-2.5", "rsi30", "volHI"]),
        (["z2.5", "rsi70", "volHI", "bearTurn"], ["z-2.5", "rsi30", "volHI", "bullTurn"]),
        (["z2.5", "rsi70", "volHI", "bearTurn", "bodyBig"], ["z-2.5", "rsi30", "volHI", "bullTurn", "bodyBig"]),
        (["z2.5", "rsi70", "volHI", "bearTurn", "bodyBig", "m5dn"], ["z-2.5", "rsi30", "volHI", "bullTurn", "bodyBig", "m5up"]),
        (["z2.5", "rsi70", "volHI", "bearTurn", "bodyBig", "m5dn", "levelNear"],
         ["z-2.5", "rsi30", "volHI", "bullTurn", "bodyBig", "m5up", "levelNear"]),
    ]
    for call_g, put_g in tiers:
        label = f"{len(call_g)} gates: " + ("+".join(call_g) or "any direction")
        summarize(label, sniper(frames, call_g, put_g))

    print("\n=== A. round-level magnetism alone")
    summarize("level_dist<0.25 vs candle colour (fade)", sniper(frames, ["levelNear", "clsHigh"], ["levelNear", "clsLow"]))
    summarize("level_dist<0.10 vs candle colour (fade)", frames and sniper(
        frames, [], []) if False else _level_tier(frames, 0.10))
    summarize("level_dist<0.05 vs candle colour (fade)", _level_tier(frames, 0.05))

    print("\n=== C. hour-of-day selectivity on the z2.5 rule (train-picked hours)")
    hour_rows = {}
    for hour in range(24):
        per = []
        for f in frames:
            g = gates(f)
            call = g["z2.5"]
            put = g["z-2.5"]
            d = np.where(call, -1.0, np.where(put, 1.0, 0.0))
            ok = (d != 0) & (f["hour"] == hour)
            ok[-1] = False
            ok &= f["next_up"] != 0
            idx = np.flatnonzero(ok)
            if len(idx):
                per.append((f["symbol"], f["day"][idx], (d[idx] == f["next_up"][idx]).astype(int)))
        h = np.concatenate([h for _, _, h in per]) if per else np.array([])
        hour_rows[hour] = (int(h.sum()), len(h))
    best = sorted(hour_rows.items(), key=lambda kv: -(kv[1][0] / kv[1][1] if kv[1][1] >= 100 else 0))[:6]
    for hour, (w, n) in best:
        print(f"  hour {hour:02d}Z  {w / n * 100 if n else 0:6.2f}%  n={n}")
    tot_w = sum(w for w, n in hour_rows.values())
    tot_n = sum(n for w, n in hour_rows.values())
    print(f"  all hours {tot_w / tot_n * 100:.2f}% n={tot_n}")

    print("\n=== E. per-symbol selection (best symbols on TRAIN, scored on HOLDOUT)")
    all_t = np.concatenate([f["t"] for f in frames])
    cut = float(np.quantile(all_t, 0.6))
    train, hold = [], []
    for f in frames:
        g = gates(f)
        d = np.where(g["z2.5"], -1.0, np.where(g["z-2.5"], 1.0, 0.0))
        ok = (d != 0)
        ok[-1] = False
        ok &= f["next_up"] != 0
        idx = np.flatnonzero(ok)
        hit = (d[idx] == f["next_up"][idx]).astype(int)
        is_tr = f["t"][idx] < cut
        train.append((f["symbol"], hit[is_tr]))
        hold.append((f["symbol"], hit[~is_tr]))
    tr = defaultdict(lambda: [0, 0])
    for s, h in train:
        tr[s][0] += int(h.sum())
        tr[s][1] += len(h)
    ranked = sorted(((v[0] / v[1], s) for s, v in tr.items() if v[1] >= 80), reverse=True)
    print(f"  symbols with >=80 train trades: {len(ranked)}")
    for frac in (0.1, 0.2, 0.3):
        keep = {s for _, s in ranked[: max(1, int(len(ranked) * frac))]}
        hw = hn = 0
        for s, h in hold:
            if s in keep:
                hw += int(h.sum())
                hn += len(h)
        if hn:
            print(f"  top {int(frac * 100)}% train symbols -> holdout {hw / hn * 100:6.2f}% n={hn} lb95={wilson_low(hw, hn):.2f}")
        else:
            print(f"  top {int(frac * 100)}% -> no holdout trades")
    top = ranked[:5]
    print("  top train symbols:", ", ".join(f"{s} {r * 100:.1f}%" for r, s in top))


def _level_tier(frames, thr):
    per = []
    for f in frames:
        near = f["level_dist"] < thr
        call = near & (f["cls_pos"] > 0.8)
        put = near & (f["cls_pos"] < 0.2)
        d = np.where(call, -1.0, np.where(put, 1.0, 0.0))
        ok = (d != 0)
        ok[-1] = False
        ok &= f["next_up"] != 0
        idx = np.flatnonzero(ok)
        if len(idx):
            per.append((f["symbol"], f["day"][idx], (d[idx] == f["next_up"][idx]).astype(int)))
    return per


if __name__ == "__main__":
    main()
