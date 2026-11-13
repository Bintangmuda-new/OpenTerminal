"""Ukur GRADE A/B/C persis seperti gate aplikasi (directional, per segmen)."""
import sys
from pathlib import Path
import numpy as np
SERVER=Path(r"E:\JANGAN DI HAPUS\OpenTerminal\server"); sys.path.insert(0,str(SERVER))
import research_harness as H
from backtest26_smc_canon import smc_canon
tabs=H.load_tables(); DAY=H.DAY; HOLD=H.HOLDOUT_FROM_DAY; LOCK=H.LOCKBOX_FROM_DAY

def wilson(p,n,z=1.96):
    if n==0: return 0
    d=1+z*z/n; c=p+z*z/(2*n); m=z*((p*(1-p)+z*z/(4*n))/n)**0.5
    return (c-m)/d*100

acc={g:{s:[0,0] for s in ("train","hold","lock")} for g in ("A","B","C")}
for s,T in tabs.items():
    ok=T["ok"].astype(bool); y=T["y"].astype(int)
    rel=T["t"]//DAY-(T["t"]//DAY).min()
    D=smc_canon(T,2,1.5)
    k1=np.asarray(D["K1_choch_canon"]).astype(int)
    ote=np.asarray(D["K10_ote"]).astype(int)
    fvg=np.asarray(D["K8_fvg_canon"]).astype(int)
    ob=np.asarray(D["K7_ob_body"]).astype(int)
    # grade hanya pada bar yang OTE searah CHoCH (sama seperti app inOte)
    layered = ok & (k1!=0) & (ote==k1)
    gA = layered & (fvg==k1) & (ob==k1)
    gB = layered & (fvg==k1) & ~(ob==k1)
    gC = layered & (fvg!=k1)
    for g,m0 in (("A",gA),("B",gB),("C",gC)):
        for seg,msk in (("train",rel<HOLD),("hold",(rel>=HOLD)&(rel<LOCK)),("lock",rel>=LOCK)):
            m=m0&msk; n=int(m.sum())
            if n: acc[g][seg][0]+=int((y[m]==k1[m]).sum()); acc[g][seg][1]+=n

print(f"{'grade':>6}{'seg':>7}{'n':>8}{'WR%':>8}{'lb95':>8}")
print("-"*40)
for g in ("A","B","C"):
    for seg in ("train","hold","lock"):
        w,n=acc[g][seg]
        if n: print(f"{g:>6}{seg:>7}{n:>8}{100*w/n:>8.2f}{wilson(w/n,n):>8.2f}")
    print()
