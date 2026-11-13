"""backtest30_trend_opt.py - cari aturan TREND terbaik di protokol T-5.

Ide dari repo SYAPOCKET (confluence 70%): HTF M5/M15, liquidity sweep EQH/EQL,
clearance ke S/R >= 0.4 ATR, struktur swing HH/HL. Ide yang sudah terbukti netral
(skor 16 faktor, EMA+RSI) tidak diulang.

Protokol research_harness: keputusan T-5 candle i (tick <= 55 s + menit < i),
entry = tick pertama menit i+1, hasil = close menit i+1.
Segmen: train hari 0-5, holdout hari 6, lockbox hari 7.
Jalankan: python server/backtest30_trend_opt.py
"""
from __future__ import annotations

import sys
from collections import defaultdict

import numpy as np

from research_harness import DAY, _day_index, load_tables, wilson_low


def shift(x, k=1):
    out = np.full(len(x), np.nan)
    out[k:] = x[:-k]
    return out


def ema(x, p):
    a = 2.0 / (p + 1.0)
    out = np.empty(len(x), dtype=float)
    acc = np.nan
    for i in range(len(x)):
        v = x[i]
        if np.isnan(v):
            out[i] = np.nan
            continue
        acc = v if np.isnan(acc) else acc + a * (v - acc)
        out[i] = acc
    return out


def roll_mean(x, p):
    c = np.cumsum(np.insert(np.nan_to_num(x), 0, 0.0))
    out = np.full(len(x), np.nan)
    out[p - 1:] = (c[p:] - c[:-p]) / p
    return out


def swings(h, l, k=2):
    """Fractal: pivot baru terpakai k menit setelah puncaknya (tanpa lookahead)."""
    n = len(h)
    hi_idx, hi_px, lo_idx, lo_px = [], [], [], []
    for i in range(k, n - k):
        wh = h[i - k:i + k + 1]
        wl = l[i - k:i + k + 1]
        if h[i] == wh.max() and (wh == h[i]).sum() == 1:
            hi_idx.append(i + k); hi_px.append(float(h[i]))
        if l[i] == wl.min() and (wl == l[i]).sum() == 1:
            lo_idx.append(i + k); lo_px.append(float(l[i]))
    return np.array(hi_idx, dtype=int), np.array(hi_px), np.array(lo_idx, dtype=int), np.array(lo_px)


def mtf(t, c, sec):
    """Tren TF tinggi dari bucket yang SUDAH tertutup sebelum menit berjalan."""
    n = len(t)
    b = (t // sec) * sec
    uniq, start = np.unique(b, return_index=True)
    end = np.append(start[1:], n)
    bclose = c[end - 1]
    fast, slow = ema(bclose, 9), ema(bclose, 21)
    pos = np.searchsorted(uniq, b) - 1
    up = np.zeros(n, dtype=bool)
    dn = np.zeros(n, dtype=bool)
    ok = pos >= 22
    p = np.clip(pos, 0, max(len(uniq) - 1, 0))
    up[ok] = (fast[p[ok]] > slow[p[ok]]) & (bclose[p[ok]] > fast[p[ok]])
    dn[ok] = (fast[p[ok]] < slow[p[ok]]) & (bclose[p[ok]] < fast[p[ok]])
    return up, dn


def features(T):
    if "_of" in T:
        return T["_of"]
    t, c, h, l = T["t"], T["c"], T["h"], T["l"]
    c5, h5, l5 = T["c5"], T["h5"], T["l5"]
    cprev = shift(c)
    e9, e21, e50 = ema(c, 9), ema(c, 21), ema(c, 50)
    cur9 = 0.2 * c5 + 0.8 * shift(e9)
    cur21 = 2 / 22 * c5 + (1 - 2 / 22) * shift(e21)
    cur50 = 2 / 51 * c5 + (1 - 2 / 51) * shift(e50)
    atr = roll_mean(shift(h - l), 14)
    dev = shift(roll_mean(np.abs(np.diff(c, prepend=c[0])), 20))
    sma20 = (roll_mean(cprev, 19) * 19 + c5) / 20
    up5, dn5 = mtf(t, c, 300)
    up15, dn15 = mtf(t, c, 900)
    hi_idx, hi_px, lo_idx, lo_px = swings(h, l)
    n = len(t)
    last_hi = np.full(n, np.nan); prev_hi = np.full(n, np.nan)
    last_lo = np.full(n, np.nan); prev_lo = np.full(n, np.nan)
    hi_ptr = lo_ptr = 0
    for i in range(n):
        while hi_ptr < len(hi_idx) and hi_idx[hi_ptr] <= i - 1:
            hi_ptr += 1
        if hi_ptr > 0:
            last_hi[i] = hi_px[hi_ptr - 1]
            prev_hi[i] = hi_px[hi_ptr - 2] if hi_ptr >= 2 else np.nan
        while lo_ptr < len(lo_idx) and lo_idx[lo_ptr] <= i - 1:
            lo_ptr += 1
        if lo_ptr > 0:
            last_lo[i] = lo_px[lo_ptr - 1]
            prev_lo[i] = lo_px[lo_ptr - 2] if lo_ptr >= 2 else np.nan
    atrs = np.where(atr > 0, atr, np.nan)
    f = {
        "c5": c5, "atr": atr,
        "z": np.where(dev > 0, (c5 - sma20) / np.where(dev > 0, dev, 1), 0.0),
        "e9": cur9, "e21": cur21, "e50": cur50,
        "up5": up5, "dn5": dn5, "up15": up15, "dn15": dn15,
        "stack_up": (cur9 > cur21) & (cur21 > cur50),
        "stack_dn": (cur9 < cur21) & (cur21 < cur50),
        "hh": (last_hi > prev_hi) & (last_lo > prev_lo),
        "ll": (last_lo < prev_lo) & (last_hi < prev_hi),
        "last_hi": last_hi, "last_lo": last_lo,
        "dist_r": (last_hi - c5) / atrs, "dist_s": (c5 - last_lo) / atrs,
        "rng5": h5 - l5, "h5": h5, "l5": l5,
    }
    f["volhi"] = f["rng5"] > np.nan_to_num(atr)
    tol = 0.1 * np.nan_to_num(atr)
    f["sweep_low"] = (~np.isnan(f["last_lo"])) & (l5 < f["last_lo"] - tol) & (c5 > f["last_lo"])
    f["sweep_high"] = (~np.isnan(f["last_hi"])) & (h5 > f["last_hi"] + tol) & (c5 < f["last_hi"])
    f["clear_up"] = f["dist_r"] >= 0.4
    f["clear_dn"] = f["dist_s"] >= 0.4
    T["_of"] = f
    return f


def rules():
    F = features
    R = {}
    R["m1 stack"] = lambda T: np.where(F(T)["stack_up"], 1, np.where(F(T)["stack_dn"], -1, 0))
    R["HTF M5+M15"] = lambda T: np.where(F(T)["up5"] & F(T)["up15"], 1,
                                         np.where(F(T)["dn5"] & F(T)["dn15"], -1, 0))
    R["M5+M15+M1"] = lambda T: np.where(F(T)["up5"] & F(T)["up15"] & F(T)["stack_up"], 1,
                                        np.where(F(T)["dn5"] & F(T)["dn15"] & F(T)["stack_dn"], -1, 0))
    R["M5+M15+M1+z<1.5"] = lambda T: np.where(
        F(T)["up5"] & F(T)["up15"] & F(T)["stack_up"] & (np.abs(F(T)["z"]) < 1.5), 1,
        np.where(F(T)["dn5"] & F(T)["dn15"] & F(T)["stack_dn"] & (np.abs(F(T)["z"]) < 1.5), -1, 0))
    R["M5+M1+HH/HL"] = lambda T: np.where(F(T)["up5"] & F(T)["hh"] & F(T)["stack_up"], 1,
                                          np.where(F(T)["dn5"] & F(T)["ll"] & F(T)["stack_dn"], -1, 0))
    R["M5+M15+clear0.4"] = lambda T: np.where(
        F(T)["up5"] & F(T)["up15"] & F(T)["clear_up"] & F(T)["stack_up"], 1,
        np.where(F(T)["dn5"] & F(T)["dn15"] & F(T)["clear_dn"] & F(T)["stack_dn"], -1, 0))
    R["sweep+HTF"] = lambda T: np.where(
        F(T)["sweep_low"] & (F(T)["up5"] | F(T)["up15"]), 1,
        np.where(F(T)["sweep_high"] & (F(T)["dn5"] | F(T)["dn15"]), -1, 0))
    R["sweep fade"] = lambda T: np.where(F(T)["sweep_low"], 1, np.where(F(T)["sweep_high"], -1, 0))
    R["full gate"] = lambda T: np.where(
        F(T)["up5"] & F(T)["up15"] & F(T)["stack_up"] & F(T)["clear_up"] & F(T)["hh"], 1,
        np.where(F(T)["dn5"] & F(T)["dn15"] & F(T)["stack_dn"] & F(T)["clear_dn"] & F(T)["ll"], -1, 0))
    R["kontrol always CALL"] = lambda T: np.ones(len(T["t"]))
    return R


def evaluate(name, rule, tabs, cooldown=0):
    d0 = _day_index(tabs)
    agg = {"train": [0, 0], "holdout": [0, 0], "lockbox": [0, 0]}
    per_day = defaultdict(lambda: [0, 0])
    sym_hold = []
    for s, T in tabs.items():
        d = np.asarray(rule(T), dtype=float)
        d = np.where(T["ok"], d, 0.0)
        if cooldown:
            last = -10 ** 9
            for i in np.flatnonzero(d):
                if i - last <= cooldown:
                    d[i] = 0.0
                else:
                    last = i
        idx = np.flatnonzero(d != 0)
        if len(idx) == 0:
            continue
        y = T["y"][idx].astype(float)
        win = (y == d[idx])
        day = T["t"][idx] // DAY - d0
        lock = day >= 7
        hold = day == 6
        for seg, msk in (("train", day < 6), ("holdout", hold), ("lockbox", lock)):
            agg[seg][0] += int(win[msk].sum())
            agg[seg][1] += int(msk.sum())
        for dd, w in zip(day, win):
            per_day[int(dd)][0] += int(w)
            per_day[int(dd)][1] += 1
        if int(hold.sum()) >= 20:
            sym_hold.append(bool(win[hold].mean() > 0.5))
    res = {"name": name,
           "per_day": {k: (v[0] / v[1] * 100 if v[1] else 0.0, v[1]) for k, v in sorted(per_day.items())}}
    for seg, (w, n) in agg.items():
        res[seg] = dict(n=n, wr=(w / n * 100 if n else 0.0), lb=wilson_low(w, n))
    res["sym_pos"] = f"{sum(sym_hold)}/{len(sym_hold)}"
    return res


def main():
    tabs = load_tables()
    print(f"symbols={len(tabs)} minutes={sum(len(T['t']) for T in tabs.values()):,}", flush=True)
    out = []
    for name, rule in rules().items():
        r = evaluate(name, rule, tabs)
        out.append(r)
        tr, ho, lb = r["train"], r["holdout"], r["lockbox"]
        days = " ".join(f"{k}:{v[0]:.1f}({v[1]})" for k, v in r["per_day"].items())
        print(f"{name:20} train {tr['wr']:6.2f}% n={tr['n']:7d} | HOLD {ho['wr']:6.2f}% n={ho['n']:7d} "
              f"lb95={ho['lb']:5.2f} | LOCK {lb['wr']:6.2f}% n={lb['n']:7d} | sym+ {r['sym_pos']}", flush=True)
        print(f"{'':20} per hari: {days}", flush=True)
    print("\n== TOP-5 holdout lb95 ==")
    for r in sorted(out, key=lambda r: -r["holdout"]["lb"])[:5]:
        print(f"  {r['name']:20} {r['holdout']['wr']:6.2f}% lb={r['holdout']['lb']:5.2f} "
              f"n={r['holdout']['n']} lock={r['lockbox']['wr']:.2f}% (n={r['lockbox']['n']})")


if __name__ == "__main__":
    main()
