"""tier_strategi.py — cari struktur sinyal BERTINGKAT (bukan kombinasi tunggal).

Tujuan: winrate maksimal TANPA membuat sinyal jadi langka.
Pendekatan: sinyal diberi peringkat A/B/C berdasarkan konfluensi OB-FVG-Liquidity
di sekitar CHoCH+OTE. Tier A = paling ketat, tier C = paling longgar.
Dengan begitu trader tetap dapat banyak sinyal, tapi tahu mana yang paling kuat.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

SERVER = Path(r"E:\JANGAN DI HAPUS\OpenTerminal\server")
sys.path.insert(0, str(SERVER))

import research_harness as H  # noqa: E402

HOLDOUT_FROM = H.HOLDOUT_FROM_DAY


def wr(y):
    nz = y[y != 0]
    return (len(nz), float((nz > 0).mean() * 100) if len(nz) else 0.0)


def main():
    d = np.load(Path(__file__).with_name("cache_signals.npz"))
    y, day, ote = d["y"], d["day"], d["ote"]
    ob, fvg, sweep, bos, h1, dsp = d["ob"], d["fvg"], d["sweep"], d["bos"], d["h1"], d["dsp"]
    base = d["p"] == 2

    print("=" * 84)
    print("STRUKTUR BERTINGKAT: konfluensi OB / FVG / Liquidity di atas CHoCH")
    print("=" * 84)
    print("Semua baris di bawah = CHoCH kanonik (p=2), CALL, dipecah per tier.\n")

    tiers = [
        ("A  OTE+OB+FVG+sweep", (ote == 1) & (ob == 1) & (fvg == 1) & (sweep == 1)),
        ("B  OTE+OB+sweep", (ote == 1) & (ob == 1) & (sweep == 1)),
        ("B2 OTE+FVG+sweep", (ote == 1) & (fvg == 1) & (sweep == 1)),
        ("C  OTE+OB", (ote == 1) & (ob == 1)),
        ("C2 OTE+FVG", (ote == 1) & (fvg == 1)),
        ("C3 OTE+sweep", (ote == 1) & (sweep == 1)),
        ("D  OTE saja", (ote == 1)),
        ("E  OB+FVG+sweep (tanpa OTE)", (ote == 0) & (ob == 1) & (fvg == 1) & (sweep == 1)),
        ("F  OB saja (tanpa OTE)", (ote == 0) & (ob == 1)),
        ("G  semua CHoCH", np.ones(len(y), bool)),
    ]

    print(f"{'tier':32} {'n':>8} {'WR':>7} {'lb95':>6} {'per_hari':>9}")
    print("-" * 84)
    for name, m in tiers:
        mm = base & (d["dir"] == 1) & m
        n, w = wr(y[mm])
        lb = H.wilson_low(int((y[mm][y[mm] != 0] > 0).sum()), n) if n else 0
        print(f"{name:32} {n:>8,} {w:>6.2f}% {lb:>6.2f} {n / 8 / 142:>9.2f}")

    print("\n" + "=" * 84)
    print("APAKAH SWEEP & OB MENAMBAH DI ATAS OTE+CHoCH? (CALL, n besar)")
    print("=" * 84)
    o = (ote == 1)
    for lbl, extra in (
        ("OTE+CHoCH            ", np.ones(len(y), bool)),
        ("  + sweep             ", sweep == 1),
        ("  + OB                ", ob == 1),
        ("  + FVG               ", fvg == 1),
        ("  + BOS               ", bos == 1),
        ("  + HTF H1 searah     ", h1 == 1),
        ("  + displacement      ", dsp == 1),
        ("  tanpa sweep          ", sweep == 0),
        ("  tanpa OB             ", ob == 0),
    ):
        mm = base & (d["dir"] == 1) & o & extra
        n, w = wr(y[mm])
        print(f"{lbl} n={n:>7,}  WR={w:6.2f}%")

    print("\n" + "=" * 84)
    print("SISI PUT: apakah ada kombinasi yang bekerja?")
    print("=" * 84)
    for name, m in tiers[:7]:
        mm = base & (d["dir"] == -1) & m
        n, w = wr(y[mm])
        print(f"{name:32} n={n:>8,}  WR={w:6.2f}%")

    print("\n" + "=" * 84)
    print("TARGET FREKUENSI: kalau butuh >=8 sinyal/hari/142 simbol")
    print("=" * 84)
    for name, m in tiers:
        mm = base & (d["dir"] == 1) & m
        n, w = wr(y[mm])
        per_hari = n / 8
        if per_hari >= 8:
            print(f"  {name:32} {per_hari:>7.0f}/hari  WR={w:5.2f}%")


if __name__ == "__main__":
    main()
