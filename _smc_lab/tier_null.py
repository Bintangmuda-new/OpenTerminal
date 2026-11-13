"""tier_null.py — uji null untuk setiap kandidat tier SEBELUM dipakai.

Jebakan yang sudah terbukti: subset CALL tertentu punya drift naik, sehingga
winrate tinggi muncul walau gate-nya kosong. Kontrol yang benar: pada
simbol+hari yang SAMA, acak sinyal mana yang "lolos gate", lalu bandingkan.

Jika winrate asli tidak jauh di atas sebaran acak -> gate itu tidak berguna.
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


def null_test(y, oo, key, n_iter=500, seed=7):
    """Acak label gate di dalam grup simbol+hari, jaga ukuran subset."""
    idx = np.arange(len(y))
    order = np.argsort(key, kind="stable")
    sk = key[order]
    groups = np.split(order, np.flatnonzero(np.diff(sk) != 0) + 1)
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n_iter):
        perm = oo.copy()
        for g in groups:
            perm[g] = oo[rng.permutation(g)]
        if perm.sum() == 0:
            continue
        out.append(wr(y[perm])[1])
    return np.array(out)


def main():
    d = np.load(Path(__file__).with_name("cache_signals.npz"))
    y, day, ote, sym = d["y"], d["day"], d["ote"], d["sym"]
    ob, fvg, sweep = d["ob"], d["fvg"], d["sweep"]
    base = (d["p"] == 2) & (d["dir"] == 1)

    idx = np.flatnonzero(base)
    yy = y[idx]
    sym_code = np.unique(sym[idx], return_inverse=True)[1].astype(np.int64)
    key = sym_code * 100 + day[idx].astype(np.int64)

    cands = [
        ("OTE saja     ", (ote == 1)[idx]),
        ("OTE+FVG      ", ((ote == 1) & (fvg == 1))[idx]),
        ("OTE+sweep    ", ((ote == 1) & (sweep == 1))[idx]),
        ("OTE+FVG+sweep", ((ote == 1) & (fvg == 1) & (sweep == 1))[idx]),
    ]

    print("=" * 88)
    print("UJI NULL TIER — apakah gate menambah sesuatu di atas drift?")
    print("=" * 88)
    print(f"{'kandidat':15} {'n':>6} {'WR asli':>8} {'null mean':>10} {'null p95':>9} {'p-value':>8}  vonis")
    print("-" * 88)
    for name, oo in cands:
        n, w = wr(yy[oo])
        if n == 0:
            continue
        nulls = null_test(yy, oo, key, n_iter=500)
        p = float((nulls >= w).mean())
        vonis = "EDGE NYATA" if p < 0.05 else "DRIFT / TIDAK BERGUNA"
        print(f"{name:15} {n:>6,} {w:>7.2f}% {nulls.mean():>9.2f}% {np.percentile(nulls, 95):>8.2f}% {p:>8.3f}  {vonis}")

    print("\n" + "=" * 88)
    print("PEMBANDING: baseline 'selalu CALL' pada subset yang sama")
    print("=" * 88)
    for name, oo in cands:
        # pada grup yang sama, ambil SEMUA sinyal (bukan hanya yang lolos gate)
        groups_with_gate = np.unique(key[oo])
        sel = np.isin(key, groups_with_gate)
        n, w = wr(yy[sel])
        print(f"{name:15} grup={len(groups_with_gate):>4}  semua sinyal di grup itu: n={n:>7,} WR={w:5.2f}%")


if __name__ == "__main__":
    main()
