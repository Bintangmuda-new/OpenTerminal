"""backtest19_crosssymbol.py — apakah kondisi T-5 SATU simbol memprediksi arah candle B simbol LAIN?

Hipotesis reverse-engineering: bila semua simbol dihasilkan loop broker yang sama
(dengan offset waktu atau driver bersama), fitur T-5 simbol X bisa mendahului arah
candle B simbol Y. backtest11 menguji ini di agregat M1 (14.762 pasangan, nol >=55%);
ini mengujinya di level T-5 dengan tabel tick, termasuk pasangan OTC vs non-OTC.

Peringkat pasangan dipilih di TRAIN; angka dilaporkan di HOLDOUT (lockbox tidak disentuh).
"""
from __future__ import annotations

import numpy as np

import research_harness as H
from backtest16_reverse import feats

TOP_X = 30          # simbol "pemandu" (paling lengkap) yang diuji sebagai prediktor
MIN_TRAIN = 500


def build_axis(tabs: dict):
    """Sumbu waktu gabungan + posisi tiap simbol di sumbu itu."""
    axis = np.unique(np.concatenate([T["t"] for T in tabs.values()]))
    pos = {s: np.searchsorted(axis, T["t"]) for s, T in tabs.items()}
    featset = {s: {k: (np.asarray(v, float), T["ok"], T["y"]) for k, v in feats(T).items()} for s, T in tabs.items()}
    return axis, pos, featset


def align(axis, pos, s, featset, key):
    v, ok, y = featset[s][key]
    out_v = np.full(len(axis), np.nan)
    out_y = np.full(len(axis), np.nan)
    out_v[pos[s]] = np.where(ok, v, np.nan)
    out_y[pos[s]] = np.where(ok, y, np.nan)
    return out_v, out_y


def main() -> None:
    tabs = H.load_tables()
    axis, pos, featset = build_axis(tabs)
    d0 = H._day_index(tabs)
    day = axis // H.DAY - d0
    tr = day < H.HOLDOUT_FROM_DAY
    ho = (day >= H.HOLDOUT_FROM_DAY) & (day < H.LOCKBOX_FROM_DAY)
    guides = sorted(tabs, key=lambda s: -int(tabs[s]["ok"].sum()))[:TOP_X]
    print(f"sumbu={len(axis):,} pemandu={len(guides)} target={len(tabs)}")

    rows = []
    for g in guides:
        for key in ("z", "pos", "body5", "gap", "volhi"):
            xv, _ = align(axis, pos, g, featset, key)
            if np.isfinite(xv[tr]).sum() < MIN_TRAIN:
                continue
            for tgt in tabs:
                if tgt == g:
                    continue
                _, yv = align(axis, pos, tgt, featset, key)
                m = np.isfinite(xv) & np.isfinite(yv) & (yv != 0)
                if (m & tr).sum() < MIN_TRAIN or (m & ho).sum() < 200:
                    continue
                for mode in ("follow", "revert"):
                    sgn = 1.0 if mode == "follow" else -1.0
                    for q in (0.2, 0.35, 0.5):
                        th = np.nanquantile(np.abs(xv[m & tr]), q)
                        pick = m & tr & (np.abs(xv) >= th)
                        if pick.sum() < MIN_TRAIN:
                            continue
                        rows.append(((np.sign(xv[pick]) * sgn == yv[pick]).mean(), g, key, mode, q, tgt))
    rows.sort(reverse=True)
    print(f"pasangan diuji: {len(rows):,} (top-20 train ditampilkan)")
    print(f"{'train%':>7} {'pemandu':16} {'fitur':7} {'mode':7} {'q':>4} {'target':16} {'HOLD%':>7} {'n':>7} {'lb95':>6}")
    allho = []
    for w, g, key, mode, q, tgt in rows[:20]:
        xv, _ = align(axis, pos, g, featset, key)
        _, yv = align(axis, pos, tgt, featset, key)
        m = np.isfinite(xv) & np.isfinite(yv) & (yv != 0)
        th = np.nanquantile(np.abs(xv[m & tr]), q)
        sgn = 1.0 if mode == "follow" else -1.0
        pick = m & ho & (np.abs(xv) >= th)
        n = int(pick.sum())
        w2 = int((np.sign(xv[pick]) * sgn == yv[pick]).sum())
        print(f"{w * 100:7.2f} {g:16} {key:7} {mode:7} {q:4.2f} {tgt:16} {w2 / max(n, 1) * 100:7.2f} {n:7d} {H.wilson_low(w2, n):6.2f}")
    for w, g, key, mode, q, tgt in rows:
        xv, _ = align(axis, pos, g, featset, key)
        _, yv = align(axis, pos, tgt, featset, key)
        m = np.isfinite(xv) & np.isfinite(yv) & (yv != 0)
        th = np.nanquantile(np.abs(xv[m & tr]), q)
        sgn = 1.0 if mode == "follow" else -1.0
        pick = m & ho & (np.abs(xv) >= th)
        if pick.sum() >= 200:
            allho.append((np.sign(xv[pick]) * sgn == yv[pick]).mean() * 100)
    a = np.array(allho)
    print(f"\nHOLDOUT semua pasangan: n={len(a)} median={np.median(a):.2f}% p95={np.percentile(a, 95):.2f}% "
          f"max={a.max():.2f}% | di atas 51%: {(a > 51).mean() * 100:.2f}%")


if __name__ == "__main__":
    main()
