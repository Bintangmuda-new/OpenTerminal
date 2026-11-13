"""matriks_sinyal.py — bangun matriks fitur SMC/ICT untuk semua sinyal CHoCH.

Setiap baris = satu sinyal CHoCH pada satu candle A (keputusan T-5s, tanpa lookahead).
Kolom = gate SMC/ICT yang aktif searah dengan sinyal itu, plus hasil (y).

Output: _smc_lab/cache_signals.npz  (dipakai grid_aturan.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

SERVER = Path(r"E:\JANGAN DI HAPUS\OpenTerminal\server")
sys.path.insert(0, str(SERVER))

import research_harness as H  # noqa: E402
from backtest24_smc import _prefix_last, _rolling  # noqa: E402
from backtest26_smc_canon import (  # noqa: E402
    EQ_TOL, FVG_MIN_ATR, OTE_HI, OTE_LO, ROLL, SWEEP_BARS, W, _confirmed, _fractal, _tier,
)

DAY = H.DAY
HOLDOUT_FROM = H.HOLDOUT_FROM_DAY

GATE_NAMES = (
    "h1",        # bias H1 searah
    "sweep",     # sweep + reclaim likuiditas
    "ob",        # order block tersentuh
    "fvg",       # FVG tersentuh
    "eqh",       # EQH/EQL (likuiditas setara)
    "ote",       # OTE 62-79%
    "dsp",       # displacement candle sebelumnya
    "bos",       # BOS recent
    "tier",      # struktur bertingkat (LTH/LTL)
)


def features(T: dict, p: int, disp: float) -> dict[str, np.ndarray]:
    o, h, l, c = (T[k].astype(float) for k in ("o", "h", "l", "c"))
    o5, h5, l5, c5 = (T[k].astype(float) for k in ("o5", "h5", "l5", "c5"))
    n = len(c)
    ix = np.arange(n)

    ph, pl = _fractal(h, l, p)
    sh_i, sh_p = _confirmed(ph, h, p)
    sl_i, sl_p = _confirmed(pl, l, p)
    sth_flag = np.zeros(n, bool); sth_flag[sh_i[sh_i >= 0]] = True
    stl_flag = np.zeros(n, bool); stl_flag[sl_i[sl_i >= 0]] = True
    ith_i, ith_p = _tier(sth_flag, h, p, higher=True)
    itl_i, itl_p = _tier(stl_flag, l, p, higher=False)
    lth_flag = np.zeros(n, bool); lth_flag[ith_i[ith_i >= 0]] = True
    ltl_flag = np.zeros(n, bool); ltl_flag[itl_i[itl_i >= 0]] = True
    lth_i, lth_p = _tier(lth_flag, h, p, higher=True)
    ltl_i, ltl_p = _tier(ltl_flag, l, p, higher=False)

    rng = h - l
    atr = _rolling(rng, 14, np.mean)
    avg_body = _rolling(np.abs(c - o), 14, np.mean)
    body = np.abs(c - o)
    ok_atr = np.isfinite(atr) & (atr > 0)
    ok_body = np.isfinite(avg_body) & (avg_body > 0)
    dsp = ok_atr & ok_body & (body >= disp * avg_body) & (body >= 0.8 * rng)

    cs = np.concatenate([[np.nan], c[:-1]])
    dsp_prev = np.concatenate([[False], dsp[:-1]])
    atr_prev = np.concatenate([[np.nan], atr[:-1]])

    # --- CHoCH kanonik (identik backtest26) ---------------------------------
    prev_sl = np.full(n, -1, dtype=int)
    v_sl = sl_i >= 0
    if v_sl.any():
        pref = _prefix_last(np.isin(ix, sl_i[v_sl]))
        prev_sl[v_sl] = pref[np.maximum(sl_i[v_sl] - 1, 0)]
        prev_sl[v_sl] = np.where(np.isin(prev_sl[v_sl], sl_i[v_sl]), prev_sl[v_sl], -1)
    hl_flag = np.zeros(n, bool)
    vh = (sl_i >= 0) & (prev_sl >= 0)
    hl_flag[sl_i[vh]] = (sl_p[vh] > l[np.maximum(prev_sl[vh], 0)])
    hl_seq = np.maximum.accumulate(np.where(hl_flag, ix, -1))
    choch_dn = (hl_seq >= 0) & (cs < l[np.maximum(hl_seq, 0)])

    prev_sh = np.full(n, -1, dtype=int)
    v_sh = sh_i >= 0
    if v_sh.any():
        prefh = _prefix_last(np.isin(ix, sh_i[v_sh]))
        prev_sh[v_sh] = prefh[np.maximum(sh_i[v_sh] - 1, 0)]
        prev_sh[v_sh] = np.where(np.isin(prev_sh[v_sh], sh_i[v_sh]), prev_sh[v_sh], -1)
    lh_flag = np.zeros(n, bool)
    vhh = (sh_i >= 0) & (prev_sh >= 0)
    lh_flag[sh_i[vhh]] = (sh_p[vhh] < h[np.maximum(prev_sh[vhh], 0)])
    lh_seq = np.maximum.accumulate(np.where(lh_flag, ix, -1))
    choch_up = (lh_seq >= 0) & (cs > h[np.maximum(lh_seq, 0)])

    # margin: seberapa jauh close melewati level (dalam ATR)
    lvl_dn = np.where(hl_seq >= 0, l[np.maximum(hl_seq, 0)], np.nan)
    lvl_up = np.where(lh_seq >= 0, h[np.maximum(lh_seq, 0)], np.nan)
    margin_dn = np.where(np.isfinite(lvl_dn) & np.isfinite(atr_prev), (lvl_dn - cs) / atr_prev, np.nan)
    margin_up = np.where(np.isfinite(lvl_up) & np.isfinite(atr_prev), (cs - lvl_up) / atr_prev, np.nan)

    # --- gate ---------------------------------------------------------------
    ubd = _prefix_last(((sh_i >= 0) & (cs > sh_p)) & dsp_prev)
    dbd = _prefix_last(((sl_i >= 0) & (cs < sl_p)) & dsp_prev)
    bear, bull = c < o, c > o
    ob_u_i = np.where(ubd > 0, _prefix_last(bear)[np.maximum(ubd - 1, 0)], -1)
    ob_d_i = np.where(dbd > 0, _prefix_last(bull)[np.maximum(dbd - 1, 0)], -1)
    fresh_u = (ubd >= 0) & (ix - ubd <= W) & (ob_u_i >= 0)
    fresh_d = (dbd >= 0) & (ix - dbd <= W) & (ob_d_i >= 0)
    ob_u_hi = np.where(ob_u_i >= 0, np.maximum(o[np.maximum(ob_u_i, 0)], c[np.maximum(ob_u_i, 0)]), np.nan)
    ob_u_lo = np.where(ob_u_i >= 0, np.minimum(o[np.maximum(ob_u_i, 0)], c[np.maximum(ob_u_i, 0)]), np.nan)
    ob_d_hi = np.where(ob_d_i >= 0, np.maximum(o[np.maximum(ob_d_i, 0)], c[np.maximum(ob_d_i, 0)]), np.nan)
    ob_d_lo = np.where(ob_d_i >= 0, np.minimum(o[np.maximum(ob_d_i, 0)], c[np.maximum(ob_d_i, 0)]), np.nan)

    gap_u = l[2:] - h[:-2]
    gap_d = l[:-2] - h[2:]
    thr = FVG_MIN_ATR * np.nan_to_num(atr[2:], nan=1e18)
    fvg_u = np.zeros(n, bool); fvg_d = np.zeros(n, bool)
    fvg_u[2:] = (gap_u > 0) & (c[1:-1] > o[1:-1]) & (gap_u >= thr)
    fvg_d[2:] = (gap_d > 0) & (c[1:-1] < o[1:-1]) & (gap_d >= thr)
    lu = _prefix_last(fvg_u); ld = _prefix_last(fvg_d)
    fu = (lu >= 0) & (ix - lu <= W)
    fd = (ld >= 0) & (ix - ld <= W)
    fvu_hi = np.where(lu >= 0, l[np.maximum(lu, 0)], np.nan)
    fvd_lo = np.where(ld >= 0, h[np.maximum(ld, 0)], np.nan)

    def sweep(side: str) -> np.ndarray:
        out = np.zeros(n, bool)
        lvl = sl_p if side == "low" else sh_p
        for k in range(SWEEP_BARS):
            if k:
                lv = np.concatenate([np.full(k, np.nan), lvl[:-k]])
                p5 = np.concatenate([np.full(k, False), (l5 < lvl)[:-k]]) if side == "low" \
                    else np.concatenate([np.full(k, False), (h5 > lvl)[:-k]])
                b5 = np.concatenate([np.full(k, False), (c5 > lvl)[:-k]]) if side == "low" \
                    else np.concatenate([np.full(k, False), (c5 < lvl)[:-k]])
            else:
                lv = lvl
                p5 = (l5 < lvl) if side == "low" else (h5 > lvl)
                b5 = (c5 > lvl) if side == "low" else (c5 < lvl)
            out |= np.isfinite(lv) & p5 & b5
        return out

    sw_up, sw_dn = sweep("low"), sweep("high")

    eqh = np.zeros(n, bool); eql = np.zeros(n, bool)
    v_h = (sh_i >= 0) & (prev_sh >= 0)
    eqh[v_h] = np.abs(h[np.maximum(sh_i[v_h], 0)] - h[np.maximum(prev_sh[v_h], 0)]) < EQ_TOL * atr[v_h]
    v_l = (sl_i >= 0) & (prev_sl >= 0)
    eql[v_l] = np.abs(l[np.maximum(sl_i[v_l], 0)] - l[np.maximum(prev_sl[v_l], 0)]) < EQ_TOL * atr[v_l]

    span = sh_p - sl_p
    okleg = np.isfinite(span) & (span > 0)
    den = np.where(okleg, span, 1.0)
    retr_from_hi = np.where(okleg, (sh_p - c5) / den, np.nan)
    retr_from_lo = np.where(okleg, (c5 - sl_p) / den, np.nan)
    ote_long = okleg & (retr_from_hi >= OTE_LO) & (retr_from_hi <= OTE_HI)
    ote_short = okleg & (retr_from_lo >= OTE_LO) & (retr_from_lo <= OTE_HI)

    blk = T["t"] // 3600
    ub, start = np.unique(blk, return_index=True)
    end = np.append(start[1:], n)
    k = np.searchsorted(ub, blk)
    kk = np.maximum(k - 1, 0)
    contig = (k >= 1) & (ub[kk] == blk - 1)
    h1 = np.where(contig, np.sign(c[end[kk] - 1] - o[start[kk]]), np.nan)
    h1_up, h1_dn = h1 > 0, h1 < 0

    ob_up_touch = fresh_u & (l5 <= ob_u_hi) & (c5 > ob_u_lo)
    ob_dn_touch = fresh_d & (h5 >= ob_d_lo) & (c5 < ob_d_hi)
    fvg_up_touch = fu & (l5 <= fvu_hi) & (c5 > fvu_hi)
    fvg_dn_touch = fd & (h5 >= fvd_lo) & (c5 < fvd_lo)

    rmax = _rolling(h, ROLL, np.max)
    rmin = _rolling(l, ROLL, np.min)
    bos_up = (sh_i >= 0) & (cs > sh_p)
    bos_dn = (sl_i >= 0) & (cs < sl_p)
    bos_up_r = np.isfinite(rmax) & (cs > rmax)
    bos_dn_r = np.isfinite(rmin) & (cs < rmin)
    tier_up = lth_i >= 0
    tier_dn = ltl_i >= 0

    return dict(
        choch_up=choch_up, choch_dn=choch_dn,
        margin_up=margin_up, margin_dn=margin_dn,
        bull=dict(h1=h1_up, sweep=sw_up, ob=ob_up_touch, fvg=fvg_up_touch,
                  eqh=eqh, ote=ote_long, dsp=dsp_prev, bos=bos_up | bos_up_r, tier=tier_up),
        bear=dict(h1=h1_dn, sweep=sw_dn, ob=ob_dn_touch, fvg=fvg_dn_touch,
                  eqh=eql, ote=ote_short, dsp=dsp_prev, bos=bos_dn | bos_dn_r, tier=tier_dn),
    )


def build() -> None:
    print("memuat cache...", flush=True)
    tabs = H.load_tables()
    day0 = H._day_index(tabs)
    print(f"simbol={len(tabs)}", flush=True)

    frames = []
    for p in (1, 2):
        for disp in (1.5, 2.0):
            rows = []
            for sym, T in tabs.items():
                f = features(T, p, disp)
                ok = T["ok"]
                y = T["y"]
                day = (T["t"] // DAY - day0).astype(np.int16)
                for direction, trig, marg, gates in (
                    (1, f["choch_up"], f["margin_up"], f["bull"]),
                    (-1, f["choch_dn"], f["margin_dn"], f["bear"]),
                ):
                    m = trig & ok & np.isfinite(marg)
                    if not m.any():
                        continue
                    rec = {
                        "y": y[m].astype(np.int8),
                        "day": day[m],
                        "dir": np.full(m.sum(), direction, np.int8),
                        "margin": marg[m].astype(np.float32),
                        "sym": np.full(m.sum(), sym),
                        "p": np.full(m.sum(), p, np.int8),
                        "disp": np.full(m.sum(), disp, np.float32),
                    }
                    for gname in GATE_NAMES:
                        rec[gname] = gates[gname][m].astype(np.int8)
                    rows.append(rec)
            if not rows:
                continue
            cat = {k: np.concatenate([r[k] for r in rows]) for k in rows[0]}
            frames.append(cat)
            print(f"  p={p} disp={disp} -> {len(cat['y']):,} sinyal", flush=True)

    out = {k: np.concatenate([f[k] for f in frames]) for k in frames[0]}
    path = Path(__file__).with_name("cache_signals.npz")
    np.savez_compressed(path, **out)
    print(f"\ntotal {len(out['y']):,} sinyal -> {path}", flush=True)
    print("simbol:", len(np.unique(out["sym"])), flush=True)


if __name__ == "__main__":
    build()
