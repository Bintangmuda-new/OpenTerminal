"""validasi_final.py — uji kandidat teratas secara keras sebelum dipakai.

Uji:
  1. LOCKBOX (hari 7) — data yang tidak dipakai memilih apa pun.
  2. Per simbol — berapa simbol yang positif.
  3. Per hari — apakah keuntungan terkonsentrasi di satu hari.
  4. Kontrol arah (CALL terus / PUT terus pada baris yang sama).
  5. Kontrol null (label diacak per simbol+hari).
  6. Sensitivitas ambang OTE.
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


def load() -> dict[str, np.ndarray]:
    z = np.load(Path(__file__).with_name("cache_signals.npz"))
    return {k: z[k] for k in z.files}


def wr(y: np.ndarray) -> tuple[int, float]:
    nz = y[y != 0]
    return (len(nz), float((nz > 0).mean() * 100) if len(nz) else 0.0)


def gate_mask(d: dict, combo: str, direction: int, p: int) -> np.ndarray:
    m = (d["dir"] == direction) & (d["p"] == p)
    for g in combo.split("+"):
        m &= d[g] == 1
    return m


def section(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def main() -> None:
    d = load()
    cands = [
        ("CALL", 2, "fvg+ote"),
        ("CALL", 2, "sweep+ote"),
        ("CALL", 1, "eqh+ote"),
        ("CALL", 2, "h1+ote"),
        ("CALL", 1, "ob+ote"),
    ]

    for name, p, combo in cands:
        direction = 1 if name == "CALL" else -1
        m = gate_mask(d, combo, direction, p)
        y = d["y"][m]
        day = d["day"][m]
        sym = d["sym"][m]

        section(f"KANDIDAT {name} p={p} {combo}  (total {len(y):,} sinyal)")

        # 1. per segmen
        for seg, lo, hi in (("TRAIN  ", 0, HOLDOUT_FROM), ("HOLDOUT", HOLDOUT_FROM, LOCKBOX_FROM), ("LOCKBOX", LOCKBOX_FROM, 99)):
            s = (day >= lo) & (day < hi)
            n, w = wr(y[s])
            lb = H.wilson_low(int(round(w / 100 * n)), n) if n else 0
            print(f"  {seg}  n={n:>6,}  WR={w:6.2f}%  lb95={lb:5.2f}")

        # 2. per simbol
        npos = ntot = 0
        rows = []
        for s in np.unique(sym):
            n, w = wr(y[sym == s])
            if n < 20:
                continue
            ntot += 1
            npos += w > 50
            rows.append((s, n, w))
        print(f"\n  simbol (n>=20): {npos}/{ntot} winrate > 50%")
        for s, n, w in sorted(rows, key=lambda r: -r[2])[:6]:
            print(f"    {s:12s} n={n:>4} WR={w:5.1f}%")

        # 3. per hari
        print("\n  per hari:")
        for dy in sorted(np.unique(day)):
            n, w = wr(y[day == dy])
            print(f"    hari {dy}: n={n:>5,} WR={w:5.1f}%")

        # 4. kontrol arah
        n_all, w_all = wr(y)
        print(f"\n  kontrol: selalu CALL={w_all:.2f}% · selalu PUT={100 - w_all:.2f}%  (edge {w_all - 50:+.2f}pt)")

        # 5. kontrol null: acak label dalam simbol+hari
        rng = np.random.default_rng(11)
        # kunci numerik: simbol dikodekan ke int agar bisa diurutkan
        sym_code = np.unique(sym, return_inverse=True)[1].astype(np.int64)
        key = sym_code * 100 + day.astype(np.int64)
        order = np.argsort(key, kind="stable")
        sorted_key = key[order]
        groups = np.split(order, np.flatnonzero(np.diff(sorted_key) != 0) + 1)
        nulls = []
        for _ in range(200):
            shuffled = y.copy()
            for grp in groups:
                shuffled[grp] = y[rng.permutation(grp)]
            n2, w2 = wr(shuffled)
            nulls.append(w2)
        nulls = np.array(nulls)
        p95 = float(np.percentile(nulls, 95))
        print(f"  null p95={p95:.2f}%  vs sinyal asli {w_all:.2f}% {'LOLOS' if w_all > p95 else 'TIDAK LOLOS'}")


if __name__ == "__main__":
    main()
