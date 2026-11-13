"""uji_asimetri.py — kenapa OTE hanya bekerja di CALL?

Hipotesis:
  H1. OTE long = beli di retracement ke bawah (mean-reversion ke arah trend).
      OTE short butuh harga naik dulu ke premium, lalu turun.
  H2. Drift naik jangka pendek membuat CALL diuntungkan mekanis.
  H3. OTE bekerja dua arah, tapi PUT kalah karena drift > efek OTE.

Uji: hitung edge OTE setelah mengeluarkan drift, per arah, dan per simbol
yang punya cukup banyak OTE CALL maupun PUT.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

SERVER = Path(r"E:\JANGAN DI HAPUS\OpenTerminal\server")
sys.path.insert(0, str(SERVER))

import research_harness as H  # noqa: E402


def load() -> dict[str, np.ndarray]:
    z = np.load(Path(__file__).with_name("cache_signals.npz"))
    return {k: z[k] for k in z.files}


def wr(y: np.ndarray) -> tuple[int, float]:
    nz = y[y != 0]
    return (len(nz), float((nz > 0).mean() * 100) if len(nz) else 0.0)


def main() -> None:
    d = load()
    y, day, sym, ote = d["y"], d["day"], d["sym"], d["ote"]

    print("=" * 74)
    print("A) PER SIMBOL: apakah OTE CALL & OTE PUT sama-sama bekerja?")
    print("=" * 74)
    print(f"{'simbol':16} {'n_CALL':>7} {'WR_CALL':>8} {'n_PUT':>7} {'WR_PUT':>8}  {'keduanya>50?':>12}")
    both = only_c = only_p = neither = 0
    rows = []
    for s in np.unique(sym):
        mc = (d["dir"] == 1) & (d["p"] == 2) & (ote == 1) & (sym == s)
        mp = (d["dir"] == -1) & (d["p"] == 2) & (ote == 1) & (sym == s)
        nc, wc = wr(y[mc])
        npp, wp = wr(y[mp])
        if nc < 25 or npp < 25:
            continue
        rows.append((s, nc, wc, npp, wp))
        if wc > 50 and wp > 50:
            both += 1
        elif wc > 50:
            only_c += 1
        elif wp > 50:
            only_p += 1
        else:
            neither += 1
    for s, nc, wc, npp, wp in sorted(rows, key=lambda r: -(r[2] + r[4]))[:12]:
        mark = "YA" if (wc > 50 and wp > 50) else ("CALL saja" if wc > 50 else ("PUT saja" if wp > 50 else "tidak"))
        print(f"{s:16} {nc:>7} {wc:>7.1f}% {npp:>7} {wp:>7.1f}%  {mark:>12}")
    print(f"\n  simbol dengan KEDUA arah >50%: {both}")
    print(f"  hanya CALL >50%: {only_c}   hanya PUT >50%: {only_p}   keduanya <50%: {neither}")
    print(f"  total simbol diuji: {len(rows)}")

    print("\n" + "=" * 74)
    print("B) EDGE OTE SETELAH DRIFT DIKELUARKAN (per arah)")
    print("=" * 74)
    print("   baseline = semua sinyal CHoCH arah itu pada hari yang sama")
    for direction, lbl in ((1, "CALL"), (-1, "PUT")):
        diffs = []
        for dy in sorted(np.unique(day)):
            mo = (d["dir"] == direction) & (d["p"] == 2) & (ote == 1) & (day == dy)
            mb = (d["dir"] == direction) & (d["p"] == 2) & (day == dy)
            no, wo = wr(y[mo])
            nb, wb = wr(y[mb])
            if no < 50:
                continue
            diffs.append(wo - wb)
        diffs = np.array(diffs)
        se = diffs.std(ddof=1) / np.sqrt(len(diffs)) if len(diffs) > 1 else float("nan")
        print(f"  {lbl:4s} edge rata-rata = {diffs.mean():+6.2f}pt  positif di {(diffs > 0).sum()}/{len(diffs)} hari  t={diffs.mean() / se:+.2f}")

    print("\n" + "=" * 74)
    print("C) APAKAH 'OTE' HANYA PROXY UNTUK 'HARGA SEDANG TURUN'?")
    print("=" * 74)
    print("   bandingkan: OTE CALL vs CALL biasa yang juga jauh di bawah high leg")
    span_proxy = d["ote"]  # sudah OTE
    base_c = (d["dir"] == 1) & (d["p"] == 2)
    for name, m in (
        ("OTE CALL", base_c & (span_proxy == 1)),
        ("CALL lain", base_c & (span_proxy == 0)),
    ):
        n, w = wr(y[m])
        print(f"  {name:12s} n={n:>8,} WR={w:5.2f}%")

    print("\n" + "=" * 74)
    print("D) DISTRIBUSI OTE: seberapa sering sinyal jatuh di zona OTE?")
    print("=" * 74)
    for direction, lbl in ((1, "CALL"), (-1, "PUT")):
        b = (d["dir"] == direction) & (d["p"] == 2)
        nb = int(b.sum())
        no = int((b & (ote == 1)).sum())
        print(f"  {lbl:4s}: {no:,} / {nb:,} = {no / nb * 100:.1f}% sinyal berada di zona OTE")


if __name__ == "__main__":
    main()
