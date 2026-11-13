"""backtest35_pullback.py - validasi kandidat terbaik keluarga TREND: pullback resume.

Aturan: tren EMA 9/21/50 searah, harga (close candle tertutup) berada > z ATR
melawan tren -> entry searah tren. Timing app: decide di close candle i,
entry open i+1, exit close i+5.
Uji: per hari, per simbol, sensitivitas ambang (dilaporkan apa adanya termasuk
yang buruk), null test per-simbol 20x.
Jalankan: python server/backtest35_pullback.py
"""
from __future__ import annotations

import numpy as np

import backtest29_trend as B29
from research_harness import DAY, _day_index, load_tables, wilson_low

H = 5


def feats(T):
    return B29.feats(T)


def signal(T, z, use_momentum=False):
    f = feats(T)
    up = f["stack_up"] & (f["z"] < -z)
    dn = f["stack_dn"] & (f["z"] > z)
    if use_momentum:
        f2 = B29.feats(T)
        up &= (f2["c5"] > f2["o5"]) if "o5" in f2 else True
        dn &= True
    d = np.where(up, 1, np.where(dn, -1, 0))
    return np.where(T[f"ok{H}"], d, 0)


def segment(T, d, y):
    idx = np.flatnonzero(d != 0)
    if len(idx) == 0:
        return None
    return T["t"][idx], T["y5"][idx] if y is None else y[idx], d[idx]


def report(name, tabs, z, quiet=False):
    d0 = _day_index(tabs)
    agg = {"train": [0, 0], "holdout": [0, 0], "lockbox": [0, 0]}
    per_day = {}
    sym_w, sym_n = {}, {}
    for s, T in tabs.items():
        d = signal(T, z)
        idx = np.flatnonzero(d != 0)
        if len(idx) == 0:
            continue
        win = (T[f"y{H}"][idx] == d[idx])
        day = T["t"][idx] // DAY - d0
        for seg, msk in (("train", day < 6), ("holdout", day == 6), ("lockbox", day >= 7)):
            agg[seg][0] += int(win[msk].sum()); agg[seg][1] += int(msk.sum())
        for dd, ww in zip(day, win):
            per_day.setdefault(int(dd), [0, 0])
            per_day[int(dd)][0] += int(ww); per_day[int(dd)][1] += 1
        sym_w[s] = int(win.sum()); sym_n[s] = len(idx)
    out = {seg: (w / n * 100 if n else 0.0, n, wilson_low(w, n)) for seg, (w, n) in agg.items()}
    if not quiet:
        tr, ho, lk = out["train"], out["holdout"], out["lockbox"]
        folds = [v[0] / v[1] for v in per_day.values() if v[1] >= 30]
        syms = [sym_w[s] / sym_n[s] for s in sym_n if sym_n[s] >= 30]
        print(f"z={z:<4} train {tr[0]:6.2f}% n={tr[1]:7d} | HOLD {ho[0]:6.2f}% n={ho[1]:7d} lb95={ho[2]:5.2f} "
              f"| LOCK {lk[0]:6.2f}% n={lk[1]:7d} | hari>50 {sum(f > .5 for f in folds)}/{len(folds)} "
              f"| simbol>50 {sum(f > .5 for f in syms)}/{len(syms)}", flush=True)
    return out, per_day, sym_w, sym_n


def main():
    tabs = load_tables()
    for T in tabs.values():
        if f"y{H}" not in T:
            B29.labels({"_": T}, H)
    print("== sensitivitas ambang z (semua dilaporkan, bukan cuma yang bagus) ==")
    for z in (0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0):
        report("pullback", tabs, z)
    print("\n== detail per hari (z=1.0) ==")
    out, per_day, sym_w, sym_n = report("pullback", tabs, 1.0)
    for dd in sorted(per_day):
        w, n = per_day[dd]
        print(f"  hari {dd}: {w / n * 100:6.2f}%  n={n:7d}  ({'train' if dd < 6 else 'HOLDOUT' if dd == 6 else 'LOCKBOX'})")
    print("\n== null test per simbol, 20x (label diacak) ==")
    rng = np.random.default_rng(11)
    real = out["holdout"][0]
    nulls = []
    for i in range(20):
        w = n = 0
        for T in tabs.values():
            d = signal(T, 1.0)
            idx = np.flatnonzero(d != 0)
            if len(idx) == 0:
                continue
            day = T["t"][idx] // DAY - _day_index(tabs)
            m = day == 6
            if not m.any():
                continue
            y = T[f"y{H}"][idx].copy()
            rng.shuffle(y)
            w += int((y[m] == d[idx][m]).sum()); n += int(m.sum())
        nulls.append(w / n * 100 if n else 0.0)
    a = np.array(nulls)
    print(f"  nyata holdout {real:.2f}%  null mean={a.mean():.2f}% p95={np.percentile(a, 95):.2f}% max={a.max():.2f}%")
    print(f"  -> {'MENONJOL' if real > np.percentile(a, 95) else 'tidak menonjol'}")
    print(f"\n  breakeven payout 80% = 55,6%. Jarak dari {real:.2f}% = {55.6 - real:+.2f} poin.")


if __name__ == "__main__":
    main()
