"""smc_lab.py — evaluasi menyeluruh aturan SMC/ICT pada cache riset.

Protokol sama dengan research_harness: keputusan T-5s, entry = open candle B,
hasil = close candle B. Train (hari 0-5) / holdout (hari 6).

Menjalankan 18 aturan kanonik x beberapa param, lalu memberi peringkat
berdasarkan lower bound 95% holdout.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

SERVER = Path(r"E:\JANGAN DI HAPUS\OpenTerminal\server")
sys.path.insert(0, str(SERVER))

import research_harness as H  # noqa: E402
from backtest26_smc_canon import smc_canon  # noqa: E402

DAY = H.DAY
HOLDOUT_FROM = H.HOLDOUT_FROM_DAY


def aggregate(tabs: dict, params: list[tuple[int, float]]) -> dict:
    """Akumulasi per (rule, param) tanpa recompute smc_canon berkali-kali."""
    store: dict[tuple[str, int, float], dict[str, list[int]]] = {}
    for pi, (p, disp) in enumerate(params):
        for s, T in tabs.items():
            dirs_map = smc_canon(T, p, disp)
            ok = T["ok"]
            y = T["y"]
            day = T["t"] // DAY - H._day_index(tabs)
            hold = day >= HOLDOUT_FROM
            tr = ~hold
            for rname, dirs in dirs_map.items():
                key = (rname, p, disp)
                slot = store.setdefault(key, {"train": [0, 0, 0], "holdout": [0, 0, 0]})
                for seg, msk in (("train", tr), ("holdout", hold)):
                    m = msk & ok & (dirs != 0)
                    if not m.any():
                        continue
                    yy = y[m].astype(float)
                    dd = dirs[m]
                    win = (yy == dd)
                    draw = (yy == 0)
                    slot[seg][0] += int(win.sum())
                    slot[seg][1] += int((~win & ~draw).sum())
                    slot[seg][2] += int(draw.sum())
        print(f"  param p={p} disp={disp} selesai ({pi + 1}/{len(params)})", flush=True)
    return store


def summarize(store: dict) -> list[dict]:
    rows = []
    for (rname, p, disp), slot in store.items():
        tr, ho = slot["train"], slot["holdout"]
        ntr = sum(tr)
        nho = sum(ho)
        rows.append(
            dict(
                rule=rname,
                p=p,
                disp=disp,
                n_train=ntr,
                wr_train=tr[0] / ntr * 100 if ntr else 0.0,
                n_hold=nho,
                wr_hold=ho[0] / nho * 100 if nho else 0.0,
                lb=H.wilson_low(ho[0], nho),
                draw=ho[2],
            )
        )
    return rows


def main() -> None:
    print("memuat cache...", flush=True)
    tabs = H.load_tables()
    total_bars = sum(len(T["t"]) for T in tabs.values())
    print(f"simbol={len(tabs)}  bar M1={total_bars:,}", flush=True)

    params = [(1, 1.5), (1, 2.0), (2, 1.5), (2, 2.0)]
    print(f"menghitung {len(params)} konfigurasi x 18 aturan...", flush=True)
    store = aggregate(tabs, params)
    rows = summarize(store)

    out = Path(__file__).with_name("hasil_aturan.csv")
    with out.open("w", encoding="utf-8") as f:
        f.write("rule,p,disp,n_train,wr_train,n_hold,wr_hold,lb95,draw\n")
        for r in sorted(rows, key=lambda r: -r["lb"]):
            f.write(
                f"{r['rule']},{r['p']},{r['disp']},{r['n_train']},{r['wr_train']:.3f},"
                f"{r['n_hold']},{r['wr_hold']:.3f},{r['lb']:.3f},{r['draw']}\n"
            )

    print("\n=== PERINGKAT: holdout (hari 6) berdasarkan lb95 ===")
    print(f"{'aturan':24} {'p':>2} {'disp':>4} {'n_hold':>8} {'WR_hold':>8} {'lb95':>7} {'WR_train':>8}")
    for r in sorted(rows, key=lambda r: -r["lb"])[:25]:
        print(
            f"{r['rule']:24} {r['p']:>2} {r['disp']:>4} {r['n_hold']:>8,} "
            f"{r['wr_hold']:>7.2f}% {r['lb']:>6.2f} {r['wr_train']:>7.2f}%"
        )

    print("\n=== Hanya aturan dengan n_hold >= 1000 ===")
    big = [r for r in rows if r["n_hold"] >= 1000]
    for r in sorted(big, key=lambda r: -r["lb"])[:15]:
        print(
            f"{r['rule']:24} p={r['p']} disp={r['disp']} n={r['n_hold']:>8,} "
            f"WR={r['wr_hold']:>6.2f}% lb95={r['lb']:>6.2f} (train {r['wr_train']:.2f}%)"
        )
    print(f"\nCSV: {out}")


if __name__ == "__main__":
    main()
