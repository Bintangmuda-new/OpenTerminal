"""backtest20_knn_verify.py — verifikasi lockbox untuk kandidat analog-matching (kNN).

backtest17 menemukan k=25 gate 0.1 -> HOLDOUT 50.76% lb95 50.28 (n=41.595), konfigurasi
terbaik dari 12 yang dicoba. Itu masih pemilihan di holdout, jadi belum bukti.
Skrip ini:
  1. mengunci konfigurasi (k=25, gate 0.1) tanpa menyetel ulang,
  2. melatih HANYA dari hari 0-5 (train),
  3. mengukur LOCKBOX (hari 7) sekali — data yang tidak pernah dipakai memilih,
  4. melaporkan per hari & per simbol, plus kontrol: arah acak pada baris yang sama.

Bila lockbox tetap > 50 dengan lb95 > 50, sinyal analog punya isi. Bila jatuh ke ~50,
temuan holdout tadi adalah derau pemilihan.
"""
from __future__ import annotations

import numpy as np

import research_harness as H
from backtest17_patterns import design, knn

K, GATE = 25, 0.10
BATCH = 2000


def main() -> None:
    tabs = H.load_tables()
    Xs, ys, ts = [], [], []
    for T in tabs.values():
        X, y, t = design(T)
        Xs.append(X); ys.append(y); ts.append(t)
    X, y, t = np.vstack(Xs), np.concatenate(ys), np.concatenate(ts)
    d0 = t.min() // H.DAY
    day = t // H.DAY - d0
    tr, ho, lb = day < H.HOLDOUT_FROM_DAY, (day >= H.HOLDOUT_FROM_DAY) & (day < H.LOCKBOX_FROM_DAY), day >= H.LOCKBOX_FROM_DAY
    mu, sd = X[tr].mean(0), X[tr].std(0) + 1e-9
    Z = ((X - mu) / sd).astype(np.float32)
    print(f"rows train={tr.sum():,} holdout={ho.sum():,} lockbox={lb.sum():,} fitur={X.shape[1]}")

    for seg_name, seg in (("HOLDOUT", ho), ("LOCKBOX", lb)):
        idx = np.flatnonzero(seg)
        ys_seg = y[idx]
        score = np.empty(len(idx))
        for s in range(0, len(idx), BATCH):
            chunk = idx[s:s + BATCH]
            # tetangga dicari di TRAIN saja; baris uji di luar batch agar tidak ada self-match
            score[s:s + BATCH] = knn(Z[tr], y[tr], Z[chunk], k=K)
        pred = np.sign(score)
        conf = np.abs(score)
        for g in (0.0, GATE, 0.15, 0.2, 0.3):
            m = (pred != 0) & (conf >= g)
            n = int(m.sum())
            if n < 50:
                continue
            w = int((pred[m] == ys_seg[m]).sum())
            print(f"  {seg_name:8} gate={g:4.2f} {w / n * 100:6.2f}% n={n:7d} lb95={H.wilson_low(w, n):5.2f}", flush=True)
        m = (pred != 0) & (conf >= GATE)
        if m.sum() >= 50:
            print(f"  {seg_name:8} per hari:", " ".join(
                f"{int(dd)}:{(pred[m & (day[seg] == dd)] == ys_seg[m & (day[seg] == dd)]).mean() * 100:.1f}"
                for dd in np.unique(day[seg]) if (m & (day[seg] == dd)).sum() > 30))
            print(f"  {seg_name:8} kontrol arah PUT terus: {(ys_seg[m] == -1).mean() * 100:.2f}% "
                  f"(arah CALL {(ys_seg[m] == 1).mean() * 100:.2f}%)")


if __name__ == "__main__":
    main()
