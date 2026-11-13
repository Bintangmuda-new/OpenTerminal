"""Verifikasi gate final: OTE & CHoCH searah, dua arah. Hitung sinyal per arah."""
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

res={s:{"CALL":[0,0],"PUT":[0,0],"ALL":[0,0]} for s in ("train","hold","lock")}
for s,T in tabs.items():
    ok=T["ok"].astype(bool); y=T["y"].astype(int)
    rel=T["t"]//DAY-(T["t"]//DAY).min()
    D=smc_canon(T,2,1.5)
    k1=np.asarray(D["K1_choch_canon"]).astype(int)
    ote=np.asarray(D["K10_ote"]).astype(int)
    gate = ok & (k1!=0) & (ote==k1)
    for seg,msk in (("train",rel<HOLD),("hold",(rel>=HOLD)&(rel<LOCK)),("lock",rel>=LOCK)):
        m=gate&msk; n=int(m.sum())
        if not n: continue
        for side,want in (("CALL",1),("PUT",-1)):
            mm=m&(k1==want); nn=int(mm.sum())
            if nn:
                res[seg][side][0]+=int((y[mm]==want).sum()); res[seg][side][1]+=nn
        res[seg]["ALL"][0]+=int((y[m]==k1[m]).sum()); res[seg]["ALL"][1]+=n

print("GATE FINAL = OTE & CHoCH searah, DUA ARAH")
print(f"{'seg':>7}{'arah':>7}{'n':>8}{'WR%':>8}{'lb95':>8}")
print("-"*40)
for seg in ("train","hold","lock"):
    for side in ("CALL","PUT","ALL"):
        w,n=res[seg][side]
        if n: print(f"{seg:>7}{side:>7}{n:>8}{100*w/n:>8.2f}{wilson(w/n,n):>8.2f}")
    print()
print("Sinyal SELL dihasilkan:", "YA" if res["train"]["PUT"][1]>0 else "TIDAK",
      f"({res['train']['PUT'][1]} sinyal PUT di train)")
