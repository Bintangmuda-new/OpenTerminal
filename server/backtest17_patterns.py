"""backtest17_patterns.py — pencocokan pola (analog / nearest-neighbour) + model nonlinier.

"Algoritma tingkat tinggi" yang diuji jujur pada T-5:
  1. Analog pattern matching: bentuk 10 candle terakhir (dinormalisasi ATR) + candle
     berjalan T-5 dicari k tetangga terdekat di TRAIN; arah mayoritas tetangga = sinyal.
     Ini cara kerja "pola market berulang". Bila generator punya pola, kNN akan menemukannya.
  2. Logistic regression dengan fitur interaksi orde-2 (24 fitur -> ~300), L2, dilatih
     di TRAIN saja, diuji di HOLDOUT, dengan gerbang keyakinan.
Tanpa dependency baru: numpy saja.
"""
from __future__ import annotations

import numpy as np

from research_harness import DAY, HOLDOUT_FROM_DAY, LOCKBOX_FROM_DAY, load_tables, wilson_low

LOOK = 10


def design(T: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Baris i: return 10 menit tertutup sebelum i + candle berjalan T-5, dinormalisasi ATR."""
    o, h, l, c = T["o"], T["h"], T["l"], T["c"]
    n = len(c)
    rng = h - l
    atr = np.convolve(rng, np.ones(14) / 14, mode="full")[:n]
    atr = np.concatenate([[np.nan], atr[:-1]])  # hanya menit < i
    cols = []
    for k in range(1, LOOK + 1):
        ck = np.concatenate([np.full(k, np.nan), c[:-k]])
        ok_ = np.concatenate([np.full(k, np.nan), o[:-k]])
        hk = np.concatenate([np.full(k, np.nan), h[:-k]])
        lk = np.concatenate([np.full(k, np.nan), l[:-k]])
        cols += [(ck - ok_) / atr, (hk - np.maximum(ck, ok_)) / atr, (np.minimum(ck, ok_) - lk) / atr]
    cols += [(T["c5"] - T["o5"]) / atr, (T["h5"] - T["l5"]) / atr,
             (T["c5"] - T["l5"]) / np.maximum(T["h5"] - T["l5"], 1e-12)]
    X = np.column_stack(cols)
    good = T["ok"] & (T["y"] != 0) & np.isfinite(X).all(1) & (atr > 0)
    return np.clip(X[good], -5, 5), T["y"][good].astype(float), T["t"][good]


def stack(tabs: dict):
    Xs, ys, ts = [], [], []
    for T in tabs.values():
        X, y, t = design(T)
        Xs.append(X); ys.append(y); ts.append(t)
    X, y, t = np.vstack(Xs), np.concatenate(ys), np.concatenate(ts)
    d0 = t.min() // DAY
    dd = t // DAY - d0
    keep = dd < LOCKBOX_FROM_DAY               # lockbox tidak pernah disentuh riset
    X, y, dd = X[keep], y[keep], dd[keep]
    hold = dd >= HOLDOUT_FROM_DAY
    return X, y, hold, dd


def report(name: str, pred: np.ndarray, y: np.ndarray, conf: np.ndarray, gates=(0.0,)) -> None:
    for g in gates:
        m = (pred != 0) & (conf >= g)
        n = int(m.sum())
        if n == 0:
            continue
        w = int((pred[m] == y[m]).sum())
        print(f"  {name:28} gate={g:5.3f} HOLD {w / n * 100:6.2f}% n={n:7d} lb95={wilson_low(w, n):5.2f}", flush=True)


def knn(Xtr, ytr, Xte, k=50, batch=2000):
    """Analog: arah rata-rata k tetangga terdekat (Euclidean) dari TRAIN."""
    rng = np.random.default_rng(0)
    if len(Xtr) > 120_000:                       # ponytail: subsample train; FAISS bila perlu penuh
        idx = rng.choice(len(Xtr), 120_000, replace=False)
        Xtr, ytr = Xtr[idx], ytr[idx]
    Xtr32, Xte32 = Xtr.astype(np.float32), Xte.astype(np.float32)
    n2 = (Xtr32 ** 2).sum(1)
    score = np.empty(len(Xte))
    for s in range(0, len(Xte), batch):
        q = Xte32[s:s + batch]
        d = n2[None, :] - 2 * q @ Xtr32.T
        nn = np.argpartition(d, k, axis=1)[:, :k]
        score[s:s + batch] = ytr[nn].mean(1)
    return score


def logistic(X, y, lam=1.0, iters=300, lr=0.5):
    w = np.zeros(X.shape[1]); b = 0.0
    yy = (y > 0).astype(float)
    for _ in range(iters):
        p = 1 / (1 + np.exp(-(X @ w + b)))
        g = p - yy
        w -= lr * (X.T @ g / len(y) + lam * w / len(y))
        b -= lr * g.mean()
    return w, b


def poly2(X):
    i, j = np.triu_indices(X.shape[1])
    return np.hstack([X, X[:, i] * X[:, j]])


def main() -> None:
    tabs = load_tables()
    X, y, hold, day = stack(tabs)
    print(f"rows={len(y):,} train={int((~hold).sum()):,} holdout={int(hold.sum()):,} fitur={X.shape[1]}")
    mu, sd = X[~hold].mean(0), X[~hold].std(0) + 1e-9
    Z = (X - mu) / sd

    print("\n== 1. analog pattern matching (kNN) ==")
    rng = np.random.default_rng(1)
    te = np.flatnonzero(hold)
    te = rng.choice(te, min(len(te), 60_000), replace=False)
    for k in (25, 100, 400):
        s = knn(Z[~hold], y[~hold], Z[te], k=k)
        report(f"kNN k={k}", np.sign(s), y[te], np.abs(s), gates=(0.0, 0.1, 0.2, 0.3))

    print("\n== 2. logistic orde-2 (interaksi) ==")
    P = poly2(Z[:, -12:])                      # 3 candle terakhir + candle T-5 -> interaksi
    w, b = logistic(P[~hold], y[~hold])
    p = 1 / (1 + np.exp(-(P[hold] @ w + b)))
    report("logit2", np.sign(p - 0.5), y[hold], np.abs(p - 0.5), gates=(0.0, 0.01, 0.02, 0.05))
    pt = 1 / (1 + np.exp(-(P[~hold] @ w + b)))
    report("logit2 (TRAIN, in-sample)", np.sign(pt - 0.5), y[~hold], np.abs(pt - 0.5), gates=(0.0, 0.02))


if __name__ == "__main__":
    main()
