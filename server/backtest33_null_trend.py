"""backtest33_null_trend.py - null test untuk kandidat terbaik keluarga TREND (h=5).

Kandidat: stack EMA9/21/50 searah + |z|<1,5, hasil = close menit i+5 vs entry
menit i+1 (backtest29). Null: acak label y5 di dalam tiap simbol 20x.
Kalau angka nyata tidak menonjol dari null, itu artefak seleksi.
Jalankan: python server/backtest33_null_trend.py
"""
from __future__ import annotations

import numpy as np

import backtest29_trend as B
from research_harness import load_tables, wilson_low

N_NULL = 20


def hit(T, y, h=5):
    f = B.feats(T)
    d = np.where(f["stack_up"] & (np.abs(f["z"]) < 1.5), 1,
                 np.where(f["stack_dn"] & (np.abs(f["z"]) < 1.5), -1, 0))
    d = np.where(T[f"ok{h}"], d, 0)
    idx = np.flatnonzero(d != 0)
    if len(idx) == 0:
        return 0, 0
    return int((y[idx] == d[idx]).sum()), len(idx)


def main():
    tabs = load_tables()
    for T in tabs.values():
        B.labels({"_": T}, 5)
    w = n = 0
    for T in tabs.values():
        ww, nn = hit(T, T["y5"])
        w += ww; n += nn
    wr = w / n * 100
    print(f"nyata   : {wr:6.2f}%  n={n:7d}  lb95={wilson_low(w, n):5.2f}")
    rng = np.random.default_rng(7)
    best = []
    for i in range(N_NULL):
        w2 = n2 = 0
        for T in tabs.values():
            y = T["y5"].copy()
            perm = rng.permutation(len(y))
            ww, nn = hit(T, y[perm])
            w2 += ww; n2 += nn
        v = w2 / n2 * 100
        best.append(v)
        print(f"  null {i + 1:2d}: {v:6.2f}%  n={n2}")
    b = np.array(best)
    print(f"\nnull mean={b.mean():.2f}%  p95={np.percentile(b, 95):.2f}%  max={b.max():.2f}%")
    print(f"nyata {wr:.2f}% vs null p95 {np.percentile(b, 95):.2f}%  -> "
          + ("MENONJOL" if wr > np.percentile(b, 95) else "TIDAK menonjol (artefak seleksi)"))
    print(f"breakeven payout 80% = 55,6%. Selisih ke nyata: {55.6 - wr:+.2f} poin.")


if __name__ == "__main__":
    main()
