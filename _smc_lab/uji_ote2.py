"""uji_ote2.py — permutasi yang BENAR untuk gate OTE.

Null test sebelumnya salah: mengacak y di dalam grup simbol+hari tidak mengubah
jumlah menang total, sehingga sd=0. Yang benar: acak LABEL OTE di antara semua
sinyal CALL pada simbol+hari yang sama, lalu hitung ulang winrate subset semu-OTE.
Ini menguji apakah posisi OTE-lah yang membawa edge, atau kebetulan.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

import sys

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
    base = (d["dir"] == 1) & (d["p"] == 2)

    idx = np.flatnonzero(base)
    yy = y[idx]
    oo = (ote[idx] == 1)
    ss = sym[idx]
    dd = day[idx]

    n_real, w_real = wr(yy[oo])
    print(f"OTE CALL asli: n={n_real:,} WR={w_real:.2f}%")

    sym_code = np.unique(ss, return_inverse=True)[1].astype(np.int64)
    key = sym_code * 100 + dd.astype(np.int64)
    order = np.argsort(key, kind="stable")
    sk = key[order]
    groups = np.split(order, np.flatnonzero(np.diff(sk) != 0) + 1)
    print(f"grup (simbol x hari): {len(groups):,}  rata-rata {len(idx) / len(groups):.0f} sinyal/grup")

    rng = np.random.default_rng(21)
    nulls = []
    for _ in range(1000):
        perm = oo.copy()
        for g in groups:
            perm[g] = oo[rng.permutation(g)]
        # subset semu-OTE harus punya ukuran sama dengan OTE asli
        if perm.sum() == 0:
            continue
        nulls.append(wr(yy[perm])[1])
    nulls = np.array(nulls)
    pval = float((nulls >= w_real).mean())

    print("\nnull (label OTE diacak dalam simbol+hari):")
    print(f"  mean={nulls.mean():.2f}%  sd={nulls.std():.2f}%  "
          f"p95={np.percentile(nulls, 95):.2f}%  p99={np.percentile(nulls, 99):.2f}%  max={nulls.max():.2f}%")
    print(f"  p-value (null >= asli) = {pval:.4f}")
    print(f"  VONIS: {'EDGE NYATA' if pval < 0.05 else 'TIDAK SIGNIFIKAN'}")

    # --- uji kedua: OTE di PUT juga? (simetri = tanda aturan, bukan artefak) ---
    print("\n" + "=" * 70)
    print("UJI SIMETRI: apakah OTE bekerja dua arah?")
    print("=" * 70)
    for direction, lbl in ((1, "CALL"), (-1, "PUT")):
        b = (d["dir"] == direction) & (d["p"] == 2)
        for name, m in (("OTE", b & (d["ote"] == 1)), ("NON-OTE", b & (d["ote"] == 0))):
            n, w = wr(d["y"][m])
            print(f"  {lbl:4s} {name:8s} n={n:>8,} WR={w:5.2f}%")

    # --- uji ketiga: apakah OTE masih bekerja setelah drift dibuang? ---
    print("\n" + "=" * 70)
    print("UJI DRIFT-NETRAL: winrate relatif terhadap baseline hari itu")
    print("=" * 70)
    print(f"{'hari':>5} {'WR_ote':>8} {'WR_base':>8} {'selisih':>9}")
    diffs = []
    for dy in sorted(np.unique(day)):
        mo = base & (ote == 1) & (day == dy)
        mb = base & (day == dy)
        no, wo = wr(d["y"][mo])
        nb, wb = wr(d["y"][mb])
        if no < 50:
            continue
        diffs.append(wo - wb)
        print(f"{dy:>5} {wo:>7.2f}% {wb:>7.2f}% {wo - wb:>+8.2f}pt")
    diffs = np.array(diffs)
    print(f"\n  rata-rata selisih = {diffs.mean():+.2f}pt  (positif di {(diffs > 0).sum()}/{len(diffs)} hari)")
    se = diffs.std(ddof=1) / np.sqrt(len(diffs))
    print(f"  t = {diffs.mean() / se:.2f}  (n_hari={len(diffs)})")


if __name__ == "__main__":
    main()
