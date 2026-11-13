"""ringkasan_hasil.py — jalankan semua uji final dan simpan ke CSV/JSON.

Output:
  hasil_ringkasan.csv  — satu baris per sel uji
  hasil_ringkasan.json — angka kunci untuk dibaca program lain
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

SERVER = Path(r"E:\JANGAN DI HAPUS\OpenTerminal\server")
sys.path.insert(0, str(SERVER))

import research_harness as H  # noqa: E402
from uji_ote_murni import zones  # noqa: E402

HOLDOUT_FROM = H.HOLDOUT_FROM_DAY
HERE = Path(__file__).parent


def wr(y: np.ndarray) -> tuple[int, float]:
    nz = y[y != 0]
    return (len(nz), float((nz > 0).mean() * 100) if len(nz) else 0.0)


def main() -> None:
    print("memuat cache...", flush=True)
    tabs = H.load_tables()
    day0 = H._day_index(tabs)

    cells = {
        k: {"train": [0, 0], "hold": [0, 0], "lock": [0, 0]}
        for k in ("ote_only", "ote_choch", "choch_only", "neither")
    }
    per_sym = {}

    for sym, T in tabs.items():
        z = zones(T, 2)
        y = T["y"]
        ok = T["ok"]
        day = T["t"] // H.DAY - day0
        seg = np.where(day >= H.LOCKBOX_FROM_DAY, "lock", np.where(day >= HOLDOUT_FROM, "hold", "train"))

        for direction, ote_m, ch_m in ((1, z["ote_long"], z["choch_up"]), (-1, z["ote_short"], z["choch_dn"])):
            sets = {
                "ote_only": ote_m & ok,
                "ote_choch": ote_m & ch_m & ok,
                "choch_only": ch_m & ~ote_m & ok,
                "neither": ~ote_m & ~ch_m & ok,
            }
            for name, m in sets.items():
                for s in ("train", "hold", "lock"):
                    mm = m & (seg == s)
                    if not mm.any():
                        continue
                    yy = y[mm]
                    nz = yy != 0
                    cells[name][s][0] += int((yy[nz] == direction).sum())
                    cells[name][s][1] += int(nz.sum())
            if direction == 1:
                n, w = wr(y[sets["ote_choch"]])
                if n:
                    per_sym[sym] = (n, w)

    rows = []
    for name, segs in cells.items():
        for seg, (win, n) in segs.items():
            rows.append(dict(cell=name, segment=seg, n=n, win=win,
                             wr=round(win / n * 100, 3) if n else 0.0,
                             lb95=round(H.wilson_low(win, n), 3) if n else 0.0))

    csv = HERE / "hasil_ringkasan.csv"
    with csv.open("w", encoding="utf-8") as f:
        f.write("cell,segment,n,win,wr,lb95\n")
        for r in rows:
            f.write(f"{r['cell']},{r['segment']},{r['n']},{r['win']},{r['wr']},{r['lb95']}\n")

    big = [(s, n, w) for s, (n, w) in per_sym.items() if n >= 20]
    hold = next(r for r in rows if r["cell"] == "ote_choch" and r["segment"] == "hold")
    summary = {
        "data": {"symbols": len(tabs), "holdout_from_day": HOLDOUT_FROM, "lockbox_from_day": H.LOCKBOX_FROM_DAY},
        "sel": rows,
        "ote_choch_call": {
            "n_holdout": hold["n"],
            "wr_holdout": hold["wr"],
            "lb95": hold["lb95"],
            "simbol_n20": len(big),
            "simbol_di_atas_50": sum(1 for _, _, w in big if w > 50),
            "simbol_di_atas_impas_92": sum(1 for _, _, w in big if w > 52.08),
            "ev_payout_92_pct": round((hold["wr"] / 100) * 0.92 - (1 - hold["wr"] / 100), 4) * 100,
        },
        "catatan": [
            "Edge hanya muncul di CALL; PUT+OTE tidak bekerja.",
            "Data riset 13-20 Sep 2026, jurnal live 4-12 Nov 2026 (tidak tumpang tindih).",
            "CHoCH tanpa OTE ~50%; OTE tanpa CHoCH ~50%; keduanya 55.45%.",
        ],
    }
    js = HERE / "hasil_ringkasan.json"
    js.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\nCSV  : {csv}")
    print(f"JSON : {js}")
    print("\nringkasan OTE+CHoCH CALL (holdout):")
    for k, v in summary["ote_choch_call"].items():
        print(f"  {k:28} {v}")


if __name__ == "__main__":
    main()
