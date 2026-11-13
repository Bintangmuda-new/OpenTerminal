"""backtest46_causal_trend.py - uji kausal penuh untuk keluarga TREND, tanpa batas T-5.

Aturan wajib (dari audit independen + validate-data):
  * Fitur hanya dari data <= waktu keputusan. Waktu keputusan = menit i detik 10/25/40/55.
  * Entry = tick pertama dengan t >= waktu_keputusan + 0.5 s. Tidak ada lookahead.
  * Exit = tick terakhir dengan t <= entry + H (H = 15/60/300 s).
  * Satu posisi per simbol pada satu waktu (sinyal saat posisi terbuka dilewati).
  * Tie (exit == entry) TIDAK dihitung sebagai kalah: dikeluarkan dari penyebut win rate.
  * Ambang |z| dipilih dari TRAIN (hari 0-5) saja, lalu dilaporkan apa adanya di
    HOLDOUT (hari 6) dan LOCKBOX (hari >= 7).
  * Lulus hanya bila holdout DAN lockbox >= 200 trade dan win rate keduanya >= 80%.

Jalankan: python server/backtest46_causal_trend.py
"""
from __future__ import annotations

import numpy as np
from pathlib import Path

CACHE = Path(r"E:\otc_cache\ticks")
DAY = 86400
WARMUP = 60          # menit minimum sebelum sinyal
DELAY = 0.5          # jeda order setelah keputusan (detik)
OFFSETS = (10, 25, 40, 55)
DURATIONS = (15, 60, 300)
Z_THRESHOLDS = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0)
MIN_TRADES = 200

def ema(x: np.ndarray, n: int) -> np.ndarray:
    a = 2.0 / (n + 1.0)
    out = np.empty(len(x), dtype=float)
    acc = x[0]
    for i, v in enumerate(x):
        acc = acc + a * (v - acc)
        out[i] = acc
    return out

def roll_mean(x: np.ndarray, n: int) -> np.ndarray:
    c = np.cumsum(np.insert(x, 0, 0.0))
    out = np.full(len(x), np.nan)
    out[n - 1:] = (c[n:] - c[:-n]) / n
    return out

def minutes(t: np.ndarray, p: np.ndarray) -> dict:
    m = (t // 60).astype(np.int64) * 60
    u, st = np.unique(m, return_index=True)
    en = np.append(st[1:], len(t))
    return dict(m=u, o=p[st], h=np.maximum.reduceat(p, st), l=np.minimum.reduceat(p, st),
                c=p[en - 1], consec=np.append(np.diff(u) == 60, False))

def plan(T: dict, t: np.ndarray, p: np.ndarray, off: int, H: int):
    """Kembalikan (menit_indeks, entry_idx, exit_idx) yang terurut dan tidak tumpang tindih."""
    i = np.flatnonzero(T["consec"][WARMUP:]) + WARMUP
    if len(i) == 0:
        return np.empty(0, int), np.empty(0, int), np.empty(0, int)
    dec = T["m"][i] + off                                   # waktu keputusan (detik)
    j = np.searchsorted(t, dec, side="right") - 1           # tick terakhir <= keputusan
    ok = (j >= 0) & (t[np.maximum(j, 0)] >= T["m"][i])      # tick ada di menit i
    k = np.searchsorted(t, dec + DELAY, side="left")        # entry: tick pertama setelah jeda
    ok &= k < len(t) - 1
    j, k = j[ok], k[ok]
    if len(k) == 0:
        return np.empty(0, int), np.empty(0, int), np.empty(0, int)
    e = np.searchsorted(t, t[k] + H, side="right") - 1      # exit: tick terakhir <= entry+H
    keep = e > k
    j, k, e = j[keep], k[keep], e[keep]
    i = i[ok][keep]
    # tanpa lookahead: harga keputusan tidak boleh setelah entry, exit tidak sebelum entry
    assert np.all(t[j] <= dec[ok][keep] + 1e-9)
    assert np.all(t[k] >= t[j])
    assert np.all(e >= k)
    # satu posisi per simbol: buang sinyal yang mulai sebelum exit sebelumnya
    sel, last = [], -1
    for a, b in zip(k, e):
        if a > last:
            sel.append(a)
            last = b
    sel = np.array(sel, dtype=int)
    if len(sel) == 0:
        return np.empty(0, int), np.empty(0, int), np.empty(0, int)
    pos = np.searchsorted(k, sel)
    return i[pos], k[pos], e[pos]

def signals(T: dict, t: np.ndarray, p: np.ndarray, off: int, z: np.ndarray, kind: str):
    c, h, l = T["c"], T["h"], T["l"]
    e9, e21, e50 = ema(c, 9), ema(c, 21), ema(c, 50)
    atr = np.nan_to_num(roll_mean(h - l, 14))
    dev = np.nan_to_num(roll_mean(np.abs(np.diff(c, prepend=c[0])), 20))
    po = np.roll(e9, 1); p1 = np.roll(e21, 1); p2 = np.roll(e50, 1)
    st_up = (po > p1) & (p1 > p2)
    st_dn = (po < p1) & (p1 < p2)
    hh = np.full(len(c), np.nan); ll = np.full(len(c), np.nan)
    if len(c) > 20:
        hh[20:] = np.lib.stride_tricks.sliding_window_view(h, 20).max(axis=1)[:-0]
        ll[20:] = np.lib.stride_tricks.sliding_window_view(l, 20).min(axis=1)
    dec = T["m"] + off
    j = np.searchsorted(t, dec, side="right") - 1
    pn = np.where(j >= 0, p[np.maximum(j, 0)], np.nan)
    d = np.zeros(len(c), dtype=np.int8)
    if kind == "pullback":
        d = np.where(st_up & (z < -z), 1, np.where(st_dn & (z > z), -1, 0)).astype(np.int8)
    elif kind == "follow":
        d = np.where(st_up, 1, np.where(st_dn, -1, 0)).astype(np.int8)
    elif kind == "breakout":
        d = np.where((pn > hh) & st_up, 1, np.where((pn < ll) & st_dn, -1, 0)).astype(np.int8)
    return d, pn

def run(kind: str, off: int, H: int, zth: float | None):
    agg = {}
    for f in sorted(CACHE.glob("*.npz")):
        z = np.load(f)
        t, p = z["t"], z["p"]
        if len(t) < 50_000:
            continue
        T = minutes(t, p)
        if len(T["m"]) < WARMUP + 50:
            continue
        c = T["c"]
        e9, e21, e50 = ema(c, 9), ema(c, 21), ema(c, 50)
        dev = np.nan_to_num(roll_mean(np.abs(np.diff(c, prepend=c[0])), 20))
        po, p1, p2 = np.roll(e9, 1), np.roll(e21, 1), np.roll(e50, 1)
        st_up = (po > p1) & (p1 > p2)
        st_dn = (po < p1) & (p1 < p2)
        dec = T["m"] + off
        j = np.searchsorted(t, dec, side="right") - 1
        pn = np.where(j >= 0, p[np.maximum(j, 0)], np.nan)
        zi = np.where(dev > 0, (pn - po) / np.where(dev > 0, dev, 1), 0.0)
        n_min = len(c)
        hh = np.full(n_min, -np.inf); ll = np.full(n_min, np.inf)
        if n_min > 20:
            # window berakhir di i-1 (tanpa lookahead): maksimum 20 menit sebelum i
            hh[20:] = np.lib.stride_tricks.sliding_window_view(T["h"], 20).max(axis=1)[: n_min - 20]
            ll[20:] = np.lib.stride_tricks.sliding_window_view(T["l"], 20).min(axis=1)[: n_min - 20]
        if kind == "pullback":
            d = np.where(st_up & (zi < -(zth or 1.0)), 1, np.where(st_dn & (zi > (zth or 1.0)), -1, 0)).astype(np.int8)
        elif kind == "follow":
            d = np.where(st_up, 1, np.where(st_dn, -1, 0)).astype(np.int8)
        else:
            d = np.where((pn > hh) & st_up, 1, np.where((pn < ll) & st_dn, -1, 0)).astype(np.int8)
        idx, k, e = plan(T, t, p, off, H)
        if len(k) == 0:
            continue
        sig = d[idx]
        msk = sig != 0
        if not msk.any():
            continue
        k2, e2, s2 = k[msk], e[msk], sig[msk]
        delta = p[e2] - p[k2]
        res = np.sign(delta).astype(np.int8)
        day = (t[k2] // DAY).astype(int)
        for seg, m in (("train", None), ("holdout", None), ("lockbox", None)):
            pass
        agg.setdefault("raw", []).append((day, s2, res, len(t)))
    # gabung
    if not agg.get("raw"):
        return None
    days = np.concatenate([a[0] for a in agg["raw"]])
    d0 = days.min()
    seg_days = days - d0
    sg = np.concatenate([a[1] for a in agg["raw"]])
    rs = np.concatenate([a[2] for a in agg["raw"]])
    out = {}
    for name, m in (("train", seg_days < 6), ("holdout", seg_days == 6), ("lockbox", seg_days >= 7)):
        s, r = sg[m], rs[m]
        w = int(((r == s) & (s != 0)).sum())
        l = int(((r == -s) & (s != 0)).sum())
        tie = int(((r == 0) & (s != 0)).sum())
        n = w + l
        out[name] = dict(w=w, l=l, n=n, tie=tie, wr=(w / n * 100 if n else 0.0))
    return out

def wilson(w: int, n: int, z: float = 1.96) -> float:
    if n == 0:
        return 0.0
    p = w / n
    d = 1 + z * z / n
    return ((p + z * z / (2 * n)) - z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)) / d * 100

def main():
    print("Protokol: keputusan dalam menit, entry +0.5 s setelah keputusan, tie dikeluarkan dari WR.")
    print("Split: train hari 0-5, holdout hari 6, lockbox hari >= 7. Lulus: WR >= 80% dan n >= 200 di dua segmen.\n")
    print(f"{'aturan':34} {'off':>4} {'H':>5} {'TRAIN wr':>9} {'n':>7} | {'HOLD wr':>8} {'n':>6} {'lb95':>6} | {'LOCK wr':>8} {'n':>6} | putusan")
    for kind, th in (("follow", None), ("breakout", None)) + tuple(("pullback", z) for z in Z_THRESHOLDS):
        for off in OFFSETS:
            for H in DURATIONS:
                if kind != "pullback" and (off not in (25, 55) or H not in (60, 300)):
                    continue
                r = run(kind, off, H, th)
                if r is None:
                    continue
                tr, ho, lk = r["train"], r["holdout"], r["lockbox"]
                if tr["n"] < 50:
                    continue
                lb = wilson(ho["w"], ho["n"])
                verdict = "LULUS 80%" if (ho["wr"] >= 80 and lk["wr"] >= 80 and ho["n"] >= MIN_TRADES and lk["n"] >= MIN_TRADES) else "-"
                nm = kind if th is None else f"{kind} |z|>{th}"
                print(f"{nm:34} {off:>4} {H:>5} {tr['wr']:8.2f}% {tr['n']:>7} | {ho['wr']:7.2f}% {ho['n']:>6} {lb:6.2f} | {lk['wr']:7.2f}% {lk['n']:>6} | {verdict}")

if __name__ == "__main__":
    main()


