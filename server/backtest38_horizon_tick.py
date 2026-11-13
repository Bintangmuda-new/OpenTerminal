"""backtest38_horizon_tick.py - cari >=80%: autokorelasi tick + mean-reversion horizon panjang."""
import numpy as np
from pathlib import Path
from collections import defaultdict
CACHE = Path(r"E:\otc_cache\ticks"); DAY = 86400

def ema(a, span):
    k = 2/(span+1); out = np.empty(len(a)); out[0] = a[0]
    for i in range(1, len(a)): out[i] = k*a[i] + (1-k)*out[i-1]
    return out

tabs = {}
tick_ac = []
for f in sorted(CACHE.glob("*.npz")):
    z = np.load(f); t, p = z["t"], z["p"]
    if len(t) < 5000: continue
    r = np.diff(p); r = r[r != 0]
    if len(r) > 1000:
        tick_ac.append((f.stem, np.corrcoef(r[:-1], r[1:])[0, 1], (np.sign(r[:-1]) != np.sign(r[1:])).mean()))
    m = (t//60).astype(np.int64)*60
    uniq, start = np.unique(m, return_index=True)
    end = np.append(start[1:], len(t))
    tabs[f.stem] = dict(t=uniq, o=p[start], c=p[end-1], h=np.maximum.reduceat(p, start), l=np.minimum.reduceat(p, start))

a = np.array([x[1] for x in tick_ac]); rev = np.array([x[2] for x in tick_ac])
print(f"TICK lag-1 autocorr: mean={a.mean():.3f} min={a.min():.3f} max={a.max():.3f}")
print(f"TICK reversal rate (tanda tick berikut berlawanan): mean={rev.mean()*100:.2f}% max={rev.max()*100:.2f}%")
for s, ac, rv in sorted(tick_ac, key=lambda x: -x[2])[:5]: print(f"  {s:16} ac={ac:+.3f} rev={rv*100:.2f}%")

d0 = min(int(T["t"][0])//DAY for T in tabs.values())
def run(name, rule, H):
    agg = defaultdict(lambda: [0, 0])
    for s, T in tabs.items():
        c = T["t"]; n = len(c)
        if n < 300: continue
        consec = np.zeros(n, bool); consec[:-H] = (c[H:] - c[:-H]) == 60*H
        d = rule(T); d = np.where(consec, d, 0)
        idx = np.flatnonzero(d)
        if not len(idx): continue
        y = np.sign(T["c"][idx+H] - T["o"][np.minimum(idx+1, n-1)])
        win = y == d[idx]; day = T["t"][idx]//DAY - d0
        for dd, w in zip(day, win): agg[int(dd)][0] += int(w); agg[int(dd)][1] += 1
    tr = [sum(v[0] for k, v in agg.items() if k < 6), sum(v[1] for k, v in agg.items() if k < 6)]
    ho = agg.get(6, [0, 0]); lk = [sum(v[0] for k, v in agg.items() if k >= 7), sum(v[1] for k, v in agg.items() if k >= 7)]
    f = lambda x: f"{x[0]/x[1]*100 if x[1] else 0:6.2f}% n={x[1]:6d}"
    print(f"{name:34} H={H:2d} train {f(tr)} | HOLD {f(ho)} | LOCK {f(lk)}")

def zrule(span, th):
    def r(T):
        c = T["c"]; m = ema(c, span); dev = c - m
        sd = np.sqrt(ema(dev**2, span)) + 1e-12; z = dev/sd
        return np.where(z < -th, 1, np.where(z > th, -1, 0))
    return r
def stack(T):
    c = T["c"]; e9, e21, e50 = ema(c, 9), ema(c, 21), ema(c, 50)
    return np.where((e9 > e21) & (e21 > e50), 1, np.where((e9 < e21) & (e21 < e50), -1, 0))

print("\n== horizon panjang ==")
for H in (5, 15, 30, 60):
    run("EMA stack (tren)", stack, H)
    for span, th in ((200, 2.0), (200, 3.0), (500, 2.5)):
        run(f"mean-rev z EMA{span} |z|>{th}", zrule(span, th), H)
