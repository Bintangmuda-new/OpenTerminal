"""probe_otc_generator.py - bedah generator tick OTC: step size, autokorelasi per lag, pola detik-dalam-menit, detik akhir candle."""
import numpy as np
from pathlib import Path
CACHE = Path(r"E:\otc_cache\ticks")
for s in ("EURUSD_otc", "#AAPL_otc", "BTCUSD_otc", "XAUUSD_otc"):
    z = np.load(CACHE/f"{s}.npz"); t, p = z["t"], z["p"]
    dp = np.diff(p); dt = np.diff(t)
    tick = np.round(np.min(np.abs(dp[dp != 0])), 8)
    steps = np.round(dp/tick).astype(int)
    u, c = np.unique(steps, return_counts=True); top = np.argsort(-c)[:9]
    print(f"\n== {s}: ticks={len(t)} dt median={np.median(dt):.3f}s tick={tick}")
    print("   step dist:", {int(u[i]): round(c[i]/len(steps)*100, 1) for i in top})
    r = dp[dp != 0]
    print("   autocorr lag1..5:", [round(float(np.corrcoef(r[:-k], r[k:])[0, 1]), 3) for k in (1, 2, 3, 4, 5)])
    s_ = np.sign(dp); nz = s_ != 0
    # run length dist
    runs = np.diff(np.flatnonzero(np.diff(s_[nz]) != 0)); ru, rc = np.unique(runs, return_counts=True)
    print("   run-length P:", {int(ru[i]): round(rc[i]/rc.sum()*100, 1) for i in range(min(6, len(ru)))}, " (random walk: 50,25,12.5,...)")
    # arah tick vs detik-dalam-menit
    off = (t[1:] % 60).astype(int); up = (dp > 0)
    by = [(k, up[(off >= k) & (off < k+10) & (dp != 0)].mean()*100) for k in range(0, 60, 10)]
    print("   P(up) per 10s bucket:", [f"{k}s:{v:.1f}" for k, v in by])
    # candle: close-vs-open sign vs sign of ticks in last 5 s
    m = (t//60).astype(np.int64)*60; uq, st = np.unique(m, return_index=True); en = np.append(st[1:], len(t))
    o, c = p[st], p[en-1]
    last5 = np.zeros(len(uq)); 
    for i in range(len(uq)):
        seg = p[st[i]:en[i]]; tt = t[st[i]:en[i]] % 60; k = tt >= 55
        if k.sum() >= 2: last5[i] = seg[k][-1] - seg[k][0]
    body = c - o
    both = (last5 != 0) & (body != 0)
    print(f"   sign(last5s) == sign(body): {(np.sign(last5[both]) == np.sign(body[both])).mean()*100:.1f}%  n={both.sum()}")
    # mean reversion ke open dalam candle: P(close kembali ke sisi open setelah |p55-o| besar)
    p55 = np.array([p[st[i]:en[i]][(t[st[i]:en[i]] % 60) <= 55][-1] if ((t[st[i]:en[i]] % 60) <= 55).any() else np.nan for i in range(len(uq))])
    d55 = p55 - o; ok = ~np.isnan(d55) & (d55 != 0)
    print(f"   P(close sama sisi dgn harga T-5 vs open): {(np.sign(d55[ok]) == np.sign(body[ok])).mean()*100:.1f}%  (bukan prediksi, hanya struktur)")
    # candle berikut vs candle ini
    nb = np.sign(body[1:]); cb = np.sign(body[:-1]); k = (nb != 0) & (cb != 0)
    print(f"   P(candle i+1 searah candle i): {(nb[k] == cb[k]).mean()*100:.2f}%")
