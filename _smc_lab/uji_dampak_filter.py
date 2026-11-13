"""uji_dampak_filter.py — dampak filter OTE pada frekuensi sinyal aplikasi.

Aplikasi memakai p=1, disp=1.5 (AnalyzeSmcFull), bukan p=2. Hitung:
  * berapa persen sinyal CHoCH yang lolos filter OTE
  * berapa sinyal per simbol per hari
  * winrate sebelum vs sesudah filter
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

SERVER = Path(r"E:\JANGAN DI HAPUS\OpenTerminal\server")
sys.path.insert(0, str(SERVER))

import research_harness as H  # noqa: E402

HOLDOUT_FROM = H.HOLDOUT_FROM_DAY


def main() -> None:
    d = np.load(Path(__file__).with_name("cache_signals.npz"))
    y, day, sym, ote = d["y"], d["day"], d["sym"], d["ote"]

    print("=" * 76)
    print("DAMPAK FILTER OTE PADA SINYAL APLIKASI (p=1)")
    print("=" * 76)

    for p in (1, 2):
        base = d["p"] == p
        for direction, lbl in ((1, "CALL"), (-1, "PUT")):
            b = base & (d["dir"] == direction)
            m = b & (ote == 1)
            nb, nm = int(b.sum()), int(m.sum())
            yb = y[b]; yb = yb[yb != 0]
            ym = y[m]; ym = ym[ym != 0]
            wb = (yb > 0).mean() * 100 if len(yb) else 0
            wm = (ym > 0).mean() * 100 if len(ym) else 0
            print(f"\n  p={p} {lbl}:")
            print(f"    semua sinyal : n={nb:>8,}  WR={wb:5.2f}%")
            print(f"    setelah OTE  : n={nm:>8,}  WR={wm:5.2f}%   (lolos {nm / nb * 100:.1f}%)")
            print(f"    perubahan WR : {wm - wb:+.2f}pt")

    print("\n" + "=" * 76)
    print("FREKUENSI HARIAN SETELAH FILTER (p=1, CALL, 142 simbol)")
    print("=" * 76)
    m = (d["p"] == 1) & (d["dir"] == 1) & (d["ote"] == 1)
    for dy in sorted(np.unique(day)):
        n = int((m & (day == dy)).sum())
        n_sym = len(np.unique(sym[m & (day == dy)]))
        print(f"  hari {dy}: {n:>5,} sinyal di {n_sym:>3} simbol  ({n / 142:.2f} per simbol)")

    print("\n" + "=" * 76)
    print("SINYAL PER SIMBOL PER HARI — cukup untuk trading?")
    print("=" * 76)
    tot = int(m.sum())
    ndays = len(np.unique(day))
    print(f"  total {tot:,} sinyal CALL+OTE dalam {ndays} hari, 142 simbol")
    print(f"  rata-rata {tot / 142 / ndays:.2f} sinyal per simbol per hari")
    print(f"  artinya: memantau 10 simbol -> ~{tot / 142 / ndays * 10:.1f} sinyal/hari")


if __name__ == "__main__":
    main()
