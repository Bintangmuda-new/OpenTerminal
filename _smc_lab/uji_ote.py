"""uji_ote.py — vonis akhir untuk gate OTE.

OTE CALL menunjukkan +5pt di atas baseline. Uji apakah itu edge nyata:
  1. per hari (konsisten atau terkonsentrasi)
  2. per simbol (berapa simbol positif)
  3. null test yang benar: acak label DALAM simbol+hari, pada subset OTE saja
  4. bandingkan OTE vs NON-OTE di hari yang sama (kontrol drift sempurna)
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
    y, day, sym = d["y"], d["day"], d["sym"]
    base = (d["dir"] == 1) & (d["p"] == 2)
    ote = base & (d["ote"] == 1)
    non = base & (d["ote"] == 0)

    print("=" * 78)
    print("1) KONSISTENSI PER HARI  (OTE CALL vs NON-OTE CALL, hari yang sama)")
    print("=" * 78)
    print(f"{'hari':>5} {'n_ote':>7} {'WR_ote':>8} {'n_non':>8} {'WR_non':>8} {'selisih':>9}")
    pos = 0
    tot = 0
    for dy in sorted(np.unique(day)):
        mo = ote & (day == dy)
        mn = non & (day == dy)
        no, wo = wr(y[mo])
        nn, wn = wr(y[mn])
        if no < 50:
            continue
        tot += 1
        diff = wo - wn
        pos += diff > 0
        print(f"{dy:>5} {no:>7,} {wo:>7.2f}% {nn:>8,} {wn:>7.2f}% {diff:>+8.2f}pt")
    print(f"\n  hari dengan OTE > NON-OTE: {pos}/{tot}")

    print("\n" + "=" * 78)
    print("2) PER SIMBOL (OTE CALL, n>=20)")
    print("=" * 78)
    npos = ntot = 0
    rows = []
    for s in np.unique(sym):
        m = ote & (sym == s)
        n, w = wr(y[m])
        if n < 20:
            continue
        ntot += 1
        npos += w > 50
        rows.append((s, n, w))
    print(f"  simbol winrate > 50%: {npos}/{ntot}  ({npos / ntot * 100:.0f}%)")
    for s, n, w in sorted(rows, key=lambda r: -r[2])[:8]:
        print(f"    {s:14s} n={n:>4} WR={w:5.1f}%")
    print("  ...")
    for s, n, w in sorted(rows, key=lambda r: r[2])[:5]:
        print(f"    {s:14s} n={n:>4} WR={w:5.1f}%")

    print("\n" + "=" * 78)
    print("3) NULL TEST: acak label dalam simbol+hari, subset OTE saja")
    print("=" * 78)
    rng = np.random.default_rng(7)
    idx_ote = np.flatnonzero(ote)
    yy = y[idx_ote]
    dd = day[idx_ote]
    ss = sym[idx_ote]
    sym_code = np.unique(ss, return_inverse=True)[1].astype(np.int64)
    key = sym_code * 100 + dd.astype(np.int64)
    order = np.argsort(key, kind="stable")
    sk = key[order]
    groups = np.split(order, np.flatnonzero(np.diff(sk) != 0) + 1)
    n_real, w_real = wr(yy)
    nulls = []
    for _ in range(500):
        sh = yy.copy()
        for g in groups:
            sh[g] = yy[rng.permutation(g)]
        nulls.append(wr(sh)[1])
    nulls = np.array(nulls)
    print(f"  sinyal asli : n={n_real:,}  WR={w_real:.2f}%")
    print(f"  null        : mean={nulls.mean():.2f}%  sd={nulls.std():.2f}%  p95={np.percentile(nulls, 95):.2f}%  max={nulls.max():.2f}%")
    pval = float((nulls >= w_real).mean())
    print(f"  p-value (null >= asli) = {pval:.4f}")
    print(f"  VONIS: {'EDGE NYATA' if pval < 0.05 else 'TIDAK SIGNIFIKAN'}")

    print("\n" + "=" * 78)
    print("4) UKURAN EFEK vs BIAYA (binary option payout ~92%)")
    print("=" * 78)
    for lbl, m in (("OTE CALL", ote), ("NON-OTE CALL", non), ("SEMUA CALL", base)):
        n, w = wr(y[m])
        ev = w / 100 * 0.92 - (1 - w / 100)
        print(f"  {lbl:14s} WR={w:5.2f}%  EV/trade={ev * 100:+6.2f}%  (payout 92%)")
    print("\n  catatan: winrate impas untuk payout 92% = 52.08%")


if __name__ == "__main__":
    main()
