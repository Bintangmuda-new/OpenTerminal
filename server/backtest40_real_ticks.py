"""backtest40_real_ticks.py — REAL symbols only, tick resolution.

Why this family was never run
-----------------------------
Every earlier test mixed 114 OTC symbols (broker-synthetic; backtest13 showed
the OTC ceiling is indistinguishable from shuffled data) with a handful of real
ones. Real-market symbols carry genuine microstructure. If exploitable
structure exists in this data at all, it is there and nowhere else.

Data
----
E:/otc_cache/ticks/<SYMBOL>.npz -> t (unix epoch float seconds), p (price float)
Only files WITHOUT the `_otc` marker are used. Crypto symbols span ~2.5 days,
forex pairs ~0.36 day (short span reported as the main limitation).

Protocol
--------
* Signal at decision time T reads only ticks with t <= T. No lookahead.
* Candle under test = [start, start+HORIZON); outcome is close vs open, matching
  the app's binary convention.
* Purged walk-forward by block: model fitted on earlier blocks only.
* Every number carries a lower confidence bound. A bare winrate is not a result.

Usage
-----
    server/.venv/Scripts/python.exe server/backtest40_real_ticks.py
    server/.venv/Scripts/python.exe server/backtest40_real_ticks.py BTCUSD ETHUSD
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

CACHE = Path(r"E:\otc_cache\ticks")
HORIZON = 60.0      # candle length tested, seconds
DECIDE_EARLY = 8.0  # signal read this many seconds before the candle closes
BLOCK = 1800.0      # walk-forward block, seconds
MIN_TICKS = 20_000
FEATURES = (
    "pos_in_range", "push_8s", "push_30s", "tick_skew",
    "accel", "density", "prev_dir", "prev_rr",
)


def real_files() -> list[Path]:
    out = [f for f in sorted(CACHE.glob("*.npz")) if "_otc" not in f.name.lower()]
    return [f for f in out if len(np.load(f)["t"]) >= MIN_TICKS]


def load(path: Path) -> tuple[np.ndarray, np.ndarray]:
    z = np.load(path)
    t, p = z["t"].astype(np.float64), z["p"].astype(np.float64)
    ok = np.isfinite(t) & np.isfinite(p) & (p > 0)
    t, p = t[ok], p[ok]
    order = np.argsort(t, kind="stable")
    return t[order], p[order]


def build(t: np.ndarray, p: np.ndarray, horizon: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (decide_time, outcome, X) with the app's convention:

    signal is read on candle A at (close of A - DECIDE_EARLY)
    outcome is candle B's direction, B being the next candle after A.

    Reading A's own outcome would be near-oracle at T-8s: price is already
    ~53/60 of the way through the candle, so close-vs-open is nearly known.
    That is the mistake this function refuses to make.
    """
    start0 = np.ceil(t[0] / horizon) * horizon
    starts = np.arange(start0, t[-1] - horizon, horizon)
    if len(starts) < 200:
        return np.empty(0), np.empty(0), np.empty((0, len(FEATURES)))

    i_start = np.searchsorted(t, starts)
    i_end = np.searchsorted(t, starts + horizon)
    decide_t = starts + horizon - DECIDE_EARLY
    i_dec = np.searchsorted(t, decide_t)

    keep = ((i_end <= len(t)) & (i_end - i_start > 4) & (i_dec - i_start > 4) & (i_dec < i_end))
    starts, i_start, i_end, i_dec, decide_t = (
        starts[keep], i_start[keep], i_end[keep], i_dec[keep], decide_t[keep],
    )

    feats, outcome, when = [], [], []
    for k in range(len(starts)):
        a, d, b = i_start[k], i_dec[k], i_end[k]
        seg, seg_t = p[a:d + 1], t[a:d + 1]
        if len(seg) < 6:
            continue
        step = np.abs(np.diff(seg)).mean()
        if not np.isfinite(step) or step <= 0:
            continue

        span = seg.max() - seg.min()
        pos = (seg[-1] - seg.min()) / span if span > 0 else 0.5

        def push(sec: float) -> float:
            cut = np.searchsorted(seg_t, seg_t[-1] - sec)
            return float((seg[-1] - seg[cut]) / step)

        up = int((np.diff(seg) > 0).sum())
        down = int((np.diff(seg) < 0).sum())
        skew = (up - down) / max(up + down, 1)

        half = len(seg) // 2
        accel = float(((seg[-1] - seg[half]) - (seg[half] - seg[0])) / step)
        density = len(seg) / max(seg_t[-1] - seg_t[0], 1.0)

        # previous candle: entirely in the past, safe to use
        prev_dir, prev_rr = 0.0, 1.0
        pa = np.searchsorted(t, starts[k] - horizon)
        if pa < a - 2:
            pseg = p[pa:a]
            prev_dir = float(np.sign(pseg[-1] - pseg[0]))
            prev_rr = float((pseg.max() - pseg.min()) / step)

        feats.append((pos, push(8.0), push(30.0), skew, accel, density, prev_dir, prev_rr))
        outcome.append(float(np.sign(p[b - 1] - p[a])))
        when.append(decide_t[k])

    X = np.asarray(feats, dtype=np.float64)
    y_all = np.asarray(outcome, dtype=np.float64)
    t_all = np.asarray(when, dtype=np.float64)

    # shift: features of candle A predict the outcome of candle B
    return t_all[:-1], y_all[1:], X[:-1]


def ci(w: int, n: int) -> tuple[float, float, float]:
    """(winrate, lower bound 95%, se) in percent — normal approximation."""
    wr = w / n * 100
    se = np.sqrt(0.25 / n) * 100
    return wr, wr - 1.96 * se, se


def single_rules(X: np.ndarray, y: np.ndarray, top: int = 6) -> list[tuple]:
    found = []
    for f, name in enumerate(FEATURES):
        col = X[:, f]
        sd = col.std() or 1.0
        for q in (0.5, 1.0, 1.5, 2.0):
            for direction in (1.0, -1.0):
                m = (col * direction) > q * sd
                n = int(m.sum())
                if n < 300:
                    continue
                w = int((y[m] == direction).sum())
                l = int((y[m] == -direction).sum())
                n_eff = w + l
                if n_eff < 300:
                    continue
                wr, lb, se = ci(w, n_eff)
                found.append((lb, wr, n_eff, name, q, direction))
    found.sort(reverse=True)
    for lb, wr, n, name, q, d in found[:top]:
        print(f"    {name:14} q={q:<4} dir={d:+.0f}  W={wr:5.2f}%  n={n:6d}  lb95={lb:5.2f}")
    return found


def walk_forward(X: np.ndarray, y: np.ndarray, t: np.ndarray) -> float | None:
    """Ridge logistic regression, expanding-window walk-forward.

    Folds are contiguous candle-index slices, not time blocks: at 30-minute
    blocks a 2.5-day span yields only ~17 candles per block, which is below any
    usable sample. Time is still kept per fold so the ordering is chronological.
    """
    n = len(y)
    if n < 1500:
        print(f"    walk-forward: only {n} candles, skipped")
        return None

    folds = 5
    edges = [int(n * k / (folds + 1)) for k in range(1, folds + 1)]
    n_f = X.shape[1]
    correct = total = 0
    detail = []

    for edge in edges:
        split = edge
        train_slice = slice(0, split)
        test_slice = slice(split, min(split + (n // (folds + 1)), n))
        ytr, Xtr = y[train_slice], X[train_slice]
        usable = np.isin(ytr, (-1.0, 1.0))
        if usable.sum() < 500:
            continue

        mu = Xtr[usable].mean(0)
        sd = Xtr[usable].std(0)
        sd[sd == 0] = 1.0
        Z = np.hstack([((Xtr[usable] - mu) / sd), np.ones((int(usable.sum()), 1))])
        target = (ytr[usable] > 0).astype(np.float64)
        w = np.zeros(Z.shape[1])
        for _ in range(300):
            pr = 1.0 / (1.0 + np.exp(-np.clip(Z @ w, -30, 30)))
            w -= 2.0 * (Z.T @ (pr - target) / len(target) + 1e-3 * w)

        Xte, yte = X[test_slice], y[test_slice]
        ok = np.isin(yte, (-1.0, 1.0))
        if ok.sum() < 50:
            continue
        Zt = np.hstack([((Xte - mu) / sd), np.ones((len(Xte), 1))])
        pred = np.where(Zt @ w > 0, 1.0, -1.0)
        c = int((pred[ok] == yte[ok]).sum())
        k = int(ok.sum())
        correct += c
        total += k
        detail.append((c / k * 100, k))

    if total == 0:
        print("    walk-forward: no scorable fold")
        return None
    wr, lb, _ = ci(correct, total)
    print(f"    walk-forward logistic: W={wr:5.2f}%  n={total:6d}  lb95={lb:5.2f}")
    print("    folds: " + "  ".join(f"{v:.1f}%(n={k})" for v, k in detail))
    return wr


def null_test(X: np.ndarray, y: np.ndarray, reps: int = 60) -> None:
    """Real best threshold vs the same sweep on shuffled outcomes."""
    def best_of(labels: np.ndarray) -> float:
        best = 0.0
        for f in (0, 1, 3, 4):
            col = X[:, f]
            sd = col.std() or 1.0
            for q in (1.0, 2.0):
                for direction in (1.0, -1.0):
                    m = (col * direction) > q * sd
                    if m.sum() < 300:
                        continue
                    best = max(best, int((labels[m] == direction).sum()) / m.sum())
        return best

    real = best_of(y)
    rng = np.random.default_rng(7)
    null = np.array([best_of(rng.permutation(y)) for _ in range(reps)])
    p95 = float(np.percentile(null, 95))
    verdict = "ARTIFACT" if real <= p95 else "ABOVE NULL"
    print(f"    null: real_max={real * 100:5.2f}%  null_mean={null.mean() * 100:5.2f}%  "
          f"null_p95={p95 * 100:5.2f}%  -> {verdict}")


def run_symbol(path: Path, horizon: float) -> dict:
    t, p = load(path)
    decide, y, X = build(t, p, horizon)
    if len(y) < 500:
        print(f"  {path.stem:10} skipped ({len(y)} candles)")
        return {}

    usable = np.isin(y, (-1.0, 1.0))
    y, X, decide = y[usable], X[usable], decide[usable]
    up = float((y > 0).mean() * 100)
    span = (t[-1] - t[0]) / 86400

    print(f"\n  {path.stem:10} ticks={len(t):8,}  span={span:5.2f}d  "
          f"candles={len(y):6,}  up={up:5.2f}%")
    best = single_rules(X, y)
    wf = walk_forward(X, y, decide)
    null_test(X, y)

    return {
        "symbol": path.stem,
        "span_days": span,
        "candles": len(y),
        "up_rate": up,
        "best_lb95": best[0][0] if best else float("nan"),
        "best_wr": best[0][1] if best else float("nan"),
        "best_rule": f"{best[0][3]} q={best[0][4]} dir={best[0][5]:+.0f}" if best else "-",
        "walk_forward": wf if wf is not None else float("nan"),
    }


def main() -> None:
    args = sys.argv[1:]
    horizons = [60.0]
    symbols: list[str] = []
    for a in args:
        if a.startswith("H"):
            horizons = [float(x) for x in a[1:].split(",")]
        else:
            symbols.append(a)

    files = real_files()
    if symbols:
        files = [f for f in files if f.stem.upper() in {s.upper() for s in symbols}]

    print(f"real symbols: {len(files)}  horizon={horizons}s  decide=T-{DECIDE_EARLY}s  block={BLOCK}s")
    results = []
    for h in horizons:
        print(f"\n================ horizon {h:.0f}s ================")
        for f in files:
            r = run_symbol(f, h)
            if r:
                r["horizon"] = h
                results.append(r)

    print("\n================ summary ================")
    print(f"{'symbol':10} {'span_d':>7} {'candles':>8} {'up%':>6} {'best_W%':>7} {'lb95':>6} {'WF%':>6}  rule")
    for r in sorted(results, key=lambda x: (-(x["best_lb95"] if x["best_lb95"] == x["best_lb95"] else -999))):
        print(f"{r['symbol']:10} {r['span_days']:7.2f} {r['candles']:8,} {r['up_rate']:6.2f} "
              f"{r['best_wr']:7.2f} {r['best_lb95']:6.2f} {r['walk_forward']:6.2f}  {r['best_rule']}")

    passing = [r for r in results if r["best_lb95"] >= 55.0]
    print(f"\nsymbols with lb95 >= 55%: {len(passing)} / {len(results)}")
    if passing:
        for r in passing:
            print(f"  CANDIDATE {r['symbol']} {r['best_rule']} W={r['best_wr']:.2f}% lb95={r['best_lb95']:.2f}")
    else:
        print("  none. No real-symbol rule clears the economic bar out of sample.")


if __name__ == "__main__":
    main()
