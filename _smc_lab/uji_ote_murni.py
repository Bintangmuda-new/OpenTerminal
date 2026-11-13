"""uji_ote_murni.py — apakah CHoCH menambah nilai di atas OTE, atau OTE saja cukup?

Ini pertanyaan paling penting: jika "beli di zona OTE" tanpa CHoCH menghasilkan
winrate yang sama, maka trigger CHoCH tidak berguna dan strategi kita sebenarnya
adalah mean-reversion sederhana.

Uji 4 sel:
  A. OTE saja (tanpa CHoCH)          -> baseline mean-reversion
  B. OTE + CHoCH (strategi kita)     -> apakah trigger menambah?
  C. CHoCH saja (tanpa OTE)          -> baseline struktur
  D. tidak keduanya                  -> kontrol

Semua memakai definisi OTE yang sama (retracement 62-79% dari leg terakhir),
dihitung dari candle yang sudah tertutup (tanpa lookahead).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

SERVER = Path(r"E:\JANGAN DI HAPUS\OpenTerminal\server")
sys.path.insert(0, str(SERVER))

import research_harness as H  # noqa: E402
from backtest24_smc import _rolling  # noqa: E402
from backtest26_smc_canon import OTE_HI, OTE_LO, _confirmed, _fractal  # noqa: E402

HOLDOUT_FROM = H.HOLDOUT_FROM_DAY


def wr(y: np.ndarray) -> tuple[int, float]:
    nz = y[y != 0]
    return (len(nz), float((nz > 0).mean() * 100) if len(nz) else 0.0)


def zones(T: dict, p: int = 2) -> dict[str, np.ndarray]:
    """Hitung OTE dan CHoCH terpisah, tanpa saling bergantung."""
    o, h, l, c = (T[k].astype(float) for k in ("o", "h", "l", "c"))
    c5 = T["c5"].astype(float)
    n = len(c)
    ix = np.arange(n)
    ph, pl = _fractal(h, l, p)
    sh_i, sh_p = _confirmed(ph, h, p)
    sl_i, sl_p = _confirmed(pl, l, p)
    atr = _rolling(h - l, 14, np.mean)
    cs = np.concatenate([[np.nan], c[:-1]])

    # CHoCH kanonik
    prev_sl = np.full(n, -1, dtype=int)
    v = sl_i >= 0
    if v.any():
        pref = np.maximum.accumulate(np.where(np.isin(ix, sl_i[v]), ix, -1))
        prev_sl[v] = pref[np.maximum(sl_i[v] - 1, 0)]
        prev_sl[v] = np.where(np.isin(prev_sl[v], sl_i[v]), prev_sl[v], -1)
    hl = np.zeros(n, bool)
    vh = (sl_i >= 0) & (prev_sl >= 0)
    hl[sl_i[vh]] = sl_p[vh] > l[np.maximum(prev_sl[vh], 0)]
    hl_seq = np.maximum.accumulate(np.where(hl, ix, -1))
    choch_dn = (hl_seq >= 0) & (cs < l[np.maximum(hl_seq, 0)])

    prev_sh = np.full(n, -1, dtype=int)
    v2 = sh_i >= 0
    if v2.any():
        prefh = np.maximum.accumulate(np.where(np.isin(ix, sh_i[v2]), ix, -1))
        prev_sh[v2] = prefh[np.maximum(sh_i[v2] - 1, 0)]
        prev_sh[v2] = np.where(np.isin(prev_sh[v2], sh_i[v2]), prev_sh[v2], -1)
    lh = np.zeros(n, bool)
    vhh = (sh_i >= 0) & (prev_sh >= 0)
    lh[sh_i[vhh]] = sh_p[vhh] < h[np.maximum(prev_sh[vhh], 0)]
    lh_seq = np.maximum.accumulate(np.where(lh, ix, -1))
    choch_up = (lh_seq >= 0) & (cs > h[np.maximum(lh_seq, 0)])

    # OTE murni
    span = sh_p - sl_p
    okleg = np.isfinite(span) & (span > 0)
    den = np.where(okleg, span, 1.0)
    retr_hi = np.where(okleg, (sh_p - c5) / den, np.nan)
    retr_lo = np.where(okleg, (c5 - sl_p) / den, np.nan)
    ote_long = okleg & (retr_hi >= OTE_LO) & (retr_hi <= OTE_HI)
    ote_short = okleg & (retr_lo >= OTE_LO) & (retr_lo <= OTE_HI)
    return dict(choch_up=choch_up, choch_dn=choch_dn, ote_long=ote_long, ote_short=ote_short)


def main() -> None:
    print("memuat cache...", flush=True)
    tabs = H.load_tables()
    day0 = H._day_index(tabs)

    cells = {k: {"train": [0, 0], "hold": [0, 0]} for k in ("A_ote_only", "B_ote_choch", "C_choch_only", "D_neither")}

    for T in tabs.values():
        z = zones(T, 2)
        y = T["y"]
        ok = T["ok"]
        day = T["t"] // H.DAY - day0
        seg = np.where(day >= HOLDOUT_FROM, "hold", "train")
        for direction, ote_m, ch_m in ((1, z["ote_long"], z["choch_up"]), (-1, z["ote_short"], z["choch_dn"])):
            sets = {
                "A_ote_only": ote_m & ok,
                "B_ote_choch": ote_m & ch_m & ok,
                "C_choch_only": ch_m & ~ote_m & ok,
                "D_neither": ~ote_m & ~ch_m & ok,
            }
            for name, m in sets.items():
                for s in ("train", "hold"):
                    mm = m & (seg == s)
                    if not mm.any():
                        continue
                    yy = y[mm]
                    nz = yy != 0
                    cells[name][s][0] += int((yy[nz] == direction).sum())
                    cells[name][s][1] += int(nz.sum())

    print("\n" + "=" * 82)
    print("APAKAH CHoCH MENAMBAH NILAI DI ATAS OTE?  (CALL, p=2)")
    print("=" * 82)
    print(f"{'sel':14} {'n_train':>9} {'WR_train':>9} {'n_hold':>9} {'WR_hold':>9} {'lb95':>7}")
    for name in ("A_ote_only", "B_ote_choch", "C_choch_only", "D_neither"):
        tr, ho = cells[name]["train"], cells[name]["hold"]
        ntr, nho = tr[1], ho[1]
        wtr = tr[0] / ntr * 100 if ntr else 0
        who = ho[0] / nho * 100 if nho else 0
        lb = H.wilson_low(ho[0], nho) if nho else 0
        print(f"{name:14} {ntr:>9,} {wtr:>8.2f}% {nho:>9,} {who:>8.2f}% {lb:>6.2f}")

    print("\ncatatan: A = 'beli di zona OTE' TANPA trigger CHoCH")
    print("         B = strategi kita (OTE + CHoCH)")
    print("         jika A ~= B, trigger CHoCH tidak menambah nilai")


if __name__ == "__main__":
    main()
