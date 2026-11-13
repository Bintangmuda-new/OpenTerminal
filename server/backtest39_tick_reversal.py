"""backtest39_tick_reversal.py - fade tick besar, expiry detik (entry=tick keputusan, exit=tick terakhir <= t+H)."""
import numpy as np
from pathlib import Path
CACHE = Path(r"E:\otc_cache\ticks")
syms = ["EURGBP","AUDUSD","USDCHF","USDCAD","AUDCAD","EURUSD","GBPUSD","USDJPY","EURJPY","GBPJPY","AUDJPY","CADJPY","EURCHF","CHFJPY","AUDCHF","CADCHF","GBPAUD","GBPCAD","GBPCHF","NASUSD"]
for H in (5, 15, 30, 60):
    print(f"\n== expiry {H}s, fade tick >= k*median|dp| ==")
    tot = {}
    for s in syms:
        f = CACHE/f"{s}.npz"
        if not f.exists(): continue
        z = np.load(f); t, p = z["t"], z["p"]
        dp = np.diff(p); med = np.median(np.abs(dp[dp != 0]))
        for k in (1, 2, 3):
            idx = np.flatnonzero(np.abs(dp) >= k*med) + 1   # tick keputusan = setelah lompatan
            idx = idx[idx < len(t)-1]
            # split half: first half train, second half holdout
            half = t[0] + (t[-1]-t[0])/2
            j = np.searchsorted(t, t[idx] + H, side="right") - 1
            ok = (t[j] > t[idx]) & (t[j] <= t[idx]+H)
            idx, j = idx[ok], j[ok]
            d = -np.sign(dp[idx-1])
            y = np.sign(p[j] - p[idx])
            win = y == d; hold = t[idx] > half
            for seg, m in (("train", ~hold), ("hold", hold)):
                a = tot.setdefault((k, seg), [0, 0, 0]); a[0] += int(win[m].sum()); a[1] += int(m.sum()); a[2] += int((y[m] == 0).sum())
    for k in (1, 2, 3):
        tr, ho = tot[(k, "train")], tot[(k, "hold")]
        print(f"  k={k}: train {tr[0]/tr[1]*100:6.2f}% n={tr[1]:7d} draw={tr[2]/tr[1]*100:4.1f}% | HOLD {ho[0]/ho[1]*100:6.2f}% n={ho[1]:7d} draw={ho[2]/ho[1]*100:4.1f}%")
