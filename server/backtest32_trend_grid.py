"""backtest32_trend_grid.py - grid besar aturan TREND, seleksi di TRAIN, vonis di HOLDOUT+lockbox.

Tujuan: menjawab permintaan "minimal 80%". Semua kombinasi gerbang tren diuji
(~200 varian), peringkat diambil dari TRAIN (hari 0-5), lalu angka HOLDOUT (hari 6)
dan LOCKBOX (hari 7) dilaporkan apa adanya. Bila tidak ada yang >= 80% di holdout,
artinya 80% hanya artefak seleksi.
Jalankan: python server/backtest32_trend_grid.py
"""
from __future__ import annotations

import itertools
from collections import defaultdict

import numpy as np

import backtest30_trend_opt as F
from research_harness import DAY, _day_index, load_tables, wilson_low


def feats(T):
    return F.features(T)


def gates(T):
    f = feats(T)
    g = {}
    g["stack_up"] = f["stack_up"]; g["stack_dn"] = f["stack_dn"]
    g["up5"] = f["up5"]; g["dn5"] = f["dn5"]
    g["up15"] = f["up15"]; g["dn15"] = f["dn15"]
    g["hh"] = f["hh"]; g["ll"] = f["ll"]
    g["clear_up"] = f["clear_up"]; g["clear_dn"] = f["clear_dn"]
    g["volhi"] = f["volhi"]
    g["sweep_low"] = f["sweep_low"]; g["sweep_high"] = f["sweep_high"]
    for z in (0.75, 1.0, 1.5, 2.0):
        g[f"z_lt{z}"] = np.abs(f["z"]) < z
        g[f"z_gt{z}"] = np.abs(f["z"]) > z
    atr = np.nan_to_num(f["atr"])
    with np.errstate(invalid="ignore", divide="ignore"):
        ext = (f["c5"] - f["e9"]) / np.where(atr > 0, atr, np.nan)
    for e in (0.25, 0.5, 1.0):
        g[f"ext_lt{e}"] = np.nan_to_num(np.abs(ext)) < e
        g[f"pull{e}_up"] = np.nan_to_num(ext) < -e
        g[f"pull{e}_dn"] = np.nan_to_num(ext) > e
    hour = (T["t"] // 3600) % 24
    g["hour_asia"] = (hour >= 0) & (hour < 8)
    g["hour_london"] = (hour >= 8) & (hour < 13)
    g["hour_ny"] = (hour >= 13) & (hour < 21)
    return g


UP_KEYS = ["stack_up", "up5", "up15", "hh", "clear_up", "volhi", "sweep_low", "z_lt0.75", "z_lt1.0",
           "z_lt1.5", "z_lt2.0", "z_gt0.75", "z_gt1.0", "z_gt1.5", "z_gt2.0", "ext_lt0.25", "ext_lt0.5",
           "ext_lt1.0", "pull0.25_up", "pull0.5_up", "pull1.0_up", "hour_asia", "hour_london", "hour_ny"]
DN_KEYS = ["stack_dn", "dn5", "dn15", "ll", "clear_dn", "volhi", "sweep_high", "z_lt0.75", "z_lt1.0",
           "z_lt1.5", "z_lt2.0", "z_gt0.75", "z_gt1.0", "z_gt1.5", "z_gt2.0", "ext_lt0.25", "ext_lt0.5",
           "ext_lt1.0", "pull0.25_dn", "pull0.5_dn", "pull1.0_dn", "hour_asia", "hour_london", "hour_ny"]


def combos(max_gates=4):
    """Kombinasi gerbang searah: selalu mulai dari stack (basis tren), lalu pilih <=3 gerbang lain."""
    outs = []
    core = "stack_up"
    rest = [k for k in UP_KEYS if k != core and not k.startswith("z_lt") and not k.startswith("ext_lt")]
    for r in range(0, max_gates):
        for extra in itertools.combinations(rest, r):
            outs.append((core,) + extra)
    return outs


def eval_combo(tabs, keys_up, keys_dn, seg):
    w = n = 0
    for s, T in tabs.items():
        g = T["_g"]
        d = np.ones(len(T["t"]), dtype=bool)
        for k in keys_up:
            d &= g[k]
        e = np.ones(len(T["t"]), dtype=bool)
        for k in keys_dn:
            e &= g[k]
        d &= T["ok"] & ~e
        e &= T["ok"]
        idx = np.flatnonzero(d | e)
        if len(idx) == 0:
            continue
        msk = seg[s][idx]
        if not msk.any():
            continue
        dirs = np.where(d[idx], 1.0, -1.0)
        win = (T["y"][idx] == dirs)
        w += int(win[msk].sum()); n += int(msk.sum())
    return w, n


def main():
    tabs = load_tables()
    for T in tabs.values():
        T["_g"] = gates(T)
    d0 = _day_index(tabs)
    seg_hold = {}
    seg_lock = {}
    for s, T in tabs.items():
        day = T["t"] // DAY - d0
        seg_hold[s] = day == 6
        seg_lock[s] = day >= 7
    up_combos = combos(4)
    print(f"symbols={len(tabs)} kombinasi={len(up_combos)}", flush=True)
    rows = []
    for i, cu in enumerate(up_combos):
        cd = tuple(k.replace("_up", "_dn") if k.endswith("_up") else k.replace("up5", "dn5").replace("up15", "dn15")
                   .replace("stack_up", "stack_dn").replace("hh", "ll").replace("clear_up", "clear_dn")
                   .replace("sweep_low", "sweep_high") for k in cu)
        tr = eval_combo(tabs, cu, cd, {s: ~seg_hold[s] & ~seg_lock[s] for s in tabs})
        if tr[1] < 500:
            continue
        rows.append((wilson_low(*tr), tr[0] / tr[1] * 100, tr[1], cu, cd))
        if (i + 1) % 500 == 0:
            print(f"  ...{i + 1}/{len(up_combos)}", flush=True)
    rows.sort(reverse=True)
    print(f"\nkombinasi lolos n_train>=500: {len(rows)}")
    print("\n== TOP-15 menurut TRAIN (lb95, wr, n) ==")
    print(f"{'gerbang':60} {'train%':>7} {'n':>8} {'hold%':>7} {'hn':>7} {'lock%':>7} {'ln':>7}")
    for lb, wr, n, cu, cd in rows[:15]:
        ho = eval_combo(tabs, cu, cd, seg_hold)
        lk = eval_combo(tabs, cu, cd, seg_lock)
        hwr = ho[0] / ho[1] * 100 if ho[1] else 0.0
        lwr = lk[0] / lk[1] * 100 if lk[1] else 0.0
        print(f"{'+'.join(cu)[:60]:60} {wr:7.2f} {n:8d} {hwr:7.2f} {ho[1]:7d} {lwr:7.2f} {lk[1]:7d}")
    print("\n== semua kombinasi dengan holdout >= 80% ==")
    over = []
    for lb, wr, n, cu, cd in rows:
        ho = eval_combo(tabs, cu, cd, seg_hold)
        if ho[1] >= 50 and ho[0] / ho[1] >= 0.8:
            over.append((ho[0] / ho[1] * 100, '+'.join(cu), ho[1], wr, n))
    if not over:
        print("  TIDAK ADA. Nol kombinasi mencapai 80% di holdout.")
    for wr, name, n, twr, tn in sorted(over, reverse=True)[:10]:
        print(f"  {wr:6.2f}% n={n:6d} train={twr:.2f}% n={tn}  {name}")
    best_ho = []
    for lb, wr, n, cu, cd in rows[:200]:
        ho = eval_combo(tabs, cu, cd, seg_hold)
        if ho[1] >= 100:
            best_ho.append((ho[0] / ho[1] * 100, '+'.join(cu), ho[1], wr))
    best_ho.sort(reverse=True)
    print("\n== holdout terbaik dari 200 kandidat teratas TRAIN ==")
    for wr, name, n, twr in best_ho[:10]:
        print(f"  {wr:6.2f}% n={n:6d} train={twr:.2f}%  {name[:70]}")


if __name__ == "__main__":
    main()
