"""backtest44_micro.py - fitur mikro intra-candle (sampai T-5) + stump boosting numpy. Gate confidence. Train 0-5, hold 6, lock 7."""
import numpy as np
from pathlib import Path
CACHE = Path(r"E:\otc_cache\ticks"); DAY = 86400; D0 = 1789277073//DAY
X, Y, DD = [], [], []
for f in sorted(CACHE.glob("*_otc.npz")):
    z = np.load(f); t, p = z["t"], z["p"]
    if len(t) < 5000: continue
    m = (t//60).astype(np.int64)*60; u, st = np.unique(m, return_index=True); en = np.append(st[1:], len(t))
    o, c = p[st], p[en-1]; consec = np.append(np.diff(u) == 60, False)
    ynext = np.sign(np.append(c[1:], 0) - np.append(o[1:], 0))
    tick = np.min(np.abs(np.diff(p)[np.diff(p) != 0]))
    for i in range(50, len(u)-1):
        if not consec[i] or ynext[i] == 0: continue
        seg = p[st[i]:en[i]]; off = t[st[i]:en[i]] % 60; k = off <= 55
        if k.sum() < 10: continue
        s = seg[k]; d = np.diff(s); nz = d[d != 0]
        if len(nz) < 5: continue
        rng_ = s.max() - s.min() + tick
        # fitur
        f1 = (s[-1] - s[0]) / rng_                       # posisi net move vs range
        f2 = (s[-1] - s.min()) / rng_                    # close position in range
        f3 = (nz > 0).mean() - .5                        # tick imbalance
        f4 = np.sign(nz[-3:]).sum()/3                    # last 3 ticks dir
        f5 = (s[-1] - s[-6]) / rng_ if len(s) > 6 else 0 # last 5 ticks move
        f6 = len(nz) / k.sum()                           # activity
        f7 = (s[-1] - c[i-1]) / rng_                     # vs prev close
        f8 = np.sign(c[i-1] - o[i-1])                    # prev candle dir
        f9 = np.sign(c[i-2] - o[i-2])
        f10 = (s[-1] - s[0]) / (np.abs(nz).sum() + tick) # efficiency ratio
        f11 = np.abs(nz).max() / (np.abs(nz).mean() + 1e-12)  # max jump ratio
        f12 = ((s[-1] - s[0]) > 0) * 2 - 1
        f13 = (np.argmax(s) - np.argmin(s)) / len(s)     # order of hi/lo
        X.append([f1, f2, f3, f4, f5, f6, f7, f8, f9, f10, f11, f12, f13]); Y.append(ynext[i]); DD.append(u[i]//DAY - D0)
X = np.array(X); Y = np.array(Y); DD = np.array(DD); tr, ho, lk = DD < 6, DD == 6, DD >= 7
print("n", X.shape, "train", tr.sum(), "hold", ho.sum(), "lock", lk.sum())
names = ["netmove","closepos","imbalance","last3","last5mv","activity","vsprevclose","prev1","prev2","effratio","maxjump","sign","hilo_order"]
print("== univariat: WR arah = sign(fitur) ==")
for j, nm in enumerate(names):
    d = np.sign(X[:, j]); 
    for flip in (1, -1):
        m = d != 0; w_tr = (flip*d[tr & m] == Y[tr & m]).mean(); w_ho = (flip*d[ho & m] == Y[ho & m]).mean(); w_lk = (flip*d[lk & m] == Y[lk & m]).mean()
        if w_tr > .505: print(f"   {nm:12} x{flip:+d}: train {w_tr*100:5.2f} | hold {w_ho*100:5.2f} | lock {w_lk*100:5.2f}")
# stump boosting (AdaBoost) 200 rounds
Xt, Yt = X[tr], Y[tr]; w = np.ones(len(Yt))/len(Yt); H = []
qs = np.quantile(Xt, np.linspace(.05, .95, 19), axis=0)
for r in range(200):
    best = (1.0, None)
    for j in range(X.shape[1]):
        for th in qs[:, j]:
            for sgn in (1, -1):
                pred = np.where(Xt[:, j] > th, sgn, -sgn); err = (w*(pred != Yt)).sum()
                if err < best[0]: best = (err, (j, th, sgn))
    err, (j, th, sgn) = best; err = max(err, 1e-9); a = .5*np.log((1-err)/err)
    pred = np.where(Xt[:, j] > th, sgn, -sgn); w *= np.exp(-a*Yt*pred); w /= w.sum(); H.append((a, j, th, sgn))
F = np.zeros(len(X))
for a, j, th, sgn in H: F += a*np.where(X[:, j] > th, sgn, -sgn)
pred = np.sign(F); conf = np.abs(F)
print("== AdaBoost 200 stumps ==")
for q in (0, .5, .8, .9, .95, .99):
    th = np.quantile(conf[tr], q)
    row = []
    for nm, seg in (("train", tr), ("hold", ho), ("lock", lk)):
        m = seg & (conf >= th); row.append(f"{nm} {(pred[m] == Y[m]).mean()*100:5.2f}% n={m.sum():6d}")
    print(f"   top {100-q*100:3.0f}%: " + " | ".join(row))
