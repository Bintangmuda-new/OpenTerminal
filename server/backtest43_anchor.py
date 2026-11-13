"""backtest43_anchor.py - (1) OTC vs real anchor reversion, (2) drift per simbol (pilih di train, uji hold/lock), (3) jam & hari."""
import numpy as np
from pathlib import Path
from collections import defaultdict
CACHE = Path(r"E:\otc_cache\ticks"); DAY = 86400
def mins(s):
    z = np.load(CACHE/f"{s}.npz"); t, p = z["t"], z["p"]
    m = (t//60).astype(np.int64)*60; u, st = np.unique(m, return_index=True); en = np.append(st[1:], len(t))
    return u, p[st], p[en-1], en-st
print("== (1) anchor: z = (otc_close - real_close) / std, prediksi candle OTC berikut balik ke real ==")
pairs = ["EURUSD","GBPUSD","USDJPY","AUDUSD","USDCAD","USDCHF","EURJPY","GBPJPY","AUDJPY","EURGBP","EURCHF","CADJPY","CHFJPY","AUDCHF","AUDCAD","CADCHF","GBPAUD","NASUSD"]
agg = defaultdict(lambda: [0, 0])
for s in pairs:
    try: ur, orr, cr, _ = mins(s); uo, oo, co, _ = mins(s+"_otc")
    except FileNotFoundError: continue
    R = dict(zip(ur.tolist(), cr)); 
    ks = [i for i, k in enumerate(uo.tolist()) if k in R and i+1 < len(uo) and uo[i+1]-uo[i] == 60]
    if len(ks) < 100: continue
    ks = np.array(ks); dev = co[ks] - np.array([R[int(uo[i])] for i in ks])
    sd = dev.std() + 1e-12; z = dev/sd; half = len(ks)//2
    y = np.sign(co[ks+1] - oo[ks+1])
    print(f"   {s:8} n={len(ks):5d} mean|dev|/pip={np.abs(dev).mean()*1e4:6.1f} corr(dev, next)={np.corrcoef(dev[:-1], (co[ks+1]-oo[ks+1])[:-1])[0,1]:+.3f}", end="")
    for th in (1, 2):
        d = np.where(z < -th, 1, np.where(z > th, -1, 0)); m = (d != 0) & (y != 0)
        w = (d[m] == y[m]); tr = m.copy(); tr[half:] = False; ho = m.copy(); ho[:half] = False
        agg[(th, "tr")][0] += int((d[tr] == y[tr]).sum()); agg[(th, "tr")][1] += int(tr.sum())
        agg[(th, "ho")][0] += int((d[ho] == y[ho]).sum()); agg[(th, "ho")][1] += int(ho.sum())
    print()
for th in (1, 2):
    tr, ho = agg[(th, "tr")], agg[(th, "ho")]
    print(f"   |z|>{th}: train {tr[0]/max(tr[1],1)*100:6.2f}% n={tr[1]} | HOLD {ho[0]/max(ho[1],1)*100:6.2f}% n={ho[1]}")

print("\n== (2) drift per simbol: arah dominan di hari 0-5, uji hari 6 dan 7+ ==")
d0 = None; rows = []
for f in sorted(CACHE.glob("*.npz")):
    u, o, c, n = mins(f.stem)
    if len(u) < 2000: continue
    if d0 is None: d0 = 1789277073//DAY
    day = u//DAY - d0; y = np.sign(c - o); ok = (y != 0) & np.append(np.diff(u) == 60, False)
    tr = ok & (day < 6); ho = ok & (day == 6); lk = ok & (day >= 7)
    if tr.sum() < 500 or ho.sum() < 200: continue
    pu = (y[tr] > 0).mean(); d = 1 if pu > .5 else -1
    rows.append((abs(pu-.5), f.stem, pu, (y[ho] == d).mean(), ho.sum(), (y[lk] == d).mean() if lk.sum() else np.nan, lk.sum()))
rows.sort(reverse=True)
for dev, s, pu, wh, nh, wl, nl in rows[:12]: print(f"   {s:14} train P(up)={pu*100:5.1f}  -> HOLD {wh*100:5.1f}% n={nh}  LOCK {wl*100:5.1f}% n={nl}")
top = rows[:10]; print(f"   top10 gabungan HOLD = {sum(r[3]*r[4] for r in top)/sum(r[4] for r in top)*100:.2f}%  LOCK = {np.nansum([r[5]*r[6] for r in top])/sum(r[6] for r in top)*100:.2f}%")

print("\n== (3) jam UTC & hari: P(up) candle, semua OTC, train vs hold ==")
H = defaultdict(lambda: [0, 0, 0, 0])
for f in sorted(CACHE.glob("*_otc.npz")):
    u, o, c, n = mins(f.stem); day = u//DAY - 1789277073//DAY; y = np.sign(c - o); ok = y != 0
    hr = (u % DAY)//3600
    for h in range(24):
        m = ok & (hr == h); a = H[h]
        a[0] += int((y[m & (day < 6)] > 0).sum()); a[1] += int((m & (day < 6)).sum()); a[2] += int((y[m & (day >= 6)] > 0).sum()); a[3] += int((m & (day >= 6)).sum())
for h in range(24):
    a = H[h]
    if a[1]: print(f"   jam {h:02d}: train P(up)={a[0]/a[1]*100:5.2f} n={a[1]:6d} | hold+ {a[2]/max(a[3],1)*100:5.2f} n={a[3]:6d}")
