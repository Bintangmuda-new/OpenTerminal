"""band_uji.py — kompromi frekuensi vs winrate pada lebar zona OTE.

Grade A/B hanya ~153 sinyal/hari di 142 simbol, jadi satu simbol bisa menunggu
lama. Uji apakah zona OTE yang lebih lebar menambah frekuensi tanpa
menghancurkan edge.
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


def load_panel():
    """Bangun ulang sinyal dengan ambang OTE yang bisa diubah."""
    tabs = H.load_tables()
    day0 = H._day_index(tabs)
    rows = []
    for sym, T in tabs.items():
        o, h, l, c = T["o"], T["h"], T["l"], T["c"]
        t, y, ok = T["t"], T["y"], T["ok"]
        n = len(c)
        if n < 120:
            continue
        # ATR kasar
        pc = np.roll(c, 1); pc[0] = c[0]
        tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
        atr = np.convolve(tr, np.ones(14) / 14, mode="same")
        disp = T.get("disp", atr)
        unit = np.maximum(disp, 1e-9)

        # pivot 2
        P = 2
        sh = np.zeros(n, bool); sl = np.zeros(n, bool)
        for i in range(P, n - P):
            w_h = h[i - P:i + P + 1]; w_l = l[i - P:i + P + 1]
            sh[i] = h[i] >= w_h.max(); sl[i] = l[i] <= w_l.min()

        def last_before(mask, i):
            m = np.flatnonzero(mask[:i])
            return m[-1] if len(m) >= 1 else -1

        out = []
        for i in range(30, n - 2):
            a = i - 1  # candle A (closed) = indeks i-1, entry di open candle i
            if a < 10:
                continue
            # swing terakhir sebelum A
            shb = np.flatnonzero(sh[:a]); slb = np.flatnonzero(sl[:a])
            if len(shb) == 0 or len(slb) == 0:
                continue
            shi, sli = shb[-1], slb[-1]
            if shi <= sli:
                continue  # butuh high terakhir setelah low
            hi, lo = h[shi], l[sli]
            span = hi - lo
            if not (span > 0):
                continue
            retr = (hi - c[a]) / span
            # lower high = pivot high sebelumnya
            prev_sh = shb[:-1]
            if len(prev_sh) == 0:
                continue
            lh = h[prev_sh[-1]]
            choch = c[a] > lh
            if not choch:
                continue
            # FVG bullish yang tersentuh
            fvg = False
            for k in range(max(2, a - 5), a + 1):
                if l[k] > h[k - 2] and c[k - 1] > o[k - 1] and (l[k] - h[k - 2]) >= 0.25 * unit[a]:
                    if l[a] <= l[k] and c[a] > l[k]:
                        fvg = True
                        break
            out.append((retr, y[i] if ok[i] else 0, t[i], sym, fvg))
        rows.extend(out)
    return rows


def main():
    rows = load_panel()
    print(f"total sinyal CHoCH: {len(rows):,}")

    # hari
    t0 = min(r[2] for r in rows)
    arr = np.array([(r[0], r[1], (r[2] - t0) // H.DAY, 1 if r[4] else 0) for r in rows], dtype=float)
    retr, y, day, fvg = arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 3]
    seg = np.where(day >= LOCKBOX_FROM, "lock", np.where(day >= HOLDOUT_FROM, "hold", "train"))

    def wr(m):
        yy = y[m]; yy = yy[yy != 0]
        return (len(yy), float((yy > 0).mean() * 100) if len(yy) else 0.0)

    print("\n" + "=" * 88)
    print("LEBAR ZONA OTE: frekuensi vs winrate (semua CHoCH CALL)")
    print("=" * 88)
    print(f"{'zona':14} {'n':>7} {'WR':>7} {'WR_tr':>7} {'WR_ho':>7} {'WR_lk':>7} {'/hari':>7} {'EV92':>7}")
    print("-" * 88)
    bands = [(0.62, 0.79), (0.55, 0.85), (0.50, 0.88), (0.45, 0.90), (0.40, 0.92), (0.30, 0.95)]
    for lo, hi in bands:
        m = (retr >= lo) & (retr <= hi)
        n, w = wr(m)
        ws = [wr(m & (seg == s))[1] for s in ("train", "hold", "lock")]
        ev = (w / 100) * .92 - (1 - w / 100)
        print(f"{lo:.2f}-{hi:.2f}      {n:>7,} {w:>6.2f}% {ws[0]:>6.2f}% {ws[1]:>6.2f}% {ws[2]:>6.2f}% {n / 8:>7.0f} {ev * 100:>+6.2f}%")

    print("\n" + "=" * 88)
    print("SAMA, TAPI HANYA YANG PUNYA FVG (grade A/B)")
    print("=" * 88)
    print(f"{'zona':14} {'n':>7} {'WR':>7} {'WR_tr':>7} {'WR_ho':>7} {'WR_lk':>7} {'/hari':>7} {'EV92':>7}")
    print("-" * 88)
    for lo, hi in bands:
        m = (retr >= lo) & (retr <= hi) & (fvg == 1)
        n, w = wr(m)
        ws = [wr(m & (seg == s))[1] for s in ("train", "hold", "lock")]
        ev = (w / 100) * .92 - (1 - w / 100)
        print(f"{lo:.2f}-{hi:.2f}      {n:>7,} {w:>6.2f}% {ws[0]:>6.2f}% {ws[1]:>6.2f}% {ws[2]:>6.2f}% {n / 8:>7.0f} {ev * 100:>+6.2f}%")


if __name__ == "__main__":
    main()
