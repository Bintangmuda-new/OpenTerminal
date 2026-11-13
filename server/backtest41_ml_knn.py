"""backtest41_ml_knn.py - ML: logistic regression + KNN pattern (numpy murni), fitur 30 return terakhir + OHLC shape. Train hari 0-5, holdout 6, lockbox 7. Gate confidence: laporkan winrate per bucket probabilitas."""
import numpy as np
from pathlib import Path
CACHE = Path(r"E:\otc_cache\ticks"); DAY = 86400; L = 30
X, Y, D = [], [], []
for f in sorted(CACHE.glob("*.npz")):
    z = np.load(f); t, p = z["t"], z["p"]
    if len(t) < 5000: continue
    m = (t//60).astype(np.int64)*60; u, st = np.unique(m, return_index=True); en = np.append(st[1:], len(t))
    o, c, h, l = p[st], p[en-1], np.maximum.reduceat(p, st), np.minimum.reduceat(p, st)
    consec = np.append(np.diff(u) == 60, False)
    r = np.diff(np.log(c), prepend=np.log(c[0])); sd = np.sqrt(np.convolve(r**2, np.ones(100)/100, "same")) + 1e-12
    rn = r/sd; body = (c-o)/(h-l+1e-12); up = (h-np.maximum(o, c))/(h-l+1e-12); lo = (np.minimum(o, c)-l)/(h-l+1e-12)
    y = np.sign(np.append(c[1:], np.nan) - np.append(o[1:], np.nan))
    for i in range(L+100, len(u)-1):
        if not consec[i] or y[i] == 0 or np.isnan(y[i]): continue
        X.append(np.r_[rn[i-L+1:i+1], body[i-4:i+1], up[i-4:i+1], lo[i-4:i+1]]); Y.append(y[i]); D.append(u[i]//DAY)
X = np.array(X); Y = np.array(Y); D = np.array(D); D -= D.min()
tr, ho, lk = D < 6, D == 6, D >= 7
print("n", X.shape, "train", tr.sum(), "hold", ho.sum(), "lock", lk.sum())
# logistic regression (gradient descent)
mu, sd = X[tr].mean(0), X[tr].std(0)+1e-9; Xs = (X-mu)/sd; Xb = np.c_[Xs, np.ones(len(Xs))]
w = np.zeros(Xb.shape[1]); yt = (Y[tr] > 0).astype(float)
for it in range(300):
    pr = 1/(1+np.exp(-Xb[tr]@w)); w -= 0.5*(Xb[tr].T@(pr-yt))/tr.sum() + 1e-4*w
def report(name, prob, seg):
    pred = np.where(prob > .5, 1, -1); conf = np.abs(prob-.5)
    print(f"{name}:")
    for q in (0, .5, .8, .9, .95, .99):
        th = np.quantile(conf[tr], q); msk = seg & (conf >= th)
        n = msk.sum(); wr = (pred[msk] == Y[msk]).mean()*100 if n else 0
        print(f"   conf top {100-q*100:4.0f}%  n={n:7d}  wr={wr:6.2f}%")
prob = 1/(1+np.exp(-Xb@w))
print("train wr", ((np.where(prob[tr] > .5, 1, -1) == Y[tr]).mean()*100).round(2))
report("LOGREG holdout", prob, ho); report("LOGREG lockbox", prob, lk)
# KNN
rng = np.random.default_rng(0); ti = np.flatnonzero(tr); ti = rng.choice(ti, min(60000, len(ti)), replace=False)
A = Xs[ti]; An = (A**2).sum(1)
def knn(seg, k=50):
    si = np.flatnonzero(seg); si = rng.choice(si, min(20000, len(si)), replace=False); out = np.zeros(len(si))
    for b in range(0, len(si), 2000):
        B = Xs[si[b:b+2000]]; d2 = An[None, :] - 2*B@A.T; nn = np.argpartition(d2, k, axis=1)[:, :k]
        out[b:b+2000] = (Y[ti][nn] > 0).mean(1)
    return si, out
for nm, seg in (("holdout", ho), ("lockbox", lk)):
    si, pk = knn(seg); pred = np.where(pk > .5, 1, -1); conf = np.abs(pk-.5)
    print(f"KNN50 {nm}:")
    for th in (0, .1, .2, .3):
        m = conf >= th; n = m.sum(); print(f"   |p-.5|>={th:.1f} n={n:6d} wr={(pred[m] == Y[si][m]).mean()*100 if n else 0:6.2f}%")
