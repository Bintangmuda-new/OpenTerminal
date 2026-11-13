"""backtest29_trend.py - keluarga aturan TREND diuji dengan protokol research_harness.

Keputusan di T-5 candle i (hanya data sampai offset <= 55 s menit i + menit < i),
entry = tick pertama menit i+1, hasil = close menit i+h (h = 1,2,3,5).
Split: train hari 0-5, holdout hari 6, lockbox hari 7 (dibuka hanya bila dipanggil
dengan argumen `lockbox`).

Jalankan: python server/backtest29_trend.py [lockbox]
"""
from __future__ import annotations

import sys

import numpy as np

from research_harness import DAY, _day_index, load_tables, wilson_low

HORIZONS = (1, 2, 3, 5)


def shift(x: np.ndarray, k: int = 1) -> np.ndarray:
    out = np.full(len(x), np.nan)
    out[k:] = x[:-k]
    return out


def ema(x: np.ndarray, p: int) -> np.ndarray:
    a = 2.0 / (p + 1.0)
    out = np.empty(len(x), dtype=float)
    acc = np.nan
    for i, v in enumerate(x):
        acc = v if np.isnan(acc) else acc + a * (v - acc)
        out[i] = acc
    return out


def roll_mean(x: np.ndarray, p: int) -> np.ndarray:
    c = np.cumsum(np.insert(np.nan_to_num(x), 0, 0.0))
    out = np.full(len(x), np.nan)
    out[p - 1:] = (c[p:] - c[:-p]) / p
    return out


def roll_max(x: np.ndarray, p: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) >= p:
        out[p - 1:] = np.lib.stride_tricks.sliding_window_view(x, p).max(axis=1)
    return out


def roll_min(x: np.ndarray, p: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) >= p:
        out[p - 1:] = np.lib.stride_tricks.sliding_window_view(x, p).min(axis=1)
    return out


def feats(T: dict) -> dict:
    if "_tf" in T:
        return T["_tf"]
    c, h, l, o = T["c"], T["h"], T["l"], T["o"]
    c5, h5, l5, o5 = T["c5"], T["h5"], T["l5"], T["o5"]
    cprev = shift(c)
    e9, e21, e50 = ema(c, 9), ema(c, 21), ema(c, 50)
    a9, a2, a5 = shift(e9), shift(e21), shift(e50)
    # satu langkah EMA memakai harga T-5 -> "sekarang" tanpa lookahead
    cur9 = 0.2 * c5 + 0.8 * a9
    cur21 = 2 / 22 * c5 + (1 - 2 / 22) * a2
    cur50 = 2 / 51 * c5 + (1 - 2 / 51) * a5
    rng = shift(h - l)
    atr = roll_mean(rng, 14)
    dev = shift(roll_mean(np.abs(np.diff(c, prepend=c[0])), 20))
    # +DM / -DM (Wilder lite) pada menit tertutup
    up = shift(np.where(h - shift(h) > shift(l) - l, np.maximum(h - shift(h), 0), 0.0))
    dn = shift(np.where(shift(l) - l > h - shift(h), np.maximum(shift(l) - l, 0), 0.0))
    dmp, dmm = roll_mean(up, 14), roll_mean(dn, 14)
    f = {
        "c5": c5, "o5": o5, "h5": h5, "l5": l5, "atr": atr,
        "e9": cur9, "e21": cur21, "e50": cur50,
        "slope50": np.where(atr > 0, (cur50 - shift(e50, 6)) / np.where(atr > 0, atr, 1), 0.0),
        "z": np.where(dev > 0, (c5 - (roll_mean(cprev, 19) * 19 + c5) / 20) / np.where(dev > 0, dev, 1), 0.0),
        "hh20": shift(roll_max(h, 20)), "ll20": shift(roll_min(l, 20)),
        "hh10": shift(roll_max(h, 10)), "ll10": shift(roll_min(l, 10)),
        "dmi": np.where((dmp + dmm) > 0, (dmp - dmm) / np.where((dmp + dmm) > 0, dmp + dmm, 1), 0.0),
        "rng5": h5 - l5,
        "prev_dir": shift(np.sign(c - o)),
    }
    f["ret3"] = np.where(atr > 0, (c5 - shift(c, 3)) / np.where(atr > 0, atr, 1), 0.0)
    f["ret10"] = np.where(atr > 0, (c5 - shift(c, 10)) / np.where(atr > 0, atr, 1), 0.0)
    f["volhi"] = f["rng5"] > np.nan_to_num(atr)
    f["stack_up"] = (cur9 > cur21) & (cur21 > cur50)
    f["stack_dn"] = (cur9 < cur21) & (cur21 < cur50)
    T["_tf"] = f
    return f


def sig(x: np.ndarray) -> np.ndarray:
    return np.sign(np.nan_to_num(x))


def rules() -> dict:
    R = {}
    # 1. alignment EMA 9/21/50
    R["ema stack 9/21/50"] = lambda T: np.where(feats(T)["stack_up"], 1, np.where(feats(T)["stack_dn"], -1, 0))
    R["ema9/21"] = lambda T: sig(feats(T)["e9"] - feats(T)["e21"])
    # 2. stack + slope searah
    R["stack + slope50"] = lambda T: np.where(feats(T)["stack_up"] & (feats(T)["slope50"] > 0.2), 1,
                                              np.where(feats(T)["stack_dn"] & (feats(T)["slope50"] < -0.2), -1, 0))
    # 3. DMI konsisten
    R["dmi(14) follow"] = lambda T: np.where(feats(T)["dmi"] > 0.25, 1, np.where(feats(T)["dmi"] < -0.25, -1, 0))
    R["dmi + stack"] = lambda T: np.where(feats(T)["stack_up"] & (feats(T)["dmi"] > 0.2), 1,
                                          np.where(feats(T)["stack_dn"] & (feats(T)["dmi"] < -0.2), -1, 0))
    # 4. Donchian breakout 10/20
    for p in (10, 20):
        R[f"donchian{p} break"] = lambda T, p=p: np.where(feats(T)["c5"] > feats(T)[f"hh{p}"], 1,
                                                          np.where(feats(T)["c5"] < feats(T)[f"ll{p}"], -1, 0))
        R[f"donchian{p} break +stack"] = lambda T, p=p: np.where(
            (feats(T)["c5"] > feats(T)[f"hh{p}"]) & feats(T)["stack_up"], 1,
            np.where((feats(T)["c5"] < feats(T)[f"ll{p}"]) & feats(T)["stack_dn"], -1, 0))
    # 5. momentum biasa
    for k in (3, 10):
        R[f"ret{k} follow"] = lambda T, k=k: sig(feats(T)[f"ret{k}"])
        R[f"ret{k} follow +stack"] = lambda T, k=k: np.where(
            (feats(T)[f"ret{k}"] > 0.2) & feats(T)["stack_up"], 1,
            np.where((feats(T)[f"ret{k}"] < -0.2) & feats(T)["stack_dn"], -1, 0))
    # 6. pullback resume dalam tren
    def pullback(T):
        f = feats(T)
        touch_up = f["c5"] < f["e9"] * 1.0005
        touch_dn = f["c5"] > f["e9"] * 0.9995
        return np.where(f["stack_up"] & touch_up, 1, np.where(f["stack_dn"] & touch_dn, -1, 0))
    R["pullback resume"] = pullback
    # 7. tren + z-score moderat (hindari ekstensi)
    R["stack + |z|<1.5"] = lambda T: np.where(feats(T)["stack_up"] & (np.abs(feats(T)["z"]) < 1.5), 1,
                                              np.where(feats(T)["stack_dn"] & (np.abs(feats(T)["z"]) < 1.5), -1, 0))
    R["stack + volhi"] = lambda T: np.where(feats(T)["stack_up"] & feats(T)["volhi"], 1,
                                            np.where(feats(T)["stack_dn"] & feats(T)["volhi"], -1, 0))
    R["stack + slope + volhi"] = lambda T: np.where(
        feats(T)["stack_up"] & feats(T)["volhi"] & (feats(T)["slope50"] > 0.2), 1,
        np.where(feats(T)["stack_dn"] & feats(T)["volhi"] & (feats(T)["slope50"] < -0.2), -1, 0))
    R["kontrol always CALL"] = lambda T: np.ones(len(T["t"]))
    return R


def labels(tabs: dict, h: int) -> None:
    """y_h = tanda(close menit i+h - entry menit i+1); ok_h = menit berurutan."""
    for T in tabs.values():
        c, o, t = T["c"], T["o"], T["t"]
        n = len(t)
        entry = np.append(o[1:], np.nan)          # open menit i+1
        close = np.append(c[1 + h - 1:], np.full(h, np.nan))  # close menit i+h
        ok = np.zeros(n, dtype=bool)
        if n > h:
            d = np.diff(t)
            ok[:-h] = (np.lib.stride_tricks.sliding_window_view(d, h) == 60).all(axis=1)
        T[f"y{h}"] = np.sign(np.nan_to_num(close - entry)).astype(np.int8)
        T[f"ok{h}"] = ok & (T["n5"] >= 6)


def evaluate_h(name: str, rule, tabs: dict, h: int, cooldown: int = 0, quiet: bool = False) -> dict:
    d0 = _day_index(tabs)
    agg = {"train": [0, 0, 0], "holdout": [0, 0, 0], "lockbox": [0, 0, 0]}
    folds = {}
    sym_hold = []
    for s, T in tabs.items():
        d = np.asarray(rule(T), dtype=float)
        d = np.where(T[f"ok{h}"], d, 0.0)
        if cooldown:
            last = -10 ** 9
            for i in np.flatnonzero(d):
                if i - last <= cooldown:
                    d[i] = 0
                else:
                    last = i
        idx = np.flatnonzero(d != 0)
        if len(idx) == 0:
            continue
        y = T[f"y{h}"][idx].astype(float)
        win = (y == d[idx])
        draw = (y == 0)
        day = T["t"][idx] // DAY - d0
        lock = day >= 7
        hold = (day == 6)
        for seg, msk in (("train", day < 6), ("holdout", hold), ("lockbox", lock)):
            agg[seg][0] += int(win[msk].sum())
            agg[seg][2] += int(draw[msk].sum())
            agg[seg][1] += int((~win[msk] & ~draw[msk]).sum())
        for dd, w in zip(day[hold], win[hold]):
            folds[int(dd)] = folds.get(int(dd), [0, 0])
            folds[int(dd)][0] += int(w)
            folds[int(dd)][1] += 1
        nh = int(hold.sum())
        if nh >= 20:
            sym_hold.append(bool(win[hold].mean() > 0.5))
    res = {"name": name, "h": h}
    for seg, (w, l, dr) in agg.items():
        n = w + l + dr
        res[seg] = dict(n=n, wr=(w / n * 100 if n else 0.0), lb=wilson_low(w, n), draw=dr)
    res["sym_pos"] = f"{sum(sym_hold)}/{len(sym_hold)}"
    if not quiet:
        tr, ho = res["train"], res["holdout"]
        print(f"h={h} {name:26} train {tr['wr']:6.2f}% n={tr['n']:7d} | HOLD {ho['wr']:6.2f}% "
              f"n={ho['n']:7d} lb95={ho['lb']:5.2f} draw={ho['draw']:5d} sym+ {res['sym_pos']}", flush=True)
    return res


def main() -> None:
    open_lockbox = len(sys.argv) > 1 and sys.argv[1] == "lockbox"
    tabs = load_tables()
    print(f"symbols={len(tabs)} minutes={sum(len(T['t']) for T in tabs.values()):,}")
    for h in HORIZONS:
        for T in tabs.values():
            if f"y{h}" not in T:
                labels({"_": T}, h)
    R = rules()
    out = []
    for h in HORIZONS:
        for name, rule in R.items():
            out.append(evaluate_h(name, rule, tabs, h, quiet=False))
    print("\n== TOP-10 holdout lb95 (h=1) ==")
    best = sorted([r for r in out if r["h"] == 1], key=lambda r: -r["holdout"]["lb"])[:10]
    for r in best:
        print(f"  {r['name']:26} {r['holdout']['wr']:6.2f}% lb={r['holdout']['lb']:5.2f} n={r['holdout']['n']}")
    if open_lockbox:
        tr = [r for r in out if r["h"] == 1 and r["train"]["wr"] >= 51 and r["train"]["n"] >= 3000
              and not r["name"].startswith("kontrol")]
        tr.sort(key=lambda r: -r["holdout"]["lb"])
        print("\n== LOCKBOX (hari 7) untuk kandidat lolos train ==")
        for r in tr:
            lb = r["lockbox"]
            print(f"  {r['name']:26} LOCK {lb['wr']:6.2f}% n={lb['n']:7d} lb95={lb['lb']:5.2f}")


if __name__ == "__main__":
    main()
