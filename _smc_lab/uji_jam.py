"""Apakah ada jam/segmen waktu dengan winrate jauh berbeda? Ini bisa
menjelaskan kenapa live (jam tertentu) jauh di bawah backtest agregat."""
import sys
from collections import defaultdict
from pathlib import Path
import numpy as np
SERVER=Path(r"E:\JANGAN DI HAPUS\OpenTerminal\server"); sys.path.insert(0,str(SERVER))
import research_harness as H
from backtest26_smc_canon import smc_canon
tabs=H.load_tables()

def wilson(p,n,z=1.96):
    if n==0: return 0
    d=1+z*z/n; c=p+z*z/(2*n); m=z*((p*(1-p)+z*z/(4*n))/n)**0.5
    return (c-m)/d*100

per_jam=defaultdict(lambda:[0,0])
for s,T in tabs.items():
    ok=T["ok"].astype(bool); y=T["y"].astype(int)
    k1=np.asarray(smc_canon(T,2,1.5)["K1_choch_canon"]).astype(int)
    ote=np.asarray(smc_canon(T,2,1.5)["K10_ote"]).astype(int)
    gate=ok&(k1!=0)&(ote==k1)
    hour=(T["t"]%86400)//3600
    for h in range(24):
        m=gate&(hour==h); n=int(m.sum())
        if n: per_jam[h][0]+=int((y[m]==k1[m]).sum()); per_jam[h][1]+=n

print(f"{'jam':>4}{'n':>8}{'WR%':>8}{'lb95':>8}")
print("-"*28)
for h in sorted(per_jam):
    w,n=per_jam[h]
    flag=" <-" if n>=300 else ""
    print(f"{h:>4}{n:>8}{100*w/n:>8.2f}{wilson(w/n,n):>8.2f}{flag}")
