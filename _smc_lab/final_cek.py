"""final_cek.py — dua pertanyaan terakhir sebelum implementasi.

1. Apakah grade dengan OB stabil di ketiga periode (bukan hanya gabungan)?
2. Bisakah PUT diselamatkan dengan filter HTF (H1 searah)?
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

SERVER = Path(r"E:\JANGAN DI HAPUS\OpenTerminal\server")
sys.path.insert(0, str(SERVER))

import research_harness as H  # noqa: E402

HOLDOUT_FROM = H.HOLDOUT_FROM_DAY
LOCKBOX_FROM = H.LOCKBOX_FROM_DAY


def wr(y):
    nz = y[y != 0]
    return (len(nz), float((nz > 0).mean() * 100) if len(nz) else 0.0)


def main():
    d = np.load(Path(__file__).with_name("cache_signals.npz"))
    y, day, ote = d["y"], d["day"], d["ote"]
    fvg, sweep, ob, h1 = d["fvg"], d["sweep"], d["ob"], d["h1"]
    base = d["p"] == 2
    seg = np.where(day >= LOCKBOX_FROM, "lock", np.where(day >= HOLDOUT_FROM, "hold", "train"))

    print("=" * 94)
    print("1) STABILITAS GRADE CALL — harus konsisten di train/holdout/lockbox")
    print("=" * 94)
    grades = [
        ("A  OTE+FVG+OB       ", (ote == 1) & (fvg == 1) & (ob == 1)),
        ("B  OTE+FVG (tanpa OB)", (ote == 1) & (fvg == 1) & (ob == 0)),
        ("C  OTE saja          ", (ote == 1) & (fvg == 0)),
    ]
    print(f"{'grade':22} {'n':>6} {'WR':>7} | {'WR_tr':>7} {'WR_ho':>7} {'WR_lk':>7} | {'min':>6} {'EV92':>7}")
    print("-" * 94)
    for name, m in grades:
        mm = base & (d["dir"] == 1) & m
        n, w = wr(y[mm])
        ws = [wr(y[mm & (seg == s)])[1] for s in ("train", "hold", "lock")]
        ev = (w / 100) * .92 - (1 - w / 100)
        print(f"{name:22} {n:>6,} {w:>6.2f}% | {ws[0]:>6.2f}% {ws[1]:>6.2f}% {ws[2]:>6.2f}% | {min(ws):>5.2f}% {ev * 100:>+6.2f}%")

    print("\n" + "=" * 94)
    print("2) BISAKAH PUT DISELAMATKAN? (filter HTF H1 searah)")
    print("=" * 94)
    tests = [
        ("PUT baseline (semua CHoCH)  ", np.ones(len(y), bool)),
        ("PUT + OTE                   ", (ote == 1)),
        ("PUT + OTE + H1 turun        ", (ote == 1) & (h1 == 1)),
        ("PUT + H1 turun saja         ", (h1 == 1)),
        ("PUT + OTE + FVG + H1 turun  ", (ote == 1) & (fvg == 1) & (h1 == 1)),
        ("PUT + OTE + OB + H1 turun   ", (ote == 1) & (ob == 1) & (h1 == 1)),
    ]
    for lbl, m in tests:
        mm = base & (d["dir"] == -1) & m
        n, w = wr(y[mm])
        ws = [wr(y[mm & (seg == s)])[1] for s in ("train", "hold", "lock")]
        print(f"{lbl} n={n:>6,}  WR={w:6.2f}%  (tr {ws[0]:5.2f} / ho {ws[1]:5.2f} / lk {ws[2]:5.2f})")

    print("\n" + "=" * 94)
    print("3) PEMBANDING CALL dengan filter HTF yang sama")
    print("=" * 94)
    for lbl, m in (
        ("CALL + OTE                 ", (ote == 1)),
        ("CALL + OTE + H1 naik       ", (ote == 1) & (h1 == 1)),
        ("CALL + OTE + FVG + H1 naik ", (ote == 1) & (fvg == 1) & (h1 == 1)),
    ):
        mm = base & (d["dir"] == 1) & m
        n, w = wr(y[mm])
        print(f"{lbl} n={n:>6,}  WR={w:6.2f}%")


if __name__ == "__main__":
    main()
