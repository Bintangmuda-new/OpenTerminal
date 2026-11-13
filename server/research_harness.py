"""research_harness.py — satu protokol evaluasi untuk semua riset strategi (backtest16+).

Realita eksekusi yang dimodelkan (aturan permanen pemilik):
  * candle A = menit i. Keputusan diambil pada T-5 detik: hanya tick dengan
    offset <= 55 s di menit i yang boleh dipakai, plus semua menit < i.
  * entry = tick pertama menit i+1 (harga saat order dibuka di awal candle B).
  * hasil = tick terakhir menit i+1 (close B) vs entry. Sama = DRAW (tidak dihitung
    menang, tetap dihitung sebagai trade bila count_draw_as_loss=True).

Tabel per simbol (build_tables, di-cache ke <cache>/min/<symbol>.npz):
  t      awal menit (epoch detik)
  o,h,l,c    OHLC penuh menit i (HANYA boleh dipakai untuk menit < i)
  o5,h5,l5,c5,n5   OHLC + jumlah tick yang terlihat sampai T-5 di menit i
  n      jumlah tick penuh menit i
  entry, closeb   entry & close candle B (menit i+1)  -> LABEL, jangan jadi fitur
  y      +1 / -1 / 0 = arah closeb vs entry
  ok     baris valid (menit i+1 berurutan & cukup tick)

Pemakaian:
  from research_harness import load_tables, evaluate
  tabs = load_tables()                     # dict symbol -> dict array
  def rule(T): return np.where(..., 1, np.where(..., -1, 0))   # +1 CALL, -1 PUT
  evaluate("nama", rule, tabs)
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

CACHE = Path(r"E:\otc_cache")
DECIDE_AT = 5            # detik sebelum close candle A (aturan permanen T-5)
MIN_TICKS = 6
DAY = 86400
# Tiga segmen agar loop "sinyal salah -> perbaiki" tidak menghafal data:
#   train  (hari 0-5)  pencarian & fitting        (~126k menit)
#   holdout(hari 6)    validasi tiap iterasi      (~85k menit)
#   lockbox(hari 7)    dibuka SEKALI untuk vonis  (~140k menit)
HOLDOUT_FROM_DAY = 6
LOCKBOX_FROM_DAY = 7
OPEN_LOCKBOX = False     # set True hanya pada run vonis akhir


def _build_symbol(t: np.ndarray, p: np.ndarray) -> dict | None:
    if len(t) < 1000:
        return None
    m = (t // 60).astype(np.int64) * 60
    off = t - m
    uniq, start = np.unique(m, return_index=True)
    end = np.append(start[1:], len(t))
    k = len(uniq)
    o = p[start]
    c = p[end - 1]
    h = np.maximum.reduceat(p, start)
    l = np.minimum.reduceat(p, start)
    n = end - start
    vis = off <= 60 - DECIDE_AT
    # tick terlihat sampai T-5 per menit
    o5 = np.full(k, np.nan); h5 = o5.copy(); l5 = o5.copy(); c5 = o5.copy()
    n5 = np.zeros(k, dtype=np.int64)
    idx = np.searchsorted(uniq, m[vis])
    pv = p[vis]
    np.add.at(n5, idx, 1)
    h5[:] = -np.inf; l5[:] = np.inf
    np.maximum.at(h5, idx, pv); np.minimum.at(l5, idx, pv)
    first = np.unique(idx, return_index=True)
    o5[first[0]] = pv[first[1]]
    last_pos = np.r_[first[1][1:], len(idx)] - 1
    c5[first[0]] = pv[last_pos]
    h5[n5 == 0] = np.nan; l5[n5 == 0] = np.nan
    entry = np.append(o[1:], np.nan)
    closeb = np.append(c[1:], np.nan)
    nb = np.append(n[1:], 0)
    consec = np.append(np.diff(uniq) == 60, False)
    ok = consec & (n5 >= MIN_TICKS) & (nb >= 2)
    y = np.sign(np.nan_to_num(closeb - entry)).astype(np.int8)
    return dict(t=uniq.astype(np.int64), o=o, h=h, l=l, c=c, n=n,
                o5=o5, h5=h5, l5=l5, c5=c5, n5=n5,
                entry=entry, closeb=closeb, y=y, ok=ok)


def build_tables() -> None:
    src, dst = CACHE / "ticks", CACHE / "min"
    dst.mkdir(parents=True, exist_ok=True)
    for f in sorted(src.glob("*.npz")):
        out = dst / f.name
        if out.exists():
            continue
        z = np.load(f)
        tab = _build_symbol(z["t"], z["p"])
        if tab is not None:
            np.savez(out, **tab)
            print(f.stem, len(tab["t"]), flush=True)


def load_tables() -> dict[str, dict]:
    tabs = {}
    for f in sorted((CACHE / "min").glob("*.npz")):
        z = np.load(f)
        tabs[f.stem] = {k: z[k] for k in z.files}
    return tabs


def load_ticks(symbol: str) -> tuple[np.ndarray, np.ndarray]:
    z = np.load(CACHE / "ticks" / f"{symbol}.npz")
    return z["t"], z["p"]


def wilson_low(w: float, n: int, z: float = 1.96) -> float:
    if n == 0:
        return 0.0
    p = w / n
    d = 1 + z * z / n
    return ((p + z * z / (2 * n)) - z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)) / d * 100


def _day_index(tabs: dict) -> int:
    return min(int(T["t"][0]) // DAY for T in tabs.values())


def evaluate(name: str, rule, tabs: dict, cooldown: int = 0, quiet: bool = False) -> dict:
    """rule(T) -> array +1/-1/0 sepanjang T['t']. Draw dihitung sebagai bukan-menang."""
    d0 = _day_index(tabs)
    agg = {"train": [0, 0, 0], "holdout": [0, 0, 0]}   # win, loss, draw
    by_day = defaultdict(lambda: [0, 0])
    sym_hold = []
    for s, T in tabs.items():
        d = np.asarray(rule(T), dtype=float)
        d = np.where(T["ok"], d, 0.0)
        if cooldown:
            last = -10**9
            for i in np.flatnonzero(d):
                if i - last <= cooldown:
                    d[i] = 0
                else:
                    last = i
        idx = np.flatnonzero(d != 0)
        if len(idx) == 0:
            continue
        y = T["y"][idx].astype(float)
        win = (y == d[idx]); draw = (y == 0)
        day = T["t"][idx] // DAY - d0
        lock = day >= LOCKBOX_FROM_DAY
        hold = (day >= HOLDOUT_FROM_DAY) & ~lock
        segs = [("train", day < HOLDOUT_FROM_DAY), ("holdout", hold)]
        if OPEN_LOCKBOX:
            segs.append(("lockbox", lock))
        for seg, msk in segs:
            agg.setdefault(seg, [0, 0, 0])
            agg[seg][0] += int(win[msk].sum())
            agg[seg][2] += int(draw[msk].sum())
            agg[seg][1] += int((~win[msk] & ~draw[msk]).sum())
        for dd, w in zip(day[~lock], win[~lock]):
            by_day[int(dd)][0] += int(w); by_day[int(dd)][1] += 1
        nh = int(hold.sum())
        if nh >= 20:
            sym_hold.append(win[hold].mean() > 0.5)
    res = {"name": name}
    for seg, (w, l, dr) in agg.items():
        n = w + l + dr
        res[seg] = dict(n=n, wr=(w / n * 100 if n else 0.0), lb=wilson_low(w, n), draw=dr)
    folds = [v[0] / v[1] for _, v in sorted(by_day.items()) if v[1] >= 30]
    res["days_pos"] = f"{sum(r > 0.5 for r in folds)}/{len(folds)}"
    res["sym_pos"] = f"{sum(sym_hold)}/{len(sym_hold)}"
    if not quiet:
        tr, ho = res["train"], res["holdout"]
        lb = res.get("lockbox")
        extra = f" | LOCKBOX {lb['wr']:6.2f}% n={lb['n']} lb95={lb['lb']:5.2f}" if lb else ""
        print(f"{name:40} train {tr['wr']:6.2f}% n={tr['n']:7d} | HOLD {ho['wr']:6.2f}% "
              f"n={ho['n']:7d} lb95={ho['lb']:5.2f} draw={ho['draw']} days+ {res['days_pos']} sym+ {res['sym_pos']}{extra}",
              flush=True)
    return res


def _selftest() -> None:
    # Fitur T-5 tidak boleh melihat tick setelah offset 55 s.
    t = np.arange(0, 600, 1.0)
    p = np.arange(600, dtype=float)
    p[(t % 60) > 55] = 1e9
    tab = _build_symbol(np.tile(t, 2) + np.repeat([0, 600], 600), np.tile(p, 2))
    assert np.nanmax(tab["h5"]) < 1e9, "lookahead: T-5 melihat tick > 55 s"
    assert tab["entry"][0] == tab["o"][1] and tab["closeb"][0] == tab["c"][1]


if __name__ == "__main__":
    _selftest()
    if len(sys.argv) > 1 and sys.argv[1] == "build":
        build_tables()
