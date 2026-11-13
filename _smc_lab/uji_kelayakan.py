"""uji_kelayakan.py — apakah OTE+CHoCH layak dipakai? Uji per simbol + EV + payout.

Kriteria praktis:
  * winrate harus > titik impas payout (92% -> 52.08%, 80% -> 55.56%)
  * harus positif di mayoritas simbol, bukan 2-3 simbol saja
  * frekuensi harus cukup untuk trading nyata
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

SERVER = Path(r"E:\JANGAN DI HAPUS\OpenTerminal\server")
sys.path.insert(0, str(SERVER))

import research_harness as H  # noqa: E402
from uji_ote_murni import zones  # noqa: E402

HOLDOUT_FROM = H.HOLDOUT_FROM_DAY


def wr(y: np.ndarray) -> tuple[int, float]:
    nz = y[y != 0]
    return (len(nz), float((nz > 0).mean() * 100) if len(nz) else 0.0)


def main() -> None:
    print("memuat cache...", flush=True)
    tabs = H.load_tables()
    day0 = H._day_index(tabs)

    per_sym = {}
    all_y = []
    all_t = []
    for sym, T in tabs.items():
        z = zones(T, 2)
        y = T["y"]
        ok = T["ok"]
        m = ok & z["choch_up"] & z["ote_long"]
        n, w = wr(y[m])
        if n:
            per_sym[sym] = (n, w)
        all_y.append(y[m])
        all_t.append(T["t"][m])

    y = np.concatenate(all_y)
    t = np.concatenate(all_t)
    n_tot, w_tot = wr(y)

    print("\n" + "=" * 78)
    print("1) SEBARAN PER SIMBOL (OTE + CHoCH CALL)")
    print("=" * 78)
    big = [(s, n, w) for s, (n, w) in per_sym.items() if n >= 20]
    pos = sum(1 for _, _, w in big if w > 50)
    print(f"  simbol dengan n>=20: {len(big)}   winrate>50%: {pos} ({pos / len(big) * 100:.0f}%)")
    impas92 = sum(1 for _, _, w in big if w > 52.08)
    print(f"  simbol yang melewati impas payout 92%: {impas92} ({impas92 / len(big) * 100:.0f}%)")
    print("\n  10 terbaik:")
    for s, n, w in sorted(big, key=lambda r: -r[2])[:10]:
        print(f"    {s:14s} n={n:>4} WR={w:5.1f}%")
    print("  10 terburuk:")
    for s, n, w in sorted(big, key=lambda r: r[2])[:10]:
        print(f"    {s:14s} n={n:>4} WR={w:5.1f}%")

    print("\n" + "=" * 78)
    print("2) FREKUENSI & EV PADA BERBAGAI PAYOUT")
    print("=" * 78)
    span_hari = (t.max() - t.min()) / 86400
    print(f"  total sinyal: {n_tot:,}  winrate {w_tot:.2f}%")
    print(f"  rentang data: {span_hari:.1f} hari, {len(tabs)} simbol")
    print(f"  sinyal per simbol per hari: {n_tot / len(tabs) / span_hari:.2f}")
    print()
    print(f"  {'payout':>7} {'impas':>8} {'EV/trade':>10} {'status':>12}")
    for payout in (0.92, 0.88, 0.85, 0.80, 0.75):
        impas = 1 / (1 + payout) * 100
        ev = (w_tot / 100) * payout - (1 - w_tot / 100)
        status = "UNTUNG" if ev > 0 else "RUGI"
        print(f"  {payout * 100:>6.0f}% {impas:>7.2f}% {ev * 100:>+9.2f}% {status:>12}")

    print("\n" + "=" * 78)
    print("3) BERAPA SINYAL PER HARI YANG BISA DIPAKAI?")
    print("=" * 78)
    day = (t // 86400).astype(np.int64)
    for d in sorted(np.unique(day)):
        n = int((day == d).sum())
        print(f"  hari {d}: {n:>4} sinyal ({n / len(tabs):.2f} per simbol)")


if __name__ == "__main__":
    main()
