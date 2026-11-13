"""banding_choch.py — buktikan apakah CHoCH di aplikasi (C#) = CHoCH kanonik (Python).

Dua perbedaan yang dicurigai:
  A. level bullish: C# memakai `lastSth` (swing high SEBELUMNYA, lebih tinggi)
     sedangkan kanonik memakai pivotHigh itu sendiri (lower high terbaru).
  B. level bearish: C# memakai `pivotLow` (higher low terbaru) = kanonik. Simetris? dicek.

Skrip ini menjalankan dua implementasi pada data yang sama dan membandingkan
jumlah sinyal + winrate per arah.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

SERVER = Path(r"E:\JANGAN DI HAPUS\OpenTerminal\server")
sys.path.insert(0, str(SERVER))

import research_harness as H  # noqa: E402

DAY = H.DAY
HOLDOUT_FROM = H.HOLDOUT_FROM_DAY


def choch_csharp_like(h: np.ndarray, l: np.ndarray, c: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Replika 1:1 loop C# AnalyzeSmcFull (MainWindow.xaml.cs:1508-1536).

    Asimetri yang diuji: `lowerHigh = lastSth` vs `higherLow = pivotLow`.
    """
    n = len(c)
    up = np.zeros(n, bool)   # bullish CHoCH -> CALL
    dn = np.zeros(n, bool)   # bearish CHoCH -> PUT
    last_stl = np.nan
    last_sth = np.nan
    higher_low = np.nan
    lower_high = np.nan
    for index in range(1, n):
        pivot = index - 1
        if pivot >= 1:
            ph = h[pivot]
            pl = l[pivot]
            if ph > h[pivot - 1] and ph > h[index]:
                if not np.isnan(last_sth) and ph < last_sth:
                    lower_high = last_sth          # <-- perilaku C# apa adanya
                last_sth = ph
            if pl < l[pivot - 1] and pl < l[index]:
                if not np.isnan(last_stl) and pl > last_stl:
                    higher_low = pl                # <-- perilaku C#
                last_stl = pl
        close_a = c[index]
        if not np.isnan(higher_low) and close_a < higher_low:
            dn[index] = True
        elif not np.isnan(lower_high) and close_a > lower_high:
            up[index] = True
    return up, dn


def choch_canonical(h: np.ndarray, l: np.ndarray, c: np.ndarray, p: int = 1) -> tuple[np.ndarray, np.ndarray]:
    """Kanonik backtest26: level = harga swing yang membentuk higher-low/lower-high itu sendiri."""
    n = len(c)
    sth = np.zeros(n, bool)
    stl = np.zeros(n, bool)
    if n > 2 * p:
        for j in range(p, n - p):
            if h[j] >= h[j - p:j + p + 1].max():
                sth[j] = True
            if l[j] <= l[j - p:j + p + 1].min():
                stl[j] = True
    # konfirmasi: flag di j baru berlaku di j+p
    sh_i = np.array([j + p if sth[j] else -1 for j in range(n)])
    sl_i = np.array([j + p if stl[j] else -1 for j in range(n)])

    hl_flag = np.zeros(n, bool)
    prev_sl = -1
    for i in range(n):
        if sl_i[i] >= 0:
            if prev_sl >= 0 and l[sl_i[i]] > l[prev_sl]:
                hl_flag[sl_i[i]] = True
            prev_sl = sl_i[i]
    lh_flag = np.zeros(n, bool)
    prev_sh = -1
    for i in range(n):
        if sh_i[i] >= 0:
            if prev_sh >= 0 and h[sh_i[i]] < h[prev_sh]:
                lh_flag[sh_i[i]] = True
            prev_sh = sh_i[i]

    hl_seq = np.maximum.accumulate(np.where(hl_flag, np.arange(n), -1))
    lh_seq = np.maximum.accumulate(np.where(lh_flag, np.arange(n), -1))
    dn = (hl_seq >= 0) & (c < l[np.maximum(hl_seq, 0)])
    up = (lh_seq >= 0) & (c > h[np.maximum(lh_seq, 0)])
    return up, dn


def score(tabs: dict, fn) -> dict:
    """Hitung n & winrate per arah, train/holdout, pakai konvensi harness."""
    day0 = H._day_index(tabs)
    out = {"train": {"CALL": [0, 0], "PUT": [0, 0]}, "holdout": {"CALL": [0, 0], "PUT": [0, 0]}}
    for T in tabs.values():
        ok = T["ok"]
        y = T["y"]
        o, h, l, c = (T[k].astype(float) for k in ("o", "h", "l", "c"))
        up, dn = fn(h, l, c)
        day = T["t"] // DAY - day0
        seg = np.where(day >= HOLDOUT_FROM, "holdout", "train")
        for name, mask, direction in (("CALL", up, 1), ("PUT", dn, -1)):
            for s in ("train", "holdout"):
                m = mask & ok & (seg == s)
                if not m.any():
                    continue
                yy = y[m].astype(float)
                win = int((yy == direction).sum())
                loss = int(((yy != direction) & (yy != 0)).sum())
                out[s][name][0] += win
                out[s][name][1] += win + loss
    return out


def show(label: str, res: dict) -> None:
    print(f"\n{label}")
    for s in ("train", "holdout"):
        for d in ("CALL", "PUT"):
            w, n = res[s][d]
            wr = w / n * 100 if n else 0.0
            print(f"  {s:8s} {d:4s} n={n:>8,}  WR={wr:6.2f}%")


def main() -> None:
    print("memuat cache...", flush=True)
    tabs = H.load_tables()
    print(f"simbol={len(tabs)}", flush=True)

    show("A. CHoCH seperti aplikasi (C# apa adanya)", score(tabs, choch_csharp_like))
    show("B. CHoCH kanonik (backtest26)", score(tabs, choch_canonical))

    # C: perbaiki hanya sisi bullish (lowerHigh = pivotHigh), bearish tetap
    def fixed(h, l, c):
        n = len(c)
        up = np.zeros(n, bool)
        dn = np.zeros(n, bool)
        last_stl = last_sth = np.nan
        higher_low = lower_high = np.nan
        for index in range(1, n):
            pivot = index - 1
            if pivot >= 1:
                ph, pl = h[pivot], l[pivot]
                if ph > h[pivot - 1] and ph > h[index]:
                    if not np.isnan(last_sth) and ph < last_sth:
                        lower_high = ph            # <-- diperbaiki
                    last_sth = ph
                if pl < l[pivot - 1] and pl < l[index]:
                    if not np.isnan(last_stl) and pl > last_stl:
                        higher_low = pl
                    last_stl = pl
            close_a = c[index]
            if not np.isnan(higher_low) and close_a < higher_low:
                dn[index] = True
            elif not np.isnan(lower_high) and close_a > lower_high:
                up[index] = True
        return up, dn

    show("C. CHoCH diperbaiki (lowerHigh = pivotHigh, simetris)", score(tabs, fixed))


if __name__ == "__main__":
    main()
