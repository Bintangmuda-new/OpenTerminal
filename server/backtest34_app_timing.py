"""backtest34_app_timing.py - kandidat TREND dengan timing app (decide di close candle i).

entry = open candle i+1. Hasil diukur untuk exit close i+1 (1m) dan close i+5.
Split train hari 0-5 / holdout hari 6 / lockbox hari 7.
Jalankan: python server/backtest34_app_timing.py
"""
from __future__ import annotations

import numpy as np

import backtest29_trend as B29
from research_harness import DAY, _day_index, load_tables, wilson_low


def app_feats(T):
    """Sama seperti backtest29 tapi 'sekarang' = close candle i yang sudah tertutup."""
    if "_af" in T:
        return T["_af"]
    c, h, l = T["c"], T["h"], T["l"]
    e9, e21, e50 = B29.ema(c, 9), B29.ema(c, 21), B29.ema(c, 50)
    atr = B29.roll_mean(B29.shift(h - l), 14)
    dev = B29.shift(B29.roll_mean(np.abs(np.diff(c, prepend=c[0])), 20))
    sma20 = B29.roll_mean(c, 20)
    f = {"atr": atr,
         "z": np.where(dev > 0, (c - sma20) / np.where(dev > 0, dev, 1), 0.0),
         "stack_up": (e9 > e21) & (e21 > e50),
         "stack_dn": (e9 < e21) & (e21 < e50),
         "volhi": (h - l) > np.nan_to_num(atr)}
    T["_af"] = f
    return f


def pick(T, name):
    f = app_feats(T)
    if name == "stack + |z|<1.5":
        return np.where(f["stack_up"] & (np.abs(f["z"]) < 1.5), 1,
                        np.where(f["stack_dn"] & (np.abs(f["z"]) < 1.5), -1, 0))
    if name == "stack + |z|<1.5 + volhi":
        return np.where(f["stack_up"] & (np.abs(f["z"]) < 1.5) & f["volhi"], 1,
                        np.where(f["stack_dn"] & (np.abs(f["z"]) < 1.5) & f["volhi"], -1, 0))
    if name == "stack + |z|<1.0":
        return np.where(f["stack_up"] & (np.abs(f["z"]) < 1.0), 1,
                        np.where(f["stack_dn"] & (np.abs(f["z"]) < 1.0), -1, 0))
    if name == "stack 9/21/50":
        return np.where(f["stack_up"], 1, np.where(f["stack_dn"], -1, 0))
    if name == "stack + z<-1 (pullback)":
        return np.where(f["stack_up"] & (f["z"] < -1.0), 1,
                        np.where(f["stack_dn"] & (f["z"] > 1.0), -1, 0))
    raise KeyError(name)


def run(tabs, name, h):
    d0 = _day_index(tabs)
    agg = {"train": [0, 0], "holdout": [0, 0], "lockbox": [0, 0]}
    for T in tabs.values():
        B29.labels({"_": T}, h) if f"y{h}" not in T else None
        d = pick(T, name)
        d = np.where(T[f"ok{h}"], d, 0.0)
        idx = np.flatnonzero(d != 0)
        if len(idx) == 0:
            continue
        win = (T[f"y{h}"][idx] == d[idx])
        day = T["t"][idx] // DAY - d0
        for seg, msk in (("train", day < 6), ("holdout", day == 6), ("lockbox", day >= 7)):
            agg[seg][0] += int(win[msk].sum()); agg[seg][1] += int(msk.sum())
    out = []
    for seg in ("train", "holdout", "lockbox"):
        w, n = agg[seg]
        out.append((w / n * 100 if n else 0.0, n, wilson_low(w, n)))
    tr, ho, lk = out
    print(f"h={h} {name:30} train {tr[0]:6.2f}% n={tr[1]:7d} | HOLD {ho[0]:6.2f}% n={ho[1]:7d} lb95={ho[2]:5.2f} "
          f"| LOCK {lk[0]:6.2f}% n={lk[1]:7d}")


def main():
    tabs = load_tables()
    for h in (1, 5):
        for name in ("stack 9/21/50", "stack + |z|<1.5", "stack + |z|<1.0",
                     "stack + |z|<1.5 + volhi", "stack + z<-1 (pullback)"):
            run(tabs, name, h)
        print()
    print("Timing: decide di close candle i (seperti app), entry open i+1, exit close i+h.")


if __name__ == "__main__":
    main()
