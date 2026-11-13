"""backtest21_analog_select.py — protokol pemilihan yang benar untuk sinyal analog.

Masalah pada backtest17/20: konfigurasi kNN dipilih dengan melihat HOLDOUT, jadi
angka holdout-nya tidak bersih. Di sini:

  1. Grid varian diuji pada TRAIN saja (hari 0-5).
  2. Varian terbaik dipilih dari TRAIN (winrate, syarat n >= 2000).
  3. Varian itu dilaporkan pada HOLDOUT (hari 6) DAN LOCKBOX (hari 7) — dua-duanya
     belum pernah dipakai memilih.

Varian: jumlah tetangga k, gerbang keyakinan, subset fitur (bentuk candle saja vs
termasuk volatilitas), dan sumber tetangga (semua simbol vs simbol yang sama).
"""
from __future__ import annotations

import numpy as np

import research_harness as H
from backtest17_patterns import design, knn

K_GRID = (10, 25, 50, 100)
GATE_GRID = (0.0, 0.1, 0.2)
SUBSETS = {
    "shape": slice(0, 30),          # 10 candle x (body, upper wick, lower wick)
    "shape3": slice(21, 33),        # 3 candle terakhir + candle berjalan T-5
    "micro": slice(30, 33),         # hanya candle berjalan T-5
    "all": slice(0, 33),
}


def main() -> None:
    tabs = H.load_tables()
    Xs, ys, ts, ss = [], [], [], []
    for name, T in tabs.items():
        X, y, t = design(T)
        Xs.append(X); ys.append(y); ts.append(t); ss.append(np.full(len(y), name, dtype=object))
    X, y, t, sym = np.vstack(Xs), np.concatenate(ys), np.concatenate(ts), np.concatenate(ss)
    d0 = t.min() // H.DAY
    day = t // H.DAY - d0
    seg = {"train": day < H.HOLDOUT_FROM_DAY,
           "holdout": (day >= H.HOLDOUT_FROM_DAY) & (day < H.LOCKBOX_FROM_DAY),
           "lockbox": day >= H.LOCKBOX_FROM_DAY}
    mu, sd = X[seg["train"]].mean(0), X[seg["train"]].std(0) + 1e-9
    Z = ((X - mu) / sd).astype(np.float32)
    print(f"rows train={seg['train'].sum():,} holdout={seg['holdout'].sum():,} lockbox={seg['lockbox'].sum():,}")

    rng = np.random.default_rng(7)
    te = {k: rng.choice(np.flatnonzero(m), min(int(m.sum()), 40_000), replace=False) for k, m in seg.items()}

    results = []
    for sub_name, sl in SUBSETS.items():
        Zs = Z[:, sl]
        for src in ("allsym", "samesym"):
            for k in K_GRID:
                for g in GATE_GRID:
                    scores = {s: np.zeros(len(te[s])) for s in seg}
                    for s in seg:
                        idx = te[s]
                        if src == "allsym":
                            scores[s] = knn(Zs[seg["train"]], y[seg["train"]], Zs[idx], k=k)
                        else:
                            out = np.zeros(len(idx))
                            for name in np.unique(sym[idx]):
                                own = seg["train"] & (sym == name)
                                m = sym[idx] == name
                                if own.sum() < 200:
                                    out[m] = 0
                                    continue
                                out[m] = knn(Zs[own], y[own], Zs[idx[m]], k=min(k, max(5, own.sum() // 10)))
                            scores[s] = out
                    row = {"subset": sub_name, "src": src, "k": k, "gate": g}
                    for s in seg:
                        pred = np.sign(scores[s]); conf = np.abs(scores[s])
                        m = (pred != 0) & (conf >= g)
                        n = int(m.sum())
                        row[s] = (int((pred[m] == y[te[s]][m]).sum()), n)
                    results.append(row)
                    if row["train"][1] >= 2000:
                        print(f"  {sub_name:7} {src:8} k={k:3d} gate={g:3.1f} | "
                              f"train {row['train'][0] / row['train'][1] * 100:6.2f}% n={row['train'][1]:6d} | "
                              f"hold {row['holdout'][0] / max(row['holdout'][1], 1) * 100:6.2f}% n={row['holdout'][1]:6d}",
                              flush=True)

    eligible = [r for r in results if r["train"][1] >= 2000]
    best = max(eligible, key=lambda r: r["train"][0] / r["train"][1])
    print(f"\nDIPILIH dari TRAIN: {best['subset']} {best['src']} k={best['k']} gate={best['gate']} "
          f"-> train {best['train'][0] / best['train'][1] * 100:.2f}% n={best['train'][1]}")
    for s in ("holdout", "lockbox"):
        w, n = best[s]
        print(f"  {s.upper():8} {w / max(n, 1) * 100:6.2f}% n={n:6d} lb95={H.wilson_low(w, n):5.2f}")
    # pembanding: berapa varian yang train-nya >50 (uji berapa banyak yang selamat)
    over = [r for r in eligible if r["train"][0] / r["train"][1] > 0.5]
    print(f"varian train>50%: {len(over)}/{len(eligible)}; "
          f"di antaranya lockbox>50%: {sum(1 for r in over if r['lockbox'][0] / max(r['lockbox'][1], 1) > 0.5)}")


if __name__ == "__main__":
    main()
