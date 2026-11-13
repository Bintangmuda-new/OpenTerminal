"""uji_konsekutif.py — apakah entry beruntun (mengikuti tren) merugikan?

Jurnal live menunjukkan 33/43 entry berjarak tepat 5 menit: sinyal CHoCH muncul
setiap candle saat tren berjalan. Uji pada data riset: bagaimana winrate
sinyal yang datang <=N candle setelah sinyal sebelumnya (arah sama, simbol sama)
dibanding sinyal yang "segar" (jeda panjang).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

SERVER = Path(r"E:\JANGAN DI HAPUS\OpenTerminal\server")
sys.path.insert(0, str(SERVER))

import research_harness as H  # noqa: E402

HOLDOUT_FROM = H.HOLDOUT_FROM_DAY


def load() -> dict[str, np.ndarray]:
    z = np.load(Path(__file__).with_name("cache_signals.npz"))
    return {k: z[k] for k in z.files}


def wr(y: np.ndarray) -> tuple[int, float]:
    nz = y[y != 0]
    return (len(nz), float((nz > 0).mean() * 100) if len(nz) else 0.0)


def main() -> None:
    print("memuat cache penuh (butuh urutan waktu)...", flush=True)
    tabs = H.load_tables()
    day0 = H._day_index(tabs)

    # kumpulkan sinyal CHoCH kanonik per simbol, urut waktu
    from backtest26_smc_canon import smc_canon

    buckets = {1: {"fresh": [0, 0], "chained": [0, 0]}, -1: {"fresh": [0, 0], "chained": [0, 0]}}
    ote_buckets = {1: {"fresh": [0, 0], "chained": [0, 0]}, -1: {"fresh": [0, 0], "chained": [0, 0]}}
    all_buckets = {"fresh": [0, 0], "chained": [0, 0]}

    for sym, T in tabs.items():
        dirs_map = smc_canon(T, 1, 1.5)
        dirs = dirs_map["K1_choch_canon"]
        y = T["y"]
        ok = T["ok"]
        t = T["t"]
        for direction, mask in ((1, dirs == 1), (-1, dirs == -1)):
            idx = np.flatnonzero(mask & ok)
            if len(idx) < 2:
                continue
            gaps = np.diff(t[idx]) / 60.0
            prev_gap = np.concatenate([[np.inf], gaps])
            for i, ix in enumerate(idx):
                yy = y[ix]
                if yy == 0:
                    continue
                chained = prev_gap[i] <= 5
                b = buckets[direction]["chained" if chained else "fresh"]
                b[0] += int(yy == direction)
                b[1] += 1
                ab = all_buckets["chained" if chained else "fresh"]
                ab[0] += int(yy == direction)
                ab[1] += 1

    print("\n" + "=" * 74)
    print("A) FRESH vs CHAINED (sinyal <=5 menit setelah sinyal sebelumnya)")
    print("=" * 74)
    for direction, lbl in ((1, "CALL"), (-1, "PUT")):
        for kind in ("fresh", "chained"):
            w, n = buckets[direction][kind]
            print(f"  {lbl:4s} {kind:8s} n={n:>9,}  WR={w / n * 100:5.2f}%")
    for kind in ("fresh", "chained"):
        w, n = all_buckets[kind]
        print(f"  SEMUA {kind:8s} n={n:>9,}  WR={w / n * 100:5.2f}%")

    print("\n" + "=" * 74)
    print("B) BUKTI VISUAL: kenapa 'chained' bisa berbeda")
    print("=" * 74)
    print("  chained = sinyal muncul saat tren sudah berjalan (entry terlambat)")
    print("  fresh   = sinyal pertama setelah struktur baru terbentuk")
    for direction, lbl in ((1, "CALL"), (-1, "PUT")):
        wf, nf = buckets[direction]["fresh"]
        wc, nc = buckets[direction]["chained"]
        if nf and nc:
            print(f"  {lbl:4s} selisih = {wc / nc * 100 - wf / nf * 100:+.2f}pt "
                  f"(fresh {wf / nf * 100:.2f}% vs chained {wc / nc * 100:.2f}%)")


if __name__ == "__main__":
    main()
