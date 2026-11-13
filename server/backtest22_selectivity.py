"""backtest22_selectivity.py — berapa winrate maksimum yang bisa dibeli dengan selektivitas?

Pertanyaan pemilik: "winrate minimum 80%". Prediksi arah hanya bisa dinaikkan dengan
mengambil lebih sedikit sinyal (gerbang keyakinan makin ketat). Skrip ini mengukur
kurva winrate-vs-jumlah-sinyal pada LOCKBOX (data yang tidak dipakai memilih), untuk
tiga sumber keyakinan, lalu membandingkannya dengan kontrol null (label diacak dalam
simbol+hari, sehingga seluruh pola hancur tetapi jumlah sinyal tetap).

Kunci: bila pada n tertentu data nyata dan null mencapai winrate yang sama, angka itu
datang dari seleksi derau, bukan dari informasi. Itulah bukti kuantitatif apakah 80%
mungkin dicapai pada n yang berguna.
"""
from __future__ import annotations

import numpy as np

import research_harness as H
from backtest16_reverse import feats
from backtest17_patterns import design, knn

K = 25


def curve(name: str, score: np.ndarray, y: np.ndarray, day: np.ndarray, sym: np.ndarray,
          gates: np.ndarray, null: bool = False) -> None:
    print(f"\n== {name}{' (NULL: label diacak per simbol-hari)' if null else ''} ==")
    yy = y.copy()
    if null:
        rng = np.random.default_rng(11)
        key = np.array([f"{a}|{b}" for a, b in zip(sym, day)])
        for k in np.unique(key):
            m = key == k
            yy[m] = rng.permutation(yy[m])
    conf = np.abs(score)
    pred = np.sign(score)
    print(f"{'gate':>7} {'n':>8} {'winrate':>8} {'lb95':>6}")
    for g in gates:
        m = (pred != 0) & (conf >= g)
        n = int(m.sum())
        if n < 20:
            print(f"{g:7.2f} {n:8d} {'—':>8}")
            continue
        w = int((pred[m] == yy[m]).sum())
        print(f"{g:7.2f} {n:8d} {w / n * 100:7.2f}% {H.wilson_low(w, n):6.2f}")
    # berapa winrate maksimum yang bisa ditemukan di subset kecil? cari di antara sinyal
    # berkeyakinan tertinggi, ukuran subset dari 5..5000, tanpa memilih dari data uji:
    print("  maksimum menurut ukuran subset berkeyakinan tertinggi (tanpa pemilihan):")
    order = np.argsort(-conf)
    for n in (10, 50, 200, 1000, 5000):
        if n > len(order):
            continue
        idx = order[:n]
        m = pred[idx] != 0
        if m.sum() < 5:
            continue
        w = int((pred[idx][m] == yy[idx][m]).sum()); k = int(m.sum())
        print(f"    n={k:5d} winrate={w / k * 100:6.2f}% lb95={H.wilson_low(w, k):5.2f}")


def main() -> None:
    tabs = H.load_tables()
    Xs, ys, ts, ss = [], [], [], []
    for name, T in tabs.items():
        X, y, t = design(T)
        Xs.append(X); ys.append(y); ts.append(t); ss.append(np.full(len(y), name, dtype=object))
    X, y, t, sym = np.vstack(Xs), np.concatenate(ys), np.concatenate(ts), np.concatenate(ss)
    d0 = t.min() // H.DAY
    day = t // H.DAY - d0
    tr = day < H.HOLDOUT_FROM_DAY
    lb = day >= H.LOCKBOX_FROM_DAY
    mu, sd = X[tr].mean(0), X[tr].std(0) + 1e-9
    Z = ((X - mu) / sd).astype(np.float32)
    print(f"train={tr.sum():,} lockbox={lb.sum():,}")

    idx = np.flatnonzero(lb)
    score_knn = np.empty(len(idx))
    for s in range(0, len(idx), 2000):
        chunk = idx[s:s + 2000]
        score_knn[s:s + 2000] = knn(Z[tr], y[tr], Z[chunk], k=K)
    gates = np.array([0.0, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4])
    curve("kNN analog k=25 (keyakinan = |p-0.5|)", score_knn, y[idx], day[idx], sym[idx], gates)

    # z-score: keyakinan = |z|
    zs, ys2 = [], []
    for name, T in tabs.items():
        f = feats(T)
        z = np.where(T["ok"] & f["volhi"], f["z"], np.nan)
        zs.append(z[lb[0:0]] if False else z)
    zall = np.concatenate(zs)
    m = np.isfinite(zall) & lb
    pred = np.where(zall[m] < 0, -1.0, 1.0)
    curve("z-score volHI (keyakinan = |z|)", pred * np.abs(zall[m]), y[m], day[m], sym[m],
          np.array([0.0, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 6.0]))

    # null untuk kNN
    curve("kNN analog k=25", score_knn, y[idx], day[idx], sym[idx], gates, null=True)
    curve("z-score volHI", pred * np.abs(zall[m]), y[m], day[m], sym[m],
          np.array([0.0, 2.0, 3.0, 4.0, 5.0, 6.0]), null=True)


if __name__ == "__main__":
    main()
