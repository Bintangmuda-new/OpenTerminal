"""backtest42_sequence.py - win rate PER-SESI (bukan per-trade) dengan step recovery, dari urutan hasil nyata (pullback z=1.0, holdout+lockbox). Payout parameter. Laporkan: WR sesi, P(bangkrut), drawdown."""
import numpy as np
import backtest29_trend as B29
from research_harness import load_tables, DAY, _day_index
tabs = load_tables(); d0 = _day_index(tabs)
for T in tabs.values():
    if "y1" not in T: B29.labels({"_": T}, 1)
seq = []
for s, T in tabs.items():
    f = B29.feats(T); d = np.where(f["stack_up"] & (f["z"] < -1.0), 1, np.where(f["stack_dn"] & (f["z"] > 1.0), -1, 0)); d = np.where(T["ok1"], d, 0)
    idx = np.flatnonzero(d); day = T["t"][idx]//DAY - d0; m = day >= 6
    for t_, w in zip(T["t"][idx][m], (T["y1"][idx][m] == d[idx][m])): seq.append((t_, s, bool(w)))
seq.sort(); W = np.array([w for _, _, w in seq]); print(f"trade nyata (holdout+lockbox): n={len(W)} WR per-trade={W.mean()*100:.2f}%")
rng = np.random.default_rng(1)
for payout in (0.80, 0.92):
    for steps in (2, 3, 4, 5):
        mult = 1/payout + 1  # stake agar 1 win menutup semua loss sebelumnya + profit 1 unit awal
        sess_w = sess_n = 0; ruin = 0; maxdd = 0; bal = 0.0; peak = 0.0; i = 0
        while i < len(W):
            stake = 1.0; lost = 0.0; k = 0
            while k < steps and i < len(W):
                if W[i]: bal += stake*payout; sess_w += 1; i += 1; break
                lost += stake; bal -= stake; i += 1; k += 1; stake = lost*mult
            else:
                pass
            sess_n += 1; peak = max(peak, bal); maxdd = max(maxdd, peak - bal)
        need = sum((mult)**j for j in range(steps))
        print(f"payout {payout:.2f} steps={steps}: WR sesi={sess_w/sess_n*100:6.2f}% (sesi={sess_n}) | PnL akhir={bal:+.0f} unit | maxDD={maxdd:.0f} | modal minimum ~{need:.0f} unit | P(1 sesi gagal)={(1-W.mean())**steps*100:.2f}%")
