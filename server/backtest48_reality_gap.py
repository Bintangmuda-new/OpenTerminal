"""backtest48_reality_gap.py — ukur jurang antara angka backtest dan kenyataan trade.

Tujuan: bukan mencari 70% (sudah terbukti tidak ada), tapi mengukur dua hal
yang menentukan P&L nyata:

  1. Edge level-tick vs level-candle.
     Backtest lama pakai outcome candle-close (naik/turun). Broker bayar
     berdasarkan tick terakhir vs tick entry. Selisih keduanya = jam buram
     liar yang bikin angka backtest tidak bisa dipakai buat trade.

  2. Frekuensi pseudo-tie di OTC.
     Kalau broker melakukan ROUNDING harga sebelum settle, tick terakhir
     sering SAMA PERSIS dengan tick entry -> trade jadi SERI. Kalau
     backtest membuang tie dari penyebut, winrate backtest otomatis
     lebih tinggi dari winrate akun nyata.

Protokol:
  data <= waktu keputusan (causal, fitur lag-1 candle)
  entry   = tick pertama setelah entry_time
  exit    = tick terakhir <= entry_time + H
  tie     = exit_price == entry_price  (DILAPORKAN, bukan dibuang)

Output: tabel per (symbol, offset, H) berisi
  n, tie%, up%, candle_win%, tick_win%, gap
"""

from __future__ import annotations

import glob
import os
import sys
import time

import numpy as np

DAY = 86400.0
DAY_LO = 20709
DAY_HI = 20716

SYMBOLS = {
    "EURUSD_otc": "EURUSD_otc.npz",
    "AUDCAD_otc": "AUDCAD_otc.npz",
    "USDCAD_otc": "USDCAD_otc.npz",
    "XAUUSD_otc": "XAUUSD_otc.npz",
    "BTCUSD_otc": "BTCUSD_otc.npz",
}

OFFSETS = (10, 25, 40, 55)
HORIZONS = (15, 30, 60, 120)

CACHE_DIRS = [r"E:\otc_cache\ticks", r"E:\JANGAN DI HAPUS\OpenTerminal\data\ticks"]


def load_symbol(tag: str):
    """Kumpulkan semua tick untuk satu simbol dari cache NPZ."""
    t_all, p_all = [], []
    for d in CACHE_DIRS:
        if not os.path.isdir(d):
            continue
        fp = os.path.join(d, tag)
        if not os.path.isfile(fp):
            continue
        for fp in [fp]:
            try:
                z = np.load(fp)
                t = np.asarray(z["t"], dtype=np.float64)
                p = np.asarray(z["p"], dtype=np.float64)
            except Exception as exc:  # noqa: BLE001
                print("  skip %s: %s" % (os.path.basename(fp), exc))
                continue
            if t.size < 100:
                continue
            day = np.floor(t / DAY).astype(np.int64)
            keep = (day >= DAY_LO) & (day <= DAY_HI)
            if not keep.any():
                continue
            t_all.append(t[keep])
            p_all.append(p[keep])
    if not t_all:
        return None, None
    t = np.concatenate(t_all)
    p = np.concatenate(p_all)
    order = np.argsort(t, kind="stable")
    t = t[order]
    p = p[order]
    # buang duplikat timestamp (tick ganda saat reconnect)
    uniq = np.concatenate(([True], np.diff(t) > 0.0))
    return t[uniq], p[uniq]


def build_candles(t: np.ndarray, p: np.ndarray):
    """Candle 60 detik dari tick. Return (t_open, o, h, l, c)."""
    mi = np.floor(t / 60.0).astype(np.int64)
    uniq, start = np.unique(mi, return_index=True)
    n = uniq.size
    if n < 10:
        return None
    o = np.empty(n)
    h = np.empty(n)
    l = np.empty(n)
    c = np.empty(n)
    ends = np.append(start[1:], mi.size)
    for k in range(n):
        s, e = start[k], ends[k]
        seg = p[s:e]
        o[k] = seg[0]
        h[k] = seg.max()
        l[k] = seg.min()
        c[k] = seg[-1]
    return uniq.astype(np.float64) * 60.0, o, h, l, c


def outcome_at(t: np.ndarray, p: np.ndarray, entry_t: float, horizon: float):
    """Return (entry_price, exit_price) atau None kalau tick kurang."""
    i0 = np.searchsorted(t, entry_t, side="left")
    if i0 >= t.size:
        return None
    i1 = np.searchsorted(t, entry_t + horizon, side="right") - 1
    if i1 <= i0:
        return None
    return p[i0], p[i1]


def run_symbol(name: str, tag: str):
    t, p = load_symbol(tag)
    if t is None:
        print("%-12s NO DATA" % name)
        return
    cd = build_candles(t, p)
    if cd is None:
        print("%-12s CANDLE TOO FEW" % name)
        return
    ct, co, ch, cl, cc = cd
    day = np.floor(ct / DAY).astype(np.int64)
    rows = []
    for off in OFFSETS:
        dec_t = ct + off  # detik keputusan di dalam candle (data terkunci ke candle lalu)
        for H in HORIZONS:
            n = 0
            tie = 0
            up_tick = 0
            up_candle = 0
            for k in range(1, ct.size - 1):
                d = day[k]
                if d < DAY_LO or d > DAY_HI:
                    continue
                et = dec_t[k]
                # entry = tick pertama setelah waktu keputusan
                res = outcome_at(t, p, et, float(H))
                if res is None:
                    continue
                ep, xp = res
                # candle penutup: candle yang memuat exit secara kira-kira
                # pakai candle dengan close terdekat <= exit window
                i_exit = np.searchsorted(ct, et + H, side="right") - 1
                if i_exit < 0 or i_exit >= cc.size:
                    continue
                c_close_prev = cc[k - 1]
                n += 1
                if xp == ep:
                    tie += 1
                elif xp > ep:
                    up_tick += 1
                if cc[i_exit] > c_close_prev:
                    up_candle += 1
            if n == 0:
                continue
            tie_pct = 100.0 * tie / n
            tick_win = 100.0 * up_tick / n
            candle_win = 100.0 * up_candle / n
            rows.append((off, H, n, tie_pct, tick_win, candle_win, candle_win - tick_win))
    print("\n=== %s  ticks=%d candles=%d days=%d-%d ===" % (
        name, t.size, ct.size, int(day.min()), int(day.max())))
    print("%4s %5s %8s %7s %9s %11s %8s" % (
        "off", "H", "n", "tie%", "tick_up%", "candle_up%", "gap"))
    for off, H, n, tie_pct, tw, cw, gap in rows:
        print("%4d %5d %8d %6.2f%% %8.2f%% %10.2f%% %+7.2f" % (
            off, H, n, tie_pct, tw, cw, gap))


def main():
    t0 = time.time()
    print("backtest48_reality_gap — level-tick vs level-candle, tie report")
    for name, tag in SYMBOLS.items():
        run_symbol(name, tag)
    print("\ndone in %.1fs" % (time.time() - t0))


if __name__ == "__main__":
    sys.exit(main())
