"""Kenapa live 43,6% jauh dari backtest 54,5%?
Uji hipotesis: (1) pilihan simbol, (2) periode/waktu, (3) drift simbol per hari."""
import sys
from pathlib import Path
import numpy as np
SERVER=Path(r"E:\JANGAN DI HAPUS\OpenTerminal\server"); sys.path.insert(0,str(SERVER))
import research_harness as H
from backtest26_smc_canon import smc_canon
tabs=H.load_tables(); DAY=H.DAY

def wr_cell(sel):
    tr=[0,0]
    for s,T in tabs.items():
        ok=T["ok"].astype(bool); y=T["y"].astype(int)
        k1=np.asarray(smc_canon(T,2,1.5)["K1_choch_canon"]).astype(int)
        m=ok&(k1!=0)&sel(T,k1)
        n=int(m.sum())
        if n: tr[0]+=int((y[m]==k1[m]).sum()); tr[1]+=n
    return tr

# Hipotesis 3: drift per simbol-hari. Kalau tiap simbol punya bias arah harian,
# maka menebak arah "lawan" drift justru untung. Ini yang mungkin terjadi live.
print("=== DRIFT: apakah arah menang bisa diprediksi dari hari sebelumnya? ===")
persist=[0,0]; flip=[0,0]
for s,T in tabs.items():
    ok=T["ok"].astype(bool); y=T["y"].astype(int)
    day=T["t"]//DAY
    days=np.unique(day)
    # arah dominan hari d
    for i in range(1,len(days)):
        d0,d1=days[i-1],days[i]
        m0=ok&(day==d0); m1=ok&(day==d1)
        if m0.sum()<50 or m1.sum()<50: continue
        up0=(y[m0]==1).mean()   # proporsi naik hari sebelumnya
        up1=(y[m1]==1).mean()
        # kalau hari lalu condong naik, apakah hari ini juga naik?
        if up0>0.52:   # kemarin naik -> taruh CALL hari ini
            persist[0]+= int((y[m1]==1).sum()); persist[1]+=int(m1.sum())
        elif up0<0.48: # kemarin turun -> taruh PUT hari ini
            persist[0]+= int((y[m1]==-1).sum()); persist[1]+=int(m1.sum())
print(f"  ikuti arah hari sebelumnya : n={persist[1]:6d} WR={100*persist[0]/persist[1]:5.2f}%" if persist[1] else "  tidak cukup data")
