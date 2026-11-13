"""probe_rng.py - cari cacat RNG generator OTC: pola k-gram tick, periodisitas, korelasi lintas simbol pada timestamp sama, kondisional pada ukuran step."""
import numpy as np
from pathlib import Path
CACHE = Path(r"E:\otc_cache\ticks")
out = []
def P(*a): 
    s = " ".join(str(x) for x in a); print(s); out.append(s)
S = ["EURUSD_otc", "GBPUSD_otc", "USDJPY_otc", "AUDUSD_otc", "#AAPL_otc", "#MSFT_otc", "BTCUSD_otc", "XAUUSD_otc", "EURGBP_otc", "USDCAD_otc"]
data = {s: np.load(CACHE/f"{s}.npz") for s in S}
# 1) k-gram: P(next up | last k signs), pakai 2 half (train/hold)
P("== k-gram tanda tick (train | hold), hanya tick != 0 ==")
for k in (3, 4, 5, 6):
    best = []
    for s in S:
        p = data[s]["p"]; d = np.sign(np.diff(p)); d = d[d != 0]; h = len(d)//2
        code = np.zeros(len(d)-k, dtype=np.int64)
        for j in range(k): code |= ((d[j:len(d)-k+j] > 0).astype(np.int64) << j)
        nxt = d[k:] > 0
        for seg, sl in (("tr", slice(0, h)), ("ho", slice(h, None))):
            pass
        tr = slice(0, h); ho = slice(h, None)
        for c in range(2**k):
            m = code[tr] == c; n = m.sum()
            if n < 2000: continue
            pu = nxt[tr][m].mean()
            m2 = code[ho] == c; pu2 = nxt[ho][m2].mean() if m2.sum() else .5
            best.append((abs(pu-.5), s, c, n, pu, pu2, m2.sum()))
    best.sort(reverse=True)
    P(f"k={k}: top deviasi train -> hold")
    for dev, s, c, n, pu, pu2, n2 in best[:4]:
        P(f"   {s:12} pattern={c:0{k}b} n_tr={n:6d} P(up)_tr={pu*100:5.1f} P(up)_ho={pu2*100:5.1f} n_ho={n2}")
# 2) kondisional ukuran step: setelah step besar (|step|>=q90), P(reversal) 
P("\n== P(tick berikut berlawanan | |step| >= q) per kuantil, hold half ==")
for s in S[:6]:
    p = data[s]["p"]; dp = np.diff(p); nz = dp != 0; dpz = dp[nz]; h = len(dpz)//2
    row = []
    for q in (.5, .9, .99):
        th = np.quantile(np.abs(dpz[:h]), q); m = np.abs(dpz[h:-1]) >= th
        rev = (np.sign(dpz[h:-1][m]) != np.sign(dpz[h+1:][m])).mean()
        row.append(f"q{int(q*100)}:{rev*100:5.1f}%(n={m.sum()})")
    P(f"   {s:12}", *row)
# 3) korelasi lintas simbol pada bucket 1 detik
P("\n== korelasi return 1s lintas simbol (sinkron timestamp) ==")
def sec_ret(s):
    z = data[s]; t = z["t"].astype(np.int64); p = z["p"]
    u, idx = np.unique(t, return_index=True); return dict(zip(u.tolist(), np.diff(np.log(p[idx]), prepend=0)))
R = {s: sec_ret(s) for s in S[:6]}
for i in range(6):
    for j in range(i+1, 6):
        a, b = R[S[i]], R[S[j]]; ks = sorted(set(a) & set(b))
        if len(ks) < 1000: continue
        x = np.array([a[k] for k in ks]); y = np.array([b[k] for k in ks]); m = (x != 0) & (y != 0)
        P(f"   {S[i]:12} {S[j]:12} corr={np.corrcoef(x[m], y[m])[0,1]:+.3f} n={m.sum()}")
# 4) periodisitas: FFT pada tanda tick EURUSD_otc
P("\n== FFT tanda tick (puncak teratas, periode dalam tick) ==")
for s in S[:3]:
    d = np.sign(np.diff(data[s]["p"])); d = d[d != 0][:2**18].astype(float); d -= d.mean()
    f = np.abs(np.fft.rfft(d))**2; f[0] = 0; top = np.argsort(-f)[:5]
    P(f"   {s:12} peak/median power = {(f[top]/np.median(f)).round(1)} periode = {(len(d)/top).round(1)}")
Path("server/_probe_rng.log").write_text("\n".join(out), encoding="utf-8")
