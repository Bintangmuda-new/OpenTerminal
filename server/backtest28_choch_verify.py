"""backtest28_choch_verify.py — verifikasi keras CHoCH kanonik (satu-satunya kandidat SMC).

bt26 menemukan K1_choch_canon: lockbox 51.25% n=96.494 lb95=50.93 d=+3.54pt. Karena aturan
itu dipilih dari 14 aturan x 4 param, angka itu WAJIB diuji terhadap pemilihan ganda.
Skrip ini menjalankan:
  1. per hari (fold) — apakah keunggulan terkonsentrasi di satu hari saja?
  2. per simbol — berapa simbol yang positif?
  3. kontrol null (label diacak dalam simbol+hari) — berapa winrate yang bisa dicapai derau?
  4. kontrol arah (CALL terus / PUT terus pada baris sinyal yang sama)
  5. kurva keyakinan: apakah winrate naik saat sinyal makin jarang?
  6. bootstrap CI untuk d
"""
from __future__ import annotations

import numpy as np

import research_harness as H
from backtest24_smc import _rolling
from backtest26_smc_canon import smc_canon

P, DISP = 1, 1.5


def collect() -> tuple[list, dict]:
    """Baris sinyal CHoCH: (hari, simbol, arah, y, margin/ATR)."""
    tabs = H.load_tables()
    d0 = H._day_index(tabs)
    rows = []
    per_sym: dict[str, list[int]] = {}
    for name, T in tabs.items():
        dirs = smc_canon(T, P, DISP)["K1_choch_canon"]
        m = T["ok"] & (dirs != 0)
        if m.sum() == 0:
            continue
        atr = _rolling(T["h"] - T["l"], 14, np.mean)
        cs = np.concatenate([[np.nan], T["c"][:-1].astype(float)])
        span = np.maximum(T["h"] - T["l"], 1e-12)
        margin = np.where(np.isfinite(atr) & (atr > 0), np.abs(cs - T["c5"]) / np.where(atr > 0, atr, 1), np.nan)
        rows.append((T["t"][m] // H.DAY - d0, name, dirs[m].astype(np.int8),
                     T["y"][m].astype(np.int8), margin[m]))
        y = T["y"][m].astype(np.int8)
        per_sym[name] = [int((y == dirs[m]).sum()), int((y == -dirs[m]).sum()), int((y == 0).sum())]
    return rows, per_sym


def main() -> None:
    rows, per_sym = collect()
    day = np.concatenate([r[0] for r in rows])
    sym = np.concatenate([np.full(len(r[3]), r[1], dtype=object) for r in rows])
    pred = np.concatenate([r[2] for r in rows]).astype(float)
    y = np.concatenate([r[3] for r in rows]).astype(float)
    margin = np.concatenate([r[4] for r in rows])
    n = len(y)
    win = y == pred
    loss = y == -pred
    print(f"total sinyal CHoCH = {n:,} pada {len(per_sym)} simbol")
    print(f"\nwinrate {win.mean() * 100:.3f}%  (menang {int(win.sum()):,} / kalah {int(loss.sum()):,})  "
          f"d={(win.mean() - loss.mean()) * 100:+.3f}pt  lb95={H.wilson_low(int(win.sum()), n):.3f}")

    print("\n1) per hari (fold):")
    for dd in np.unique(day):
        m = day == dd
        if m.sum() < 100:
            continue
        print(f"   hari {int(dd)}: {win[m].mean() * 100:6.2f}% n={int(m.sum()):7d}")

    print("\n2) per simbol:")
    arr = sorted(((k, v[0] / max(sum(v), 1) * 100, sum(v)) for k, v in per_sym.items()),
                 key=lambda t: -t[1])
    arr = [a for a in arr if a[2] > 200]
    for k, wr, cnt in arr[:6]:
        print(f"   {k:16} {wr:6.2f}% n={cnt}")
    print("   ...")
    for k, wr, cnt in arr[-4:]:
        print(f"   {k:16} {wr:6.2f}% n={cnt}")
    print(f"   simbol winrate>50: {sum(1 for a in arr if a[1] > 50)}/{len(arr)}")

    print("\n3) kontrol null (label diacak dalam simbol+hari, 5 ulangan):")
    rng = np.random.default_rng(4)
    key = np.array([f"{a}|{b}" for a, b in zip(sym, day)])
    uniq = np.unique(key)
    for rep in range(5):
        yy = y.copy()
        for k in uniq:
            m = key == k
            yy[m] = rng.permutation(yy[m])
        print(f"   null #{rep + 1}: {(yy == pred).mean() * 100:6.2f}%")

    print("\n4) kontrol arah pada baris sinyal yang sama:")
    print(f"   CALL terus {(y == 1).mean() * 100:6.2f}%   PUT terus {(y == -1).mean() * 100:6.2f}%")

    print("\n5) kurva keyakinan (|close - harga T-5| / ATR):")
    ok = np.isfinite(margin)
    print(f"{'ambang':>8} {'n':>8} {'winrate':>8} {'lb95':>6} {'d(pt)':>7}")
    for thr in (0.0, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0):
        m = ok & (margin >= thr)
        if m.sum() < 200:
            continue
        w, l = int(win[m].sum()), int(loss[m].sum())
        nn = int(m.sum())
        print(f"{thr:8.2f} {nn:8d} {w / nn * 100:7.2f}% {H.wilson_low(w, nn):6.2f} "
              f"{(w - l) / nn * 100:+7.3f}")

    print("\n6) bootstrap CI untuk d (2000 ulangan):")
    wl = win.astype(np.int8) - loss.astype(np.int8)
    bs = np.array([wl[rng.integers(0, n, n)].mean() for _ in range(2000)]) * 100
    print(f"   d={wl.mean() * 100:+.3f}pt  CI95=[{np.percentile(bs, 2.5):+.3f},{np.percentile(bs, 97.5):+.3f}]  "
          f"P(d>0)={np.mean(bs > 0) * 100:.1f}%")


if __name__ == "__main__":
    main()
