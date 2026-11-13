"""grid_aturan.py — cari kombinasi gate SMC/ICT dengan winrate tertinggi.

Disiplin anti-overfit:
  * SEMUA pemilihan dilakukan di TRAIN (hari 0-5).
  * Angka yang dilaporkan untuk kandidat teratas selalu HOLDOUT (hari 6).
  * Wajib lolos: n_holdout >= 300, lb95 > 50, dan winrate train & holdout searah.
"""
from __future__ import annotations

import itertools
import sys
from pathlib import Path

import numpy as np

SERVER = Path(r"E:\JANGAN DI HAPUS\OpenTerminal\server")
sys.path.insert(0, str(SERVER))

import research_harness as H  # noqa: E402

HOLDOUT_FROM = H.HOLDOUT_FROM_DAY
GATES = ("h1", "sweep", "ob", "fvg", "eqh", "ote", "dsp", "bos", "tier")
MIN_HOLD = 300


def load() -> dict[str, np.ndarray]:
    z = np.load(Path(__file__).with_name("cache_signals.npz"))
    return {k: z[k] for k in z.files}


def stats(y: np.ndarray) -> tuple[int, float]:
    """y: array outcome (+1/-1/0). Kembalikan (n_trade, winrate)."""
    nz = y[y != 0]
    if len(nz) == 0:
        return 0, 0.0
    return len(nz), float((nz > 0).mean() * 100)


def scan(d: dict[str, np.ndarray]) -> list[dict]:
    train = d["day"] < HOLDOUT_FROM
    hold = ~train
    results = []

    # kandidat: setiap kombinasi 0..3 gate, wajib minimal 1 gate
    combos = []
    for r in (1, 2, 3):
        combos.extend(itertools.combinations(GATES, r))

    # precompute mask gate per arah
    for p in (1, 2):
        for disp in (1.5, 2.0):
            sel_p = (d["p"] == p) & (d["disp"] == disp)
            for direction in (1, -1):
                base = sel_p & (d["dir"] == direction)
                if not base.any():
                    continue
                gm = {g: (d[g] == 1) for g in GATES}
                for combo in combos:
                    m = base.copy()
                    for g in combo:
                        m &= gm[g]
                    if not m.any():
                        continue
                    mt = m & train
                    mh = m & hold
                    nt, wt = stats(d["y"][mt])
                    nh, wh = stats(d["y"][mh])
                    if nt < 200 or nh < MIN_HOLD:
                        continue
                    results.append(dict(
                        p=p, disp=disp, direction="CALL" if direction == 1 else "PUT",
                        combo="+".join(combo), n_gates=len(combo),
                        n_train=nt, wr_train=wt, n_hold=nh, wr_hold=wh,
                        lb=H.wilson_low(int(round(wh / 100 * nh)), nh),
                        gap=wh - wt,
                    ))
    return results


def report(results: list[dict]) -> None:
    # ---- kandidat terbaik menurut TRAIN (bukan holdout) ----
    ranked = sorted(results, key=lambda r: -r["wr_train"])
    print(f"total kombinasi dievaluasi: {len(results):,}\n")

    print("=" * 100)
    print("TOP 20 menurut TRAIN — angka holdout ditampilkan sebagai ujian (bukan untuk memilih)")
    print("=" * 100)
    print(f"{'dir':4} {'p':>2} {'disp':>4} {'gate':40} {'n_tr':>7} {'WR_tr':>7} {'n_ho':>6} {'WR_ho':>7} {'lb95':>6}")
    for r in ranked[:20]:
        print(f"{r['direction']:4} {r['p']:>2} {r['disp']:>4} {r['combo']:40} "
              f"{r['n_train']:>7,} {r['wr_train']:>6.2f}% {r['n_hold']:>6,} {r['wr_hold']:>6.2f}% {r['lb']:>6.2f}")

    print("\n" + "=" * 100)
    print("TOP 20 menurut HOLDOUT (yang benar-benar bertahan di data baru)")
    print("=" * 100)
    print(f"{'dir':4} {'p':>2} {'disp':>4} {'gate':40} {'n_tr':>7} {'WR_tr':>7} {'n_ho':>6} {'WR_ho':>7} {'lb95':>6}")
    for r in sorted(results, key=lambda r: -r["wr_hold"])[:20]:
        print(f"{r['direction']:4} {r['p']:>2} {r['disp']:>4} {r['combo']:40} "
              f"{r['n_train']:>7,} {r['wr_train']:>6.2f}% {r['n_hold']:>6,} {r['wr_hold']:>6.2f}% {r['lb']:>6.2f}")

    # ---- yang lolos semua syarat ----
    passed = [r for r in results if r["lb"] > 50 and r["wr_train"] > 50 and r["wr_hold"] > 50]
    print("\n" + "=" * 100)
    print(f"LOLOS SEMUA SYARAT (train>50, hold>50, lb95>50, n_hold>={MIN_HOLD}): {len(passed)}")
    print("=" * 100)
    for r in sorted(passed, key=lambda r: -r["lb"])[:25]:
        print(f"{r['direction']:4} p={r['p']} disp={r['disp']} {r['combo']:40} "
              f"n={r['n_hold']:>6,} WR_ho={r['wr_hold']:>6.2f}% lb95={r['lb']:>6.2f} (train {r['wr_train']:.2f}%)")

    out = Path(__file__).with_name("hasil_grid.csv")
    with out.open("w", encoding="utf-8") as f:
        f.write("direction,p,disp,combo,n_gates,n_train,wr_train,n_hold,wr_hold,lb95,gap\n")
        for r in sorted(results, key=lambda r: -r["lb"]):
            f.write(f"{r['direction']},{r['p']},{r['disp']},{r['combo']},{r['n_gates']},"
                    f"{r['n_train']},{r['wr_train']:.3f},{r['n_hold']},{r['wr_hold']:.3f},"
                    f"{r['lb']:.3f},{r['gap']:.3f}\n")
    print(f"\nCSV lengkap: {out}")


def main() -> None:
    d = load()
    print(f"sinyal dimuat: {len(d['y']):,}  simbol={len(np.unique(d['sym']))}", flush=True)
    print(f"hari: {sorted(np.unique(d['day']))}", flush=True)
    res = scan(d)
    report(res)


if __name__ == "__main__":
    main()
