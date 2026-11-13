"""final_tier.py — angka final untuk desain sinyal BERTINGKAT (grade A/B/C).

Koreksi metodologi: uji null yang benar = acak MASK GATE di dalam grup
simbol+hari, bukan acak y di dalam subset terpilih (yang tidak bisa menggeser
rata-rata dan karena itu tidak membuktikan apa pun).
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
    fvg, sweep, ob = d["fvg"], d["sweep"], d["ob"]
    base = d["p"] == 2
    seg = np.where(day >= LOCKBOX_FROM, "lock", np.where(day >= HOLDOUT_FROM, "hold", "train"))

    grades = [
        ("A  OTE+FVG+sweep", (ote == 1) & (fvg == 1) & (sweep == 1)),
        ("A2 OTE+FVG      ", (ote == 1) & (fvg == 1)),
        ("B  OTE+sweep    ", (ote == 1) & (sweep == 1) & (fvg == 0)),
        ("C  OTE saja     ", (ote == 1) & (fvg == 0) & (sweep == 0)),
    ]

    print("=" * 90)
    print("GRADE SINYAL — CALL, p=2 (mutually exclusive, supaya tidak dobel)")
    print("=" * 90)
    print(f"{'grade':18} {'n':>7} {'WR':>7} {'lb95':>6} {'WR_tr':>7} {'WR_ho':>7} {'WR_lk':>7} {'EV92':>7} {'/hari':>7}")
    print("-" * 90)
    for name, m in grades:
        mm = base & (d["dir"] == 1) & m
        n, w = wr(y[mm])
        win = int((y[mm][y[mm] != 0] > 0).sum())
        lb = H.wilson_low(win, n) if n else 0
        segw = []
        for s in ("train", "hold", "lock"):
            _, ws = wr(y[mm & (seg == s)])
            segw.append(ws)
        ev = (w / 100) * 0.92 - (1 - w / 100)
        print(f"{name:18} {n:>7,} {w:>6.2f}% {lb:>6.2f} {segw[0]:>6.2f}% {segw[1]:>6.2f}% {segw[2]:>6.2f}% {ev * 100:>+6.2f}% {n / 8:>7.0f}")

    # gabungan semua OTE = frekuensi maksimal
    m_all = base & (d["dir"] == 1) & (ote == 1)
    n, w = wr(y[m_all])
    win = int((y[m_all][y[m_all] != 0] > 0).sum())
    print("-" * 90)
    print(f"{'TOTAL OTE':18} {n:>7,} {w:>6.2f}% {H.wilson_low(win, n):>6.2f} "
          f"{wr(y[m_all & (seg == 'train')])[1]:>6.2f}% {wr(y[m_all & (seg == 'hold')])[1]:>6.2f}% "
          f"{wr(y[m_all & (seg == 'lock')])[1]:>6.2f}% {(w / 100 * .92 - (1 - w / 100)) * 100:>+6.2f}% {n / 8:>7.0f}")

    print("\n" + "=" * 90)
    print("APAKAH SWEEP MENAMBAH DI ATAS OTE+FVG?")
    print("=" * 90)
    for lbl, m in (
        ("OTE+FVG (semua)      ", (ote == 1) & (fvg == 1)),
        ("  + sweep            ", (ote == 1) & (fvg == 1) & (sweep == 1)),
        ("  tanpa sweep        ", (ote == 1) & (fvg == 1) & (sweep == 0)),
        ("OTE+FVG + OB         ", (ote == 1) & (fvg == 1) & (ob == 1)),
        ("OTE+FVG tanpa OB     ", (ote == 1) & (fvg == 1) & (ob == 0)),
    ):
        mm = base & (d["dir"] == 1) & m
        n, w = wr(y[mm])
        print(f"{lbl} n={n:>6,}  WR={w:6.2f}%")

    print("\n" + "=" * 90)
    print("SISI PUT — konfirmasi tetap tidak layak")
    print("=" * 90)
    for name, m in grades:
        mm = base & (d["dir"] == -1) & m
        n, w = wr(y[mm])
        print(f"{name:18} n={n:>7,}  WR={w:6.2f}%")


if __name__ == "__main__":
    main()
