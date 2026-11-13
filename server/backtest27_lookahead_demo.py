"""backtest27_lookahead_demo.py — apa yang sebenarnya dibutuhkan untuk winrate 100%?

Pemilik meminta "winrate 100% pakai strategi SMC". Skrip ini tidak berdebat; ia
MENUNJUKKAN mekanismenya. Empat aturan diuji pada data yang sama, protokol yang sama:

  A. curang_melihat_B      arah diambil dari close candle B (masa depan)          -> harus 100%
  B. curang_geser_1        sama seperti A tetapi digeser 1 candle (bug lazim)     -> ~50%
  C. smc_bocor             aturan SMC dengan satu kebocoran lookahead kecil       -> tinggi, lalu
                           diuji ulang tanpa kebocoran                             -> ~50%
  D. smc_jujur             aturan SMC T-5 yang sebenarnya (pembanding)            -> ~50%

Tujuannya: menunjukkan bahwa 100% BUKAN hasil dari SMC, melainkan hasil dari melihat
masa depan. Setiap angka di bawah dihitung pada 42 juta tick yang sama.
"""
from __future__ import annotations

import numpy as np

import research_harness as H
from backtest24_smc import smc, rules


def run(name: str, fn) -> dict:
    """fn(T) -> +1/-1/0 per baris. Hitung winrate di ketiga segmen."""
    tabs = H.load_tables()
    d0 = H._day_index(tabs)
    agg = {"train": [0, 0, 0], "holdout": [0, 0, 0], "lockbox": [0, 0, 0]}
    for T in tabs.values():
        dirs = np.asarray(fn(T), dtype=float)
        day = T["t"] // H.DAY - d0
        segs = {"train": day < H.HOLDOUT_FROM_DAY,
                "holdout": (day >= H.HOLDOUT_FROM_DAY) & (day < H.LOCKBOX_FROM_DAY),
                "lockbox": day >= H.LOCKBOX_FROM_DAY}
        for s, msk in segs.items():
            m = msk & T["ok"] & (dirs != 0)
            y = T["y"][m].astype(float)
            pr = dirs[m]
            agg[s][0] += int((y == pr).sum())
            agg[s][1] += int((y == -pr).sum())
            agg[s][2] += int((y == 0).sum())
    out = {"name": name}
    for s, (w, l, dr) in agg.items():
        n = w + l + dr
        out[s] = (w / n * 100 if n else 0.0, n, H.wilson_low(w, n) if n else 0.0)
    a, b, c = out["train"], out["holdout"], out["lockbox"]
    print(f"{name:24} TRAIN {a[0]:6.2f}% n={a[1]:7d} | HOLD {b[0]:6.2f}% n={b[1]:7d} | "
          f"LOCK {c[0]:6.2f}% n={c[1]:7d} lb95={c[2]:5.2f}", flush=True)
    return out


def main() -> None:
    print("Winrate pada protokol T-5 (entry tick pertama candle B, hasil close B vs entry):\n")

    # A. curang: lihat close candle B
    run("A. curang lihat close B", lambda T: np.where(T["ok"], np.sign(T["closeb"] - T["entry"]), 0.0))

    # B. curang tetapi digeser 1 candle (bug yang paling sering terjadi di backtest)
    run("B. curang geser 1 candle", lambda T: np.where(
        T["ok"], np.concatenate([[0.0], np.sign(T["closeb"] - T["entry"])[:-1]]), 0.0))

    # C. SMC dengan satu kebocoran: pakai high/low candle B yang belum terjadi
    def smc_bocor(T: dict) -> np.ndarray:
        S = smc(T, 2, 60)
        R = rules(S)
        base = R["R5_sweep"]
        bocor = np.sign(T["closeb"] - T["entry"])          # kebocoran: arah masa depan
        return np.where(base != 0, bocor, 0.0)             # hanya "menyaring" sinyal SMC

    run("C. SMC + kebocoran", smc_bocor)

    # D. SMC jujur, tanpa kebocoran
    def smc_jujur(T: dict) -> np.ndarray:
        S = smc(T, 2, 60)
        return rules(S)["R5_sweep"]

    run("D. SMC jujur (sweep)", smc_jujur)

    print("\nKesimpulan mekanis: baris A mencapai 100% karena membaca close candle B.")
    print("Baris C juga tinggi untuk alasan yang sama — kebocorannya, bukan SMC-nya.")
    print("Hapus kebocoran -> kembali ~50%. Tidak ada aturan yang bisa menghasilkan 100%")
    print("pada data ini tanpa membaca masa depan, karena labelnya sendiri yang dibaca.")


if __name__ == "__main__":
    main()
