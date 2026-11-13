"""tier_validasi.py — validasi kandidat tier di holdout & lockbox terpisah.

Angka gabungan bisa menipu. Setiap kandidat harus lolos di dua periode yang
tidak dipakai memilih, dan tetap punya frekuensi layak.
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
    ob, fvg, sweep = d["ob"], d["fvg"], d["sweep"]
    base = d["p"] == 2

    seg = np.where(day >= LOCKBOX_FROM, "lock", np.where(day >= HOLDOUT_FROM, "hold", "train"))

    cands = [
        ("OTE+FVG          ", (ote == 1) & (fvg == 1)),
        ("OTE+sweep        ", (ote == 1) & (sweep == 1)),
        ("OTE+FVG+sweep    ", (ote == 1) & (fvg == 1) & (sweep == 1)),
        ("OTE+OB+FVG+sweep ", (ote == 1) & (ob == 1) & (fvg == 1) & (sweep == 1)),
        ("OTE saja         ", (ote == 1)),
    ]

    print("=" * 92)
    print("VALIDASI TIER — CALL, p=2  (train hari 0-5 | holdout hari 6 | lockbox hari 7)")
    print("=" * 92)
    print(f"{'kandidat':19} {'n_train':>8} {'WR_tr':>7} | {'n_ho':>7} {'WR_ho':>7} {'lb':>6} | {'n_lk':>7} {'WR_lk':>7} {'lb':>6}  {'vonis':>10}")
    print("-" * 92)
    for name, m in cands:
        row = []
        okall = True
        for s in ("train", "hold", "lock"):
            mm = base & (d["dir"] == 1) & m & (seg == s)
            n, w = wr(y[mm])
            win = int((y[mm][y[mm] != 0] > 0).sum())
            lb = H.wilson_low(win, n) if n else 0
            row.append((n, w, lb))
            if n < 100 or w <= 52.08:
                okall = False
        (ntr, wtr, _), (nho, who, lbo), (nlk, wlk, lbk) = row
        vonis = "LAYAK" if okall else "gugur"
        print(f"{name:19} {ntr:>8,} {wtr:>6.2f}% | {nho:>7,} {who:>6.2f}% {lbo:>6.2f} | {nlk:>7,} {wlk:>6.2f}% {lbk:>6.2f}  {vonis:>10}")

    print("\n" + "=" * 92)
    print("FREKUENSI & EV (kandidat layak, dihitung dari holdout+lockbox)")
    print("=" * 92)
    for name, m in cands:
        mm = base & (d["dir"] == 1) & m & (seg != "train")
        n, w = wr(y[mm])
        if n < 100:
            continue
        per_sym_day = n / (142 * 2)
        ev = (w / 100) * 0.92 - (1 - w / 100)
        print(f"{name} n={n:>6,}  WR={w:5.2f}%  EV={ev * 100:+5.2f}%  "
              f"{per_sym_day:5.2f} sinyal/simbol/hari  -> pantau 10 simbol = {per_sym_day * 10:4.1f}/hari")

    print("\n" + "=" * 92)
    print("UJI ARAH PUT pada kandidat yang sama (untuk memastikan tetap CALL-only)")
    print("=" * 92)
    for name, m in cands[:4]:
        mm = base & (d["dir"] == -1) & m & (seg != "train")
        n, w = wr(y[mm])
        print(f"{name} n={n:>6,}  WR={w:5.2f}%")


if __name__ == "__main__":
    main()
