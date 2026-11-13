"""backtest45_regime.py - gate pada rezim: hanya trade saat 'trend strength' tinggi (ADX-like), + hasil per-simbol top kuantil dipilih di train lalu diuji. Juga uji 'expiry 1m tapi entry saat T-5' (entry = p55, exit = close B) untuk lihat efek entry lebih awal."""
import numpy as np
from pathlib import Path
from collections import defaultdict
CACHE = Path(r"E:\otc_cache\ticks"); DAY = 86400; D0 = 1789277073//DAY
def ema(a, n):
    k = 2/(n+1); o = np.empty(len(a)); o[0] = a[0]
    for i in range(1, len(a)): o[i] = k*a[i]+(1-k)*o[i-1]
    return o
tabs = {}
for f in sorted(CACHE.glob("*_otc.npz")):
    z = np.load(f); t, p = z["t"], z["p"]
    if len(t) < 5000: continue
    m = (t//60).astype(np.int64)*60; u, st = np.unique(m, return_index=True); en = np.append(st[1:], len(t))
    o, c, h, l = p[st], p[en-1], np.maximum.reduceat(p, st), np.minimum.reduceat(p, st)
    p55 = np.full(len(u), np.nan)
    for i in range(len(u)):
        off = t[st[i]:en[i]] % 60; k = off <= 55
        if k.any(): p55[i] = p[st[i]:en[i]][k][-1]
    tabs[f.stem] = dict(u=u, o=o, c=c, h=h, l=l, p55=p55, consec=np.append(np.diff(u) == 60, False))
def adx(T, n=14):
    h, l, c = T["h"], T["l"], T["c"]
    up = np.diff(h, prepend=h[0]); dn = -np.diff(l, prepend=l[0])
    pdm = np.where((up > dn) & (up > 0), up, 0); ndm = np.where((dn > up) & (dn > 0), dn, 0)
    tr = np.maximum(h-l, np.maximum(np.abs(h-np.roll(c, 1)), np.abs(l-np.roll(c, 1))))
    atr = ema(tr, n)+1e-12; pdi = 100*ema(pdm, n)/atr; ndi = 100*ema(ndm, n)/atr
    return ema(100*np.abs(pdi-ndi)/(pdi+ndi+1e-12), n), pdi, ndi
res = defaultdict(lambda: [0, 0])
for s, T in tabs.items():
    a, pdi, ndi = adx(T); u, o, c = T["u"], T["o"], T["c"]; day = u//DAY - D0
    e9, e21 = ema(c, 9), ema(c, 21)
    ynext = np.sign(np.append(c[1:], 0) - np.append(o[1:], 0)); ok = T["consec"] & (ynext != 0)
    # label alternatif: entry di T-5 (p55), exit close B
    y55 = np.sign(np.append(c[1:], 0) - T["p55"]); ok55 = ok & ~np.isnan(T["p55"]) & (y55 != 0)
    for th in (20, 30, 40, 50):
        d = np.where((a > th) & (pdi > ndi) & (e9 > e21), 1, np.where((a > th) & (ndi > pdi) & (e9 < e21), -1, 0))
        for seg, msk in (("tr", day < 6), ("ho", day == 6), ("lk", day >= 7)):
            m = msk & ok & (d != 0); r = res[(f"ADX>{th} trend-follow", seg)]; r[0] += int((d[m] == ynext[m]).sum()); r[1] += int(m.sum())
            m = msk & ok & (d != 0); r = res[(f"ADX>{th} contra", seg)]; r[0] += int((-d[m] == ynext[m]).sum()); r[1] += int(m.sum())
            m = msk & ok55 & (d != 0); r = res[(f"ADX>{th} trend entry@T-5", seg)]; r[0] += int((d[m] == y55[m]).sum()); r[1] += int(m.sum())
    # ekstrem: candle i body besar + ADX rendah (range) -> contra
    body = c - o; ab = np.abs(body); med = np.median(ab[ab > 0]) if (ab > 0).any() else 1
    for k in (2, 3):
        d = np.where((ab > k*med) & (a < 20), -np.sign(body), 0)
        for seg, msk in (("tr", day < 6), ("ho", day == 6), ("lk", day >= 7)):
            m = msk & ok & (d != 0); r = res[(f"range+bigbody{k}x contra", seg)]; r[0] += int((d[m] == ynext[m]).sum()); r[1] += int(m.sum())
names = sorted(set(k for k, _ in res))
for nm in names:
    row = [f"{seg} {res[(nm, seg)][0]/max(res[(nm, seg)][1],1)*100:5.2f}% n={res[(nm, seg)][1]:6d}" for seg in ("tr", "ho", "lk")]
    print(f"{nm:28} " + " | ".join(row))
