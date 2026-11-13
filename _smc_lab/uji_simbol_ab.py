"""Apakah simbol yang dipakai live (A vs B) berbeda kualitasnya?
Ini menguji: apakah live kalah karena simbolnya, atau karena bug."""
import sys
from pathlib import Path
import numpy as np
SERVER=Path(r"E:\JANGAN DI HAPUS\OpenTerminal\server"); sys.path.insert(0,str(SERVER))
import research_harness as H
from backtest26_smc_canon import smc_canon
tabs=H.load_tables()

# Simbol yang muncul di jurnal live
LIVE_A=["#AAPL_otc","#AXP_otc","#BA_otc","#CSCO_otc","#FB_otc","#INTC_otc","#JNJ_otc","#JPM_otc","#MCD_otc"]
LIVE_B=["EURUSD_otc","USDJPY_otc","USDEGP_otc","OMRCNY_otc","USDIDR_otc","D30EUR_otc","AUDCAD_otc"]

def wilson(p,n,z=1.96):
    if n==0: return 0
    d=1+z*z/n; c=p+z*z/(2*n); m=z*((p*(1-p)+z*z/(4*n))/n)**0.5
    return (c-m)/d*100

def group(name, symbols, direction=None):
    w=n=0
    for s in symbols:
        T=tabs.get(s)
        if T is None: continue
        ok=T["ok"].astype(bool); y=T["y"].astype(int)
        k1=np.asarray(smc_canon(T,2,1.5)["K1_choch_canon"]).astype(int)
        ote=np.asarray(smc_canon(T,2,1.5)["K10_ote"]).astype(int)
        gate=ok&(k1!=0)&(ote==k1)
        if direction: gate&= (k1==direction)
        nn=int(gate.sum())
        if nn: w+=int((y[gate]==k1[gate]).sum()); n+=nn
    return name,w,n

print(f"{'kelompok':<28}{'n':>8}{'WR%':>8}{'lb95':>8}")
print("-"*54)
for nm,sy in (("Saham (live CALL)",LIVE_A),("Forex (live)",LIVE_B)):
    for d,lbl in ((1,"CALL"),(-1,"PUT"),(None,"kedua")):
        nm2,w,n=group(nm+" "+lbl,sy,d)
        if n: print(f"{nm2:<28}{n:>8}{100*w/n:>8.2f}{wilson(w/n,n):>8.2f}")

# semua simbol saham vs forex
stok=[s for s in tabs if not any(k in s for k in ("USD","EUR","JPY","GBP","AUD","CAD","CHF","NZD","CNY","IDR","EGP","OMR","D30","DOW","BTC","ETH"))]
fx=[s for s in tabs if s not in stok]
print()
for nm,sy in (("SEMUA saham",stok),("SEMUA forex/idx",fx)):
    w,n=0,0
    for s in sy:
        T=tabs[s]; ok=T["ok"].astype(bool); y=T["y"].astype(int)
        k1=np.asarray(smc_canon(T,2,1.5)["K1_choch_canon"]).astype(int)
        ote=np.asarray(smc_canon(T,2,1.5)["K10_ote"]).astype(int)
        g=ok&(k1!=0)&(ote==k1); nn=int(g.sum())
        if nn: w+=int((y[g]==k1[g]).sum()); n+=nn
    print(f"{nm:<28}{n:>8}{100*w/n:>8.2f}{wilson(w/n,n):>8.2f}" if n else f"{nm:<28} n=0")
