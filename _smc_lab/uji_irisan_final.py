"""Ukur irisan OTE+CHoCH (rule aplikasi) per arah, train vs holdout.
Ini menjawab: (a) apakah rule app memang terbaik, (b) apakah bug PUT-OTE
membuang edge nyata.
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

def wilson(p,n,z=1.96):
    if n==0: return 0.0
    d=1+z*z/n; c=p+z*z/(2*n); m=z*((p*(1-p)+z*z/(4*n))/n)**0.5
    return (c-m)/d

def accum(selector):
    r={"train":[0,0],"hold":[0,0],"lock":[0,0]}
    for s,T in tabs.items():
        ok=T["ok"].astype(bool); y=T["y"].astype(int); rel=T["t"]//DAY-T["t"].min()//DAY
        D=smc_canon(T,2,1.5)
        for side,dv,want in (("CALL",1,1),("PUT",-1,-1)):
            m=selector(D,side)&ok&(np.asarray(D["K1_choch_canon"]).astype(int)==want)
            for seg,msk in (("train",rel<HOLD),("hold",(rel>=HOLD)&(rel<LOCK)),("lock",rel>=LOCK)):
                mm=m&msk; n=int(mm.sum())
                if n: r[seg][0]+=int((y[mm]==want).sum()); r[seg][1]+=n
    return r

print("="*68)
print("A) RULE APLIKASI SEKARANG: OTE hanya BULLISH (CALL), PUT tanpa OTE")
print("="*68)
# CALL: OTE bullish. PUT: semua CHoCH bearish (tanpa syarat OTE) -- seperti app
def app_rule(D,side):
    if side=="CALL":
        return np.asarray(D["K10_ote"]).astype(int)==1
    return np.ones_like(np.asarray(D["K1_choch_canon"]),dtype=bool)
r=accum(app_rule)
for seg in ("train","hold","lock"):
    w,n=r[seg]
    if n: print(f"  {seg:6} n={n:7d} WR={100*w/n:5.2f}%  lb95={wilson(w/n,n)*100:5.2f}%")

print()
print("="*68)
print("B) OTE DUA ARAH (perbaikan): CALL butuh OTE, PUT juga butuh OTE")
print("="*68)
def both_rule(D,side):
    d=np.asarray(D["K10_ote"]).astype(int)
    return d==(1 if side=="CALL" else -1)
r2=accum(both_rule)
for seg in ("train","hold","lock"):
    w,n=r2[seg]
    if n: print(f"  {seg:6} n={n:7d} WR={100*w/n:5.2f}%  lb95={wilson(w/n,n)*100:5.2f}%")

print()
print("="*68)
print("C) CALL saja (OTE+CHoCH) vs PUT saja (OTE+CHoCH)")
print("="*68)
for side in ("CALL","PUT"):
    def one(D,s,side=side):
        d=np.asarray(D["K10_ote"]).astype(int)
        return d==(1 if side=="CALL" else -1)
    rr=accum(one)
    w,n=rr["train"]; wh,nh=rr["hold"]
    print(f"  {side}: train n={n:6d} WR={100*w/n:5.2f}%" if n else f"  {side}: train n=0", end="")
    if nh: print(f"   hold n={nh:5d} WR={100*wh/nh:5.2f}%")
    else: print()
