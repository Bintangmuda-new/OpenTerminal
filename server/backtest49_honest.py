"""backtest49_honest.py — uji jujur: berapa winrate yang realistis?

Beda dari backtest47:
  * Tidak memilih ambang dari train. Ambang jarang dipakai; angka dilaporkan
    apa adanya per hari, supaya kelihatan variasi antar hari.
  * Tidak membuang tie dari penyebut; tie dilaporkan terpisah.
  * Memakai seluruh 8 hari cache, bukan cuma 3 hari file.
  * Fokus pada pertanyaan sebenarnya: "kalau saya trading setiap sinyal
    tanpa filter, apa hasilnya?" lalu "kalau difilter, apakah tetap sama
    di hari berikutnya?"

Output: tabel per simbol per hari, plus ringkasan gabungan.
"""

from __future__ import annotations

import glob
import os
import time

import numpy as np

DAY = 86400.0
DAY_LO = 20709
DAY_HI = 20716

# simbol cair / sering dipakai user
SYMS = [
    "EURUSD_otc", "AUDCAD_otc", "USDCAD_otc", "AUDUSD_otc",
    "USDCHF_otc", "EURJPY_otc", "GBPUSD_otc", "XAUUSD_otc",
    "#AAPL_otc", "#TSLA_otc",
]

OFFSETS = (10, 25, 40, 55)
HORIZON = 60          # detik (1 menit, sesuai kontrak OTC 1M)

TICKS_DIR = r"E:\otc_cache\ticks"


def load(sym: str):
    fp = os.path.join(TICKS_DIR, sym + ".npz")
    if not os.path.isfile(fp):
        return None
    z = np.load(fp)
    t = np.asarray(z["t"], dtype=np.float64)
    p = np.asarray(z["p"], dtype=np.float64)
    if t.size < 5000:
        return None
    o = np.argsort(t, kind="stable")
    t, p = t[o], p[o]
    keep = np.append(True, np.diff(t) > 0)
    return t[keep], p[keep]


def trade(t, p, off, H):
    """Satu trade tiap candle menit. Return (day_array, up, tie) mask arrays."""
    mi = np.floor(t / 60.0).astype(np.int64)
    # waktu keputusan = awal menit + off
    dec = mi * 60.0 + off
    dec = dec.astype(np.float64)
    i0 = np.searchsorted(t, dec, side="left")
    i1 = np.searchsorted(t, dec + H, side="right") - 1
    ok = (i0 < t.size) & (i1 > i0) & (i1 < t.size)
    i0, i1 = i0[ok], i1[ok]
    if i0.size == 0:
        return None
    # satu trade per menit: ambil yang unik per menit
    mins = mi[ok]
    uniq = np.append(True, np.diff(mins) > 0)
    i0, i1 = i0[uniq], i1[uniq]
    mins = mins[uniq]
    ep, xp = p[i0], p[i1]
    day = np.floor((mins * 60.0) / DAY).astype(np.int64)
    up = xp > ep
    tie = xp == ep
    return day, up, tie


def main():
    t0 = time.time()
    print("backtest49_honest — winrate tanpa pemilihan ambang, per hari, 8 hari penuh")
    print("horizon=%ds, entry=tick pertama setelah menit+off\n" % HORIZON)

    grand_up = 0
    grand_n = 0
    grand_tie = 0

    for sym in SYMS:
        got = load(sym)
        if got is None:
            print("%-14s NO DATA" % sym)
            continue
        t, p = got
        print("=== %s (ticks=%d) ===" % (sym, t.size))
        print("  %4s %5s %8s %8s %7s %9s" % ("off", "H", "n", "up%", "tie%", "hari"))
        for off in OFFSETS:
            res = trade(t, p, off, HORIZON)
            if res is None:
                continue
            day, up, tie = res
            days = np.unique(day)
            # aggregated
            n_all = up.size
            up_all = int(up.sum())
            tie_all = int(tie.sum())
            denom = n_all - tie_all
            wr = 100.0 * up_all / denom if denom else 0.0
            # per-hari winrate (untuk cek stabilitas)
            daily = []
            for d in days:
                m = day == d
                nn = int(m.sum())
                tt = int(tie[m].sum())
                dd = nn - tt
                if dd < 50:
                    continue
                daily.append(100.0 * int(up[m].sum()) / dd)
            spread = ("%.1f..%.1f" % (min(daily), max(daily))) if daily else "-"
            print("  %4d %5d %8d %7.2f%% %6.2f%% %9s" % (
                off, HORIZON, denom, wr, 100.0 * tie_all / n_all, spread))
            if off == 25:
                grand_up += up_all
                grand_n += denom
                grand_tie += tie_all
        print()

    if grand_n:
        print("GABUNGAN (off=25, H=%d): n=%d up=%.2f%% tie=%.2f%% -> winrate %.2f%%" % (
            HORIZON, grand_n, 100.0 * grand_up / grand_n,
            100.0 * grand_tie / (grand_n + grand_tie),
            100.0 * grand_up / grand_n))
    print("\ndone in %.1fs" % (time.time() - t0))


if __name__ == "__main__":
    main()
