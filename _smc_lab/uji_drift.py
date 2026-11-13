"""uji_drift.py — pisahkan edge aturan dari drift pasar.

Jika aturan BENAR, winrate harus naik saat gate diperketat (kurva keyakinan naik).
Jika hanya drift, winrate tetap datar berapa pun gate-nya.
Juga: ukur baseline drift seluruh candle pada jendela data ini.
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
    print("=== BASELINE DRIFT: semua candle di jendela data (142 simbol) ===")
    tabs = H.load_tables()
    day0 = H._day_index(tabs)
    tot = win = 0
    per_day = {}
    for T in tabs.values():
        y = T["y"]
        ok = T["ok"]
        d = T["t"] // H.DAY - day0
        nz = ok & (y != 0)
        tot += int(nz.sum())
        win += int((y[nz] > 0).sum())
        for dy in np.unique(d):
            m = nz & (d == dy)
            a, b = per_day.get(int(dy), (0, 0))
            per_day[int(dy)] = (a + int(m.sum()), b + int((y[m] > 0).sum()))
    print(f"  total candle: {tot:,}  naik: {win:,}  = {win / tot * 100:.2f}%")
    print("  per hari:")
    for dy in sorted(per_day):
        n, w = per_day[dy]
        print(f"    hari {dy}: n={n:>7,}  naik={w / n * 100:5.2f}%")

    print("\n=== KURVA KEYAKINAN: apakah menambah gate menaikkan winrate? ===")
    d = load()
    gates = ("h1", "sweep", "ob", "fvg", "eqh", "ote", "dsp", "bos", "tier")
    for direction, label in ((1, "CALL"), (-1, "PUT")):
        print(f"\n  {label}: winrate saat makin banyak gate aktif")
        for p in (2,):
            base = (d["dir"] == direction) & (d["p"] == p)
            for k in range(0, 6):
                cnt = np.zeros(len(d["y"]), np.int8)
                for g in gates:
                    cnt += d[g]
                m = base & (cnt >= k)
                n, w = wr(d["y"][m])
                if n < 100:
                    continue
                print(f"    >= {k} gate: n={n:>8,}  WR={w:5.2f}%")

    print("\n=== OTE saja vs NON-OTE (kontrol drift) ===")
    for direction, label in ((1, "CALL"), (-1, "PUT")):
        base = (d["dir"] == direction) & (d["p"] == 2)
        for name, m in (("OTE", base & (d["ote"] == 1)), ("NON-OTE", base & (d["ote"] == 0))):
            n, w = wr(d["y"][m])
            print(f"  {label:4s} {name:8s} n={n:>8,} WR={w:5.2f}%")


if __name__ == "__main__":
    main()
