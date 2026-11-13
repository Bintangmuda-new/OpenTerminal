"""Cari kombinasi filter yang memaksimalkan WR, dengan syarat n cukup."""
import sys, itertools
from pathlib import Path
import numpy as np
SERVER = Path(r"E:\JANGAN DI HAPUS\OpenTerminal\server")
sys.path.insert(0, str(SERVER))
import research_harness as H
from backtest26_smc_canon import smc_canon

tabs = H.load_tables(); DAY=H.DAY; HOLD=H.HOLDOUT_FROM_DAY; LOCK=H.LOCKBOX_FROM_DAY
def wilson(p,n,z=1.96):
    if n==0: return 0.0
    d=1+z*z/n; c=p+z*z/(2*n); m=z*((p*(1-p)+z*z/(4*n))/n)**0.5
    return (c-m)/d

names=["K1_choch_canon","K10_ote","K9_sweep_reclaim3","K2_choch_disp","K8_fvg_canon",
       "K7_ob_body","K15_seq_htf_choch","K18_seq_htf_ote","K12_htf_h1","K13_seq_sweep_choch"]
combos=[]
for r in range(1,4):
    combos += list(itertools.combinations(names, r))

print(f"{'WR%':>6} {'lb95':>6} {'n_train':>8} {'n_hold':>7} {'WR_hold':>8}  kombinasi")
print("-"*100)
results=[]
for combo in combos:
    tr=[0,0]; ho=[0,0]
    for s,T in tabs.items():
        ok=T["ok"].astype(bool); y=T["y"].astype(int); rel=T["t"]//DAY-T["t"].min()//DAY
        D=smc_canon(T,2,1.5)
        base=ok.copy()
        for cname in combo:
            base &= np.asarray(D[cname]).astype(int)!=0
        sign=np.asarray(D[combo[0]]).astype(int)
        for seg,msk,acc in (("train",rel<HOLD,tr),("hold",(rel>=HOLD)&(rel<LOCK),ho)):
            m=base&msk; n=int(m.sum())
            if n==0: continue
            acc[0]+=int((y[m]==sign[m]).sum()); acc[1]+=n
    if tr[1]>=3000 and ho[1]>=200:
        wr=100*tr[0]/tr[1]; wh=100*ho[0]/ho[1]
        results.append((wilson(tr[0]/tr[1],tr[1])*100, wr, tr[1], ho[1], wh, combo))
for r in sorted(results, reverse=True)[:18]:
    print(f"{r[1]:6.2f} {r[0]:6.2f} {r[2]:8d} {r[3]:7d} {r[4]:8.2f}  {'+'.join(x.replace('_',' ')[2:] for x in r[5])}")
print(f"\nTotal kombinasi diuji: {len(combos)}, lolos syarat n: {len(results)}")
