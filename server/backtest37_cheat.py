"""backtest37_cheat.py - cari 80% TANPA T-5 (full candle info boleh dipakai)."""
from __future__ import annotations
import numpy as np
from pathlib import Path
from collections import defaultdict

CACHE = Path(r"E:\otc_cache\ticks")
DAY = 86400
HOLDOUT_DAY = 6
MIN_TICKS = 6

def load_all():
    tabs = {}
    for f in sorted(CACHE.glob("*.npz")):
        z = np.load(f)
        t, p = z["t"], z["p"]
        if len(t) < 1000:
            continue
        m = (t // 60).astype(np.int64) * 60
        uniq, start = np.unique(m, return_index=True)
        end = np.append(start[1:], len(t))
        o, c = p[start], p[end - 1]
        h = np.maximum.reduceat(p, start)
        l = np.minimum.reduceat(p, start)
        n = end - start
        entry = np.append(o[1:], np.nan)
        closeb = np.append(c[1:], np.nan)
        nb = np.append(n[1:], 0)
        consec = np.append(np.diff(uniq) == 60, False)
        ok = consec & (nb >= MIN_TICKS)
        y = np.sign(np.nan_to_num(closeb - entry)).astype(np.int8)
        tabs[f.stem] = dict(t=uniq, o=o, h=h, l=l, c=c, n=n, y=y, ok=ok)
    return tabs

def ema(arr, span):
    a = 2 / (span + 1)
    out = np.empty_like(arr, dtype=float)
    out[0] = arr[0]
    for i in range(1, len(arr)):
        out[i] = a * arr[i] + (1 - a) * out[i-1]
    return out

def _day0(tabs):
    return min(int(T["t"][0]) // DAY for T in tabs.values())

def evaluate(name, signal_fn, tabs, quiet=False):
    d0 = _day0(tabs)
    agg = defaultdict(lambda: [0, 0])
    for s, T in tabs.items():
        d = np.atleast_1d(signal_fn(T))
        d = np.where(T["ok"], d, 0).astype(int)
        idx = np.flatnonzero(d != 0)
        if len(idx) == 0:
            continue
        win = (T["y"][idx] == d[idx])
        day = T["t"][idx] // DAY - d0
        for dd, w in zip(day, win):
            agg[int(dd)][0] += int(w)
            agg[int(dd)][1] += 1
    seg = {"train": [0, 0], "holdout": [0, 0], "lockbox": [0, 0]}
    for dd, (w, n) in agg.items():
        if dd < HOLDOUT_DAY:
            seg["train"][0] += w; seg["train"][1] += n
        elif dd == HOLDOUT_DAY:
            seg["holdout"][0] += w; seg["holdout"][1] += n
        else:
            seg["lockbox"][0] += w; seg["lockbox"][1] += n
    if not quiet:
        tr, ho, lk = seg["train"], seg["holdout"], seg["lockbox"]
        print(f"{name:40} train {tr[0]/tr[1]*100 if tr[1] else 0:6.2f}% n={tr[1]:6d} | "
              f"HOLD {ho[0]/ho[1]*100 if ho[1] else 0:6.2f}% n={ho[1]:6d} | "
              f"LOCK {lk[0]/lk[1]*100 if lk[1] else 0:6.2f}% n={lk[1]:6d}")
    return seg

def main():
    print("Load data..."); tabs = load_all(); print(f"Loaded {len(tabs)} symbols")
    print("\n== BASELINE ==")
    evaluate("always CALL", lambda T: np.ones(len(T["t"]), dtype=int), tabs)
    
    print("\n== EMA STACK (full candle) ==")
    def ema_stack(T):
        c = T["c"]
        e9, e21, e50 = ema(c, 9), ema(c, 21), ema(c, 50)
        up = (e9 > e21) & (e21 > e50)
        dn = (e9 < e21) & (e21 < e50)
        return np.where(up, 1, np.where(dn, -1, 0))
    evaluate("EMA 9/21/50 stack", ema_stack, tabs)
    
    print("\n== STREAK REVERSAL ==")
    def streak_rev(T):
        bull = (T["c"] > T["o"]).astype(int)
        sig = np.zeros(len(T), dtype=int)
        cnt = 0
        for i in range(len(T)):
            cnt = cnt + 1 if bull[i] else cnt - 1
            sig[i] = -1 if cnt >= 3 else (1 if cnt <= -3 else 0)
        return sig
    evaluate("streak 3 reversal", streak_rev, tabs)
    
    print("\n== STRONG MOMENTUM ==")
    def strong_mom(T):
        body = np.abs(T["c"] - T["o"])
        avg = np.convolve(body, np.ones(20)/20, mode="same")
        strong = body > 2 * avg
        bull = T["c"] > T["o"]
        return np.where(strong & bull, 1, np.where(strong & ~bull, -1, 0))
    evaluate("strong momentum 2x", strong_mom, tabs)
    
    print("\n== INSIDE BAR ==")
    def inside_bar(T):
        h, l = T["h"], T["l"]
        inside = (h[1:] <= h[:-1]) & (l[1:] >= l[:-1])
        sig = np.zeros(len(T), dtype=int)
        for i in range(1, len(T)-1):
            if inside[i-1]:
                sig[i] = 1 if T["c"][i] > h[i-1] else (-1 if T["c"][i] < l[i-1] else 0)
        return sig
    evaluate("inside bar breakout", inside_bar, tabs)
    
    print("\n== S/R BOUNCE ==")
    def sr_bounce(T):
        sig = np.zeros(len(T), dtype=int)
        for i in range(20, len(T)):
            sup = T["l"][i-20:i].min()
            res = T["h"][i-20:i].max()
            if T["c"][i] <= sup * 1.001: sig[i] = 1
            elif T["c"][i] >= res * 0.999: sig[i] = -1
        return sig
    evaluate("S/R bounce 20", sr_bounce, tabs)
    
    print("\n== RSI EXTREME ==")
    def rsi_extreme(T, span=14, lo=30, hi=70):
        c = T["c"]; delta = np.diff(c, prepend=c[0])
        gain = np.where(delta > 0, delta, 0)
        loss = np.where(delta < 0, -delta, 0)
        avg_g = ema(gain, span); avg_l = ema(loss, span)
        rs = np.where(avg_l > 0, avg_g / avg_l, 0)
        rsi = 100 - 100 / (1 + rs)
        return np.where(rsi < lo, 1, np.where(rsi > hi, -1, 0))
    evaluate("RSI 14 30/70", rsi_extreme, tabs)
    
    print("\n== MACD CROSS ==")
    def macd_cross(T):
        c = T["c"]
        e12, e26 = ema(c, 12), ema(c, 26)
        macd = e12 - e26
        sig_line = ema(macd, 9)
        return np.where(macd > sig_line, 1, np.where(macd < sig_line, -1, 0))
    evaluate("MACD 12/26/9", macd_cross, tabs)
    
    print("\n== BOLLINGER REVERSION ==")
    def boll_rev(T, span=20, mult=2.0):
        c = T["c"]
        ma = ema(c, span)
        std = np.zeros_like(c)
        for i in range(span, len(c)):
            std[i] = np.std(c[i-span:i])
        upper, lower = ma + mult * std, ma - mult * std
        return np.where(c < lower, 1, np.where(c > upper, -1, 0))
    evaluate("Boll 20 2x", boll_rev, tabs)
    
    print("\n== BREAKOUT HIGH/LOW 20 ==")
    def breakout(T, look=20):
        high = np.zeros(len(T), dtype=float)
        low = np.zeros(len(T), dtype=float)
        for i in range(look, len(T)):
            high[i] = T["h"][i-look:i].max()
            low[i] = T["l"][i-look:i].min()
        return np.where(T["c"] > high, 1, np.where(T["c"] < low, -1, 0))
    evaluate("breakout 20", breakout, tabs)
    
    print("\n== DONE ==")

if __name__ == "__main__":
    main()
