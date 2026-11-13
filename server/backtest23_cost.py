"""backtest23_cost.py — vonis ekonomi: berapa asimetri menang-kalah yang nyata, dan payout
minimum agar tidak rugi?

Konversi yang benar untuk binary option (stake 1, payout bersih r, draw mengembalikan stake):
    w = P(menang), l = P(kalah), dr = P(draw),  d = w - l  (asimetri)
    E = r*w - l  =  0.5*[(r-1)*(1-dr) + d*(r+1)]
    titik impas:  d* = (1-r)*(1-dr)/(1+r)

Jadi klaim "winrate 80%" TIDAK cukup: pada payout r, yang menentukan untung/rugi adalah d.
Skrip ini mengukur d (beserta batas bawah 95% dan CI bootstrap) pada LOCKBOX untuk setiap
kandidat sinyal, lalu menghitung payout minimum yang dibutuhkan kandidat itu agar impas.
Kandidat dengan batas bawah d > 0 = satu-satunya yang bernilai; sisanya = nol.
"""
from __future__ import annotations

import numpy as np

import research_harness as H
from backtest16_reverse import feats
from backtest17_patterns import design, knn

K, GATE, BATCH = 25, 0.10, 2000


def asym(y: np.ndarray, pred: np.ndarray) -> tuple[int, int, int, float, float, float]:
    """(menang, kalah, draw, d, lb95(d), payout_impas) untuk pred != 0."""
    m = pred != 0
    yy, pp = y[m], pred[m]
    w = int((yy == pp).sum())
    l = int((yy == -pp).sum())
    dr = int((yy == 0).sum())
    n = w + l + dr
    d = (w - l) / n * 100
    # galat baku asimetri: Var(w-l)/n^2 -> sigma = sqrt((w+l)/n^2 - d^2)/... pakai bentuk langsung
    sd = np.sqrt(max((w + l) / n - (d / 100) ** 2, 0.0) / n) * 100
    lb = d - 1.96 * sd
    pdr = dr / n
    r_be = None if d <= 0 else (1 - pdr) / (1 + d / 100)  # d* = (1-r)(1-dr)/(1+r) diselesaikan utk r
    return w, l, dr, d, lb, (r_be if r_be is not None else float("nan"))


def boot(y: np.ndarray, pred: np.ndarray, reps: int = 2000, seed: int = 5) -> tuple[float, float]:
    m = pred != 0
    yy = (y[m] == pred[m]).astype(np.int8) - (y[m] == -pred[m]).astype(np.int8)
    rng = np.random.default_rng(seed)
    n = len(yy)
    ds = np.array([yy[rng.integers(0, n, n)].mean() for _ in range(reps)]) * 100
    return float(np.percentile(ds, 2.5)), float(np.percentile(ds, 97.5))


def row(name: str, y: np.ndarray, pred: np.ndarray) -> None:
    w, l, dr, d, lb, r_be = asym(y, pred)
    lo, hi = boot(y, pred)
    print(f"{name:34} n={w + l + dr:7d} W={w / (w + l + dr) * 100:6.2f}% L={l / (w + l + dr) * 100:6.2f}% "
          f"draw={dr / (w + l + dr) * 100:5.2f}% | d={d:+6.3f}pt lb95={lb:+6.3f} CI95=[{lo:+5.2f},{hi:+5.2f}] "
          f"| impas butuh payout {r_be * 100 if r_be == r_be else float('nan'):5.2f}%", flush=True)


def main() -> None:
    tabs = H.load_tables()
    Xs, ys, ts = [], [], []
    for T in tabs.values():
        X, y, t = design(T)
        Xs.append(X); ys.append(y); ts.append(t)
    X, y, t = np.vstack(Xs), np.concatenate(ys), np.concatenate(ts)
    d0 = t.min() // H.DAY
    day = t // H.DAY - d0
    tr = day < H.HOLDOUT_FROM_DAY
    lb = day >= H.LOCKBOX_FROM_DAY
    mu, sd = X[tr].mean(0), X[tr].std(0) + 1e-9
    Z = ((X - mu) / sd).astype(np.float32)
    print(f"lockbox={lb.sum():,} (d = P(menang) - P(kalah); draw tidak menang & tidak kalah)\n")

    idx = np.flatnonzero(lb)
    score = np.empty(len(idx))
    for s in range(0, len(idx), BATCH):
        score[s:s + BATCH] = knn(Z[tr], y[tr], Z[idx[s:s + BATCH]], k=K)
    yl = y[idx]
    row("kontrol arah CALL terus", yl, np.ones(len(yl)))
    row("kontrol arah PUT terus", yl, -np.ones(len(yl)))
    row("kNN analog k=25", yl, np.sign(score))
    row("kNN analog k=25 gate 0.10", yl, np.where(np.abs(score) >= GATE, np.sign(score), 0.0))

    # z-score volHI (aturan yang sekarang ada di aplikasi): bangun sepanjang SELURUH baris
    # design (urutan yang sama dengan X), baru disaring oleh idx — bukan ditulis kontigu.
    zfull = np.full(len(y), np.nan)
    off = 0
    for T in tabs.values():
        _, _, t_ = design(T)
        f = feats(T)
        zfull[off:off + len(t_)] = np.where(T["ok"] & f["volhi"], f["z"], np.nan)[
            np.searchsorted(T["t"], t_)]
        off += len(t_)
    zall = zfull[idx]
    for thr in (0.0, 2.0, 3.0):
        m = np.isfinite(zall) & (np.abs(zall) > thr)
        pred = np.zeros(len(yl))
        pred[m] = np.where(zall[m] < 0, 1.0, -1.0)   # z<-thr -> CALL (mean reversion)
        row(f"z-score volHI |z|>{thr:g}", yl, pred)

    print("\nAmbang payout agar sinyal tidak rugi (dari batas bawah 95% d):")
    for r in (0.80, 0.85, 0.90, 0.92, 0.95):
        w, l, dr, d, lb_, _ = asym(yl, np.sign(score))
        pdr = dr / (w + l + dr)
        d_need = (1 - r) * (1 - pdr) / (1 + r) * 100
        print(f"  payout {r * 100:.0f}%: butuh d >= {d_need:+.3f}pt; kNN k=25 punya d={d:+.3f}pt (lb95 {lb_:+.3f}) "
              f"-> {'LOLOS' if lb_ > d_need else 'GAGAL'}")


if __name__ == "__main__":
    main()
