"""backtest36_trend_vs_pullback.py - adu adil h=1 (expiry 1m app): aturan TREND app vs pullback resume.

Timing identik: decide di close candle i, entry open i+1, hasil close i+1.
Segmen train hari 0-5, holdout hari 6, lockbox hari 7. Plus null test 20x pada holdout.
Jalankan: python server/backtest36_trend_vs_pullback.py
"""
from __future__ import annotations

import numpy as np

import backtest29_trend as B29
import backtest31_app_trend as APP
from research_harness import DAY, _day_index, load_tables, wilson_low

H = 1


def pullback(T, z):
    f = B29.feats(T)
    d = np.where(f["stack_up"] & (f["z"] < -z), 1, np.where(f["stack_dn"] & (f["z"] > z), -1, 0))
    return np.where(T[f"ok{H}"], d, 0)


def evaluate(tabs, dget, label):
    d0 = _day_index(tabs)
    agg = {"train": [0, 0], "holdout": [0, 0], "lockbox": [0, 0]}
    per_day = {}
    for s, T in tabs.items():
        d = np.asarray(dget(T), dtype=float)
        idx = np.flatnonzero(d != 0)
        if len(idx) == 0:
            continue
        win = (T[f"y{H}"][idx] == d[idx])
        day = T["t"][idx] // DAY - d0
        for seg, msk in (("train", day < 6), ("holdout", day == 6), ("lockbox", day >= 7)):
            agg[seg][0] += int(win[msk].sum()); agg[seg][1] += int(msk.sum())
        for dd, ww in zip(day, win):
            per_day.setdefault(int(dd), [0, 0]); per_day[int(dd)][0] += int(ww); per_day[int(dd)][1] += 1
    tr, ho, lk = [agg[k] for k in ("train", "holdout", "lockbox")]
    folds = [v[0] / v[1] for v in per_day.values() if v[1] >= 30]
    print(f"{label:28} train {tr[0] / tr[1] * 100:6.2f}% n={tr[1]:7d} | HOLD {ho[0] / ho[1] * 100:6.2f}% "
          f"n={ho[1]:7d} lb95={wilson_low(*ho):5.2f} | LOCK {lk[0] / lk[1] * 100:6.2f}% n={lk[1]:7d} "
          f"| hari>50 {sum(f > .5 for f in folds)}/{len(folds)}")
    return agg


def main():
    tabs = load_tables()
    for T in tabs.values():
        if f"y{H}" not in T:
            B29.labels({"_": T}, H)
    def app_rule(T):
        if "_imp" not in T:
            T["_imp"] = APP.build(T)
        f = T["_imp"]
        d = np.where(f["app_call"], 1, np.where(f["app_put"], -1, 0))
        return np.where(T[f"ok{H}"], d, 0)
    print("== expiry 1m, aturan app vs kandidat pullback ==")
    for z in (0.5, 0.75, 1.0, 1.25, 1.5):
        evaluate(tabs, lambda T, z=z: pullback(T, z), f"pullback resume z={z}")
    evaluate(tabs, app_rule, "APP TREND (sekarang)")
    evaluate(tabs, lambda T: np.where(B29.feats(T)["stack_up"], 1, np.where(B29.feats(T)["stack_dn"], -1, 0)) * T[f"ok{H}"], "stack 9/21/50")
    print("\n== null test holdout, 20x (label acak), pullback z=1.0 ==")
    rng = np.random.default_rng(23)
    real_w = real_n = 0
    nulls = []
    d0 = _day_index(tabs)
    for i in range(20):
        w = n = 0
        for T in tabs.values():
            d = pullback(T, 1.0)
            idx = np.flatnonzero(d != 0)
            if len(idx) == 0:
                continue
            day = T["t"][idx] // DAY - d0
            m = day == 6
            if i == 0:
                real_w += int((T[f"y{H}"][idx][m] == d[idx][m]).sum()); real_n += int(m.sum())
            if not m.any():
                continue
            y = T[f"y{H}"][idx].copy(); rng.shuffle(y)
            w += int((y[m] == d[idx][m]).sum()); n += int(m.sum())
        if i or True:
            nulls.append(w / n * 100 if n else 0.0)
    a = np.array(nulls)
    real = real_w / real_n * 100
    print(f"  nyata {real:.2f}%  null mean={a.mean():.2f}% p95={np.percentile(a, 95):.2f}% max={a.max():.2f}%")
    print(f"  -> {'MENONJOL' if real > np.percentile(a, 95) else 'tidak menonjol'}")
    print(f"\n  breakeven payout 80% = 55,6%. Jarak {55.6 - real:+.2f} poin.")


if __name__ == "__main__":
    main()
