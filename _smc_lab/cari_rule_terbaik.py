"""Cari aturan dengan winrate tertinggi pada data besar (142 simbol).
Tujuan: buktikan apakah 70% mungkin, dan aturan mana yang paling kuat.
Simetri: CALL (dir=+1) dan PUT (dir=-1) diuji sama rata.
"""
import sys
from pathlib import Path
import numpy as np

SERVER = Path(r"E:\JANGAN DI HAPUS\OpenTerminal\server")
sys.path.insert(0, str(SERVER))
import research_harness as H
from backtest26_smc_canon import smc_canon

tabs = H.load_tables()
DAY = H.DAY
HOLD = H.HOLDOUT_FROM_DAY
LOCK = H.LOCKBOX_FROM_DAY


def wilson(p, n, z=1.96):
    if n == 0:
        return 0.0
    d = 1 + z*z/n
    c = p + z*z/(2*n)
    m = z*((p*(1-p)+z*z/(4*n))/n)**0.5
    return (c-m)/d


def run(params):
    rows = {}
    for s, T in tabs.items():
        ok = T["ok"].astype(bool); y = T["y"].astype(int)
        days = T["t"] // DAY
        rel = days - days.min()
        for p, disp in params:
            D = smc_canon(T, p, disp)
            for name, d in D.items():
                d = np.asarray(d).astype(int)
                for seg, msk in (("train", rel < HOLD), ("hold", (rel >= HOLD) & (rel < LOCK)),
                                 ("lock", rel >= LOCK)):
                    base = ok & msk & (d != 0)
                    for side, sel in (("CALL", d == 1), ("PUT", d == -1)):
                        m = base & sel
                        n = int(m.sum())
                        if n == 0:
                            continue
                        w = int((y[m] == 1).sum()) if side == "CALL" else int((y[m] == -1).sum())
                        key = (name, p, disp, side, seg)
                        a = rows.setdefault(key, [0, 0])
                        a[0] += w; a[1] += n
    return rows


params = [(p, disp) for p in (1, 2) for disp in (1.5, 2.0)]
rows = run(params)

# Gabung sisi CALL+PUT per aturan (arah mengikuti sinyal, bukan pilih-pilih)
combined = {}
for (name, p, disp, side, seg), (w, n) in rows.items():
    if seg != "hold" and seg != "train":
        continue
    k = (name, p, disp, seg)
    a = combined.setdefault(k, [0, 0])
    a[0] += w; a[1] += n

out = []
for (name, p, disp, seg), (w, n) in combined.items():
    wr = 100*w/n
    out.append((wr, wilson(w/n, n)*100, n, name, p, disp, seg))

print(f"{'WR%':>6} {'lb95':>6} {'n':>7}  {'aturan':<22} p disp seg")
print("-"*70)
for r in sorted([x for x in out if x[6]=="hold"], key=lambda x:-x[1])[:20]:
    print(f"{r[0]:6.2f} {r[1]:6.2f} {r[2]:7d}  {r[3]:<22} {r[4]} {r[5]:.1f} {r[6]}")
print("\nKEDUA ARAH (train, holdout):")
for r in sorted([x for x in out if x[6]=="train"], key=lambda x:-x[1])[:8]:
    print(f"{r[0]:6.2f} {r[1]:6.2f} {r[2]:7d}  {r[3]:<22} {r[4]} {r[5]:.1f} {r[6]}")
