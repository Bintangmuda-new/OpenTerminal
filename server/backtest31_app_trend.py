"""backtest31_app_trend.py - ukur aturan TREND yang dipakai app (decide di close candle i).

App memutuskan setelah candle i ditutup, entry = open candle i+1, hasil = close i+1.
Jalankan: python server/backtest31_app_trend.py
"""
from __future__ import annotations

import numpy as np

from research_harness import load_tables, wilson_low


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


def roll_max(x, p):
    out = np.full(len(x), np.nan)
    if len(x) >= p:
        out[p - 1:] = np.lib.stride_tricks.sliding_window_view(x, p).max(axis=1)
    return out


def roll_min(x, p):
    out = np.full(len(x), np.nan)
    if len(x) >= p:
        out[p - 1:] = np.lib.stride_tricks.sliding_window_view(x, p).min(axis=1)
    return out


def bos_up(h, l, close):
    """BOS sederhana: close menembus swing high terakhir yang terkonfirmasi."""
    n = len(h)
    hi = roll_max(h, 10)
    lo = roll_min(l, 10)
    up = (close > shift(hi)) & (shift(hi) > 0)
    dn = (close < shift(lo)) & (shift(lo) > 0)
    # jaga tren berjalan
    state = np.zeros(n, dtype=np.int8)
    cur = 0
    prev_close = shift(close)
    for i in range(n):
        if up[i]:
            cur = 1
        elif dn[i]:
            cur = -1
        state[i] = cur
        up[i] = up[i] and cur == 1
        dn[i] = dn[i] and cur == -1
    return state, up, dn


def build(T):
    o, h, l, c = T["o"], T["h"], T["l"], T["c"]
    n = len(c)
    e9, e21 = ema(c, 9), ema(c, 21)
    cross_up = np.zeros(n, dtype=bool)
    cross_dn = np.zeros(n, dtype=bool)
    cross_up[1:] = (e9[:-1] <= e21[:-1]) & (e9[1:] > e21[1:])
    cross_dn[1:] = (e9[:-1] >= e21[:-1]) & (e9[1:] < e21[1:])
    state, bu, bd = bos_up(h, l, c)
    prev_h, prev_l = shift(h), shift(l)
    body = np.abs(c - o)
    lower_wick = np.minimum(o, c) - l
    bullish = (c > o) & ((c > prev_h) | ((lower_wick > 2 * np.maximum(body, 1e-9)) & (c > o)))
    bearish = (c < o) & ((c < prev_l) | (((h - np.maximum(o, c)) > 2 * np.maximum(body, 1e-9)) & (c < o)))
    app_call = ((bu | cross_up) & (e9 > e21) & (state == 1) & bullish)
    app_put = ((bd | cross_dn) & (e9 < e21) & (state == -1) & bearish)
    return dict(app_call=app_call, app_put=app_put, cross_up=cross_up, cross_dn=cross_dn,
                bullish=bullish, bearish=bearish, stack_up=e9 > e21, stack_dn=e9 < e21,
                state=state, e9=e9, e21=e21, c=c, o=o, h=h, l=l)


def measure(tabs, pick, name):
    w = n = 0
    day_w, day_n = {}, {}
    for s, T in tabs.items():
        if "_imp" not in T:
            T["_imp"] = build(T)
        f = T["_imp"]
        ok = T["ok"]
        call = pick(f, "call") & ok
        put = pick(f, "put") & ok
        y = T["y"]
        idx = np.flatnonzero(call | put)
        if len(idx) == 0:
            continue
        d = np.where(call[idx], 1, -1)
        win = (y[idx] == d)
        w += int(win.sum()); n += len(idx)
        day = T["t"][idx] // 86400
        for dd, ww in zip(day, win):
            day_w[int(dd)] = day_w.get(int(dd), 0) + int(ww)
            day_n[int(dd)] = day_n.get(int(dd), 0) + 1
    wr = w / n * 100 if n else 0.0
    folds = [day_w[d] / day_n[d] for d in sorted(day_n) if day_n[d] >= 30]
    print(f"{name:34} {wr:6.2f}%  n={n:7d}  lb95={wilson_low(w, n):5.2f}  hari>50 {sum(f > .5 for f in folds)}/{len(folds)}")
    return wr, n


def main():
    tabs = load_tables()
    print(f"symbols={len(tabs)}  protokol: decide di close candle i, entry open i+1, hasil close i+1\n")
    measure(tabs, lambda f, s: f["app_call"] if s == "call" else f["app_put"], "APP TREND (apa adanya)")
    measure(tabs, lambda f, s: (f["cross_up"] | (f["bullish"] & f["stack_up"])) if s == "call"
            else (f["cross_dn"] | (f["bearish"] & f["stack_dn"])), "cross atau candle+stack")
    measure(tabs, lambda f, s: f["stack_up"] if s == "call" else f["stack_dn"], "stack EMA9/21 saja")
    measure(tabs, lambda f, s: f["bullish"] & f["stack_up"] if s == "call" else f["bearish"] & f["stack_dn"], "candle + stack")
    measure(tabs, lambda f, s: f["state"] == 1 if s == "call" else f["state"] == -1, "ikuti state BOS")
    print("\nCATATAN: pola bullish/bearish di sini aproksimasi CandlePatternOf app.")
    print("Agar 80% terbukti, butuh true rate ~83% pada n kecil atau ~75% pada n besar (backtest13).")


if __name__ == "__main__":
    main()
