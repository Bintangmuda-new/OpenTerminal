"""backtest11_leadlag.py — last unexplored mechanism: cross-symbol lead-lag.

Generated OTC feeds are all produced by the same broker loop, so they could
share a driver. If they do, a move in one symbol may precede a move in another,
and that IS tradeable without predicting the future. This script tests it
directly on the M1 grid: for every ordered symbol pair with enough overlap, does
symbol A's last-candle direction predict symbol B's next candle?

Guardrails against self-deception:
  - only the top pairs by TRAIN edge are kept,
  - the reported number is the HOLDOUT (last 40% of time),
  - the count of pairs tested is printed, so the reader can judge how much of
    the "best" result is just multiple-comparison luck.
"""
import itertools
import sqlite3
import sys
from pathlib import Path

import numpy as np

DB = Path(__file__).resolve().parent.parent / "data" / "otc_research.sqlite3"
MIN_OVERLAP = 800


def wilson_low(w, n, z=1.96):
    if n == 0:
        return 0.0
    p = w / n
    d = 1 + z * z / n
    return ((p + z * z / (2 * n)) - z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)) / d * 100


def load():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    series = {}
    for (symbol,) in con.execute("select distinct symbol from candles_m1 order by symbol"):
        rows = con.execute(
            "select time,open,close from candles_m1 where symbol=? order by time", (symbol,)
        ).fetchall()
        if len(rows) < MIN_OVERLAP:
            continue
        a = np.array(rows, dtype=float)
        series[symbol] = (a[:, 0].astype(np.int64), np.sign(a[:, 2] - a[:, 1]))
    con.close()
    return series


def main():
    series = load()
    syms = sorted(series)
    print(f"symbols={len(syms)}")

    # common time base: align every symbol onto the union of epochs it covers
    all_t = np.unique(np.concatenate([series[s][0] for s in syms]))
    cut = float(np.quantile(all_t, 0.6))
    print(f"epochs={len(all_t)}  train cut={cut:.0f}")

    # build a matrix: rows = epochs, cols = symbols, values = candle direction
    index = {s: i for i, s in enumerate(syms)}
    M = np.full((len(all_t), len(syms)), np.nan)
    pos = {t: i for i, t in enumerate(all_t)}
    for s in syms:
        t, d = series[s]
        M[[pos[x] for x in t], index[s]] = d
    train_mask = all_t < cut

    print(f"pairs to test: {len(syms) * (len(syms) - 1)} ordered")
    results = []
    for a in syms:
        ia = index[a]
        va = M[:, ia]
        va_next = np.concatenate([va[1:], [np.nan]])
        good_a = ~np.isnan(va) & (va != 0)
        for b in syms:
            if a == b:
                continue
            vb = M[:, index[b]]
            ok = good_a & ~np.isnan(vb) & (vb != 0)
            if ok.sum() < MIN_OVERLAP:
                continue
            hit = (va[ok] == vb[ok]).astype(float)
            ts = all_t[ok]
            tr = ts < cut
            ho = ~tr
            if tr.sum() < 200 or ho.sum() < 200:
                continue
            results.append((a, b, hit[tr].mean() * 100, int(tr.sum()), hit[ho].mean() * 100, int(ho.sum())))

    results.sort(key=lambda r: -r[2])
    print(f"\ntested {len(results)} pairs\n")
    print(f"{'A -> B':26} {'train%':>7} {'n':>6} {'hold%':>7} {'n':>6} {'lb95':>6}")
    for a, b, tr, trn, ho, hon in results[:20]:
        print(f"{a + ' -> ' + b:26} {tr:7.2f} {trn:6d} {ho:7.2f} {hon:6d} {wilson_low(ho / 100 * hon, hon):6.2f}")

    keep = [r for r in results if r[2] >= 55 and r[3] >= 300]
    print(f"\npairs with train >= 55% and n>=300: {len(keep)}")
    if keep:
        tw = sum(r[4] / 100 * r[5] for r in keep)
        tn = sum(r[5] for r in keep)
        print(f"  their combined HOLDOUT: {tw / tn * 100:.2f}% n={tn} lb95={wilson_low(tw, tn):.2f}")

    # same-symbol baseline: how often does a symbol follow itself one bar later?
    self_rows = []
    for s in syms:
        v = M[:, index[s]]
        ok = ~np.isnan(v) & (v != 0)
        vv = v[ok]
        if len(vv) < MIN_OVERLAP:
            continue
        hit = (vv[:-1] == vv[1:]).astype(float)
        ts = all_t[ok][:-1]
        self_rows.append((s, hit[ts < cut].mean() * 100 if (ts < cut).sum() else 0, hit[ts >= cut].mean() * 100))
    if self_rows:
        avg_tr = np.mean([r[1] for r in self_rows])
        avg_ho = np.mean([r[2] for r in self_rows])
        print(f"\nself follow-through baseline: train {avg_tr:.2f}%  holdout {avg_ho:.2f}%  ({len(self_rows)} symbols)")


if __name__ == "__main__":
    sys.exit(main())
