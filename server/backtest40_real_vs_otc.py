"""backtest40_real_vs_otc.py - apakah candle real (EURUSD) memprediksi candle OTC (EURUSD_otc) berikutnya, dan sebaliknya."""
import numpy as np
from pathlib import Path
CACHE = Path(r"E:\otc_cache\ticks")
def mins(s):
    z = np.load(CACHE/f"{s}.npz"); t, p = z["t"], z["p"]
    m = (t//60).astype(np.int64)*60; u, st = np.unique(m, return_index=True); en = np.append(st[1:], len(t))
    return dict(zip(u.tolist(), zip(p[st], p[en-1])))
pairs = ["EURUSD","GBPUSD","USDJPY","AUDUSD","USDCAD","USDCHF","EURJPY","GBPJPY","AUDJPY","EURGBP","EURCHF","CADJPY","CHFJPY","AUDCHF","AUDCAD","CADCHF","GBPAUD","NASUSD"]
tot = {k: [0, 0] for k in ("real->otc lag0", "real->otc lag1", "otc->real lag1", "real same-min")}
for s in pairs:
    try: R, O = mins(s), mins(s+"_otc")
    except FileNotFoundError: continue
    ks = sorted(set(R) & set(O))
    for k in ks:
        if k+60 not in O or k+60 not in R: continue
        r = np.sign(R[k][1]-R[k][0]); o_next = np.sign(O[k+60][1]-O[k+60][0]); o = np.sign(O[k][1]-O[k][0]); r_next = np.sign(R[k+60][1]-R[k+60][0])
        if r and o_next: tot["real->otc lag1"][0] += int(r == o_next); tot["real->otc lag1"][1] += 1
        if r and o: tot["real->otc lag0"][0] += int(r == o); tot["real->otc lag0"][1] += 1
        if o and r_next: tot["otc->real lag1"][0] += int(o == r_next); tot["otc->real lag1"][1] += 1
        if r and r_next: tot["real same-min"][0] += int(r == r_next); tot["real same-min"][1] += 1
for k, (w, n) in tot.items(): print(f"{k:16} agree {w/n*100 if n else 0:6.2f}% n={n}")
