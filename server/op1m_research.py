"""Read-only audit and locked OP 1M evaluation for the local tick cache."""
from __future__ import annotations

import argparse
import csv
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

import research_harness as H
from backtest26_smc_canon import smc_canon

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CACHE = Path(r"E:\otc_cache")
DEFAULT_REPORT = ROOT / "reports" / "POCKET-OPTION-OP-1M.md"
RULE = "K15_seq_htf_choch"
PARAMS = (1, 1.5)  # selected on training data by backtest26_smc_canon.py


def utc(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def wilson(wins: int, total: int) -> tuple[float, float]:
    if total == 0:
        return 0.0, 100.0
    z = 1.96
    p = wins / total
    d = 1 + z * z / total
    center = (p + z * z / (2 * total)) / d
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / d
    return (center - half) * 100, (center + half) * 100


def audit_ticks(folder: Path) -> dict:
    files = sorted(folder.glob("*.npz"))
    result = {"files": len(files), "ticks": 0, "bad_files": [], "invalid": 0,
              "unsorted": 0, "duplicate_times": 0, "first": math.inf, "last": -math.inf}
    for path in files:
        try:
            with np.load(path, allow_pickle=False) as archive:
                t, p = archive["t"], archive["p"]
                if t.ndim != 1 or p.ndim != 1 or len(t) != len(p):
                    raise ValueError("t/p shape mismatch")
                result["ticks"] += len(t)
                result["invalid"] += int((~np.isfinite(t) | ~np.isfinite(p) | (p <= 0)).sum())
                result["unsorted"] += int((np.diff(t) < 0).sum())
                result["duplicate_times"] += int(len(t) - np.unique(t).size)
                if len(t):
                    result["first"] = min(result["first"], float(t[0]))
                    result["last"] = max(result["last"], float(t[-1]))
        except Exception as error:
            result["bad_files"].append(f"{path.name}: {error}")
    result["bytes"] = sum(path.stat().st_size for path in files)
    return result


def audit_csv(folder: Path) -> dict:
    result = {"files": 0, "bytes": 0, "rows": 0, "bad_rows": 0,
              "invalid_ohlc": 0, "duplicate_times": 0, "out_of_order": 0,
              "assets": set(), "headers": set(), "first": math.inf, "last": -math.inf}
    for path in sorted(folder.glob("*.csv")):
        result["files"] += 1
        result["bytes"] += path.stat().st_size
        result["assets"].add(path.name.split("_2024_")[0])
        try:
            with path.open(newline="", encoding="utf-8-sig") as source:
                reader = csv.DictReader(source)
                result["headers"].add(tuple(reader.fieldnames or ()))
                previous = None
                for row in reader:
                    result["rows"] += 1
                    try:
                        t, o, h, l, c = (float(row[key]) for key in ("timestamp", "open", "high", "low", "close"))
                        if not all(map(math.isfinite, (t, o, h, l, c))) or min(o, h, l, c) <= 0:
                            result["bad_rows"] += 1
                        if h < max(o, c, l) or l > min(o, c, h):
                            result["invalid_ohlc"] += 1
                        if previous is not None:
                            result["duplicate_times"] += int(t == previous)
                            result["out_of_order"] += int(t < previous)
                        previous = t
                        result["first"] = min(result["first"], t)
                        result["last"] = max(result["last"], t)
                    except (KeyError, TypeError, ValueError):
                        result["bad_rows"] += 1
        except (OSError, csv.Error) as error:
            result["bad_rows"] += 1
            result.setdefault("bad_files", []).append(f"{path.name}: {error}")
    result["assets"] = len(result["assets"])
    result["headers"] = [list(header) for header in sorted(result["headers"])]
    return result


def summarize(pred: np.ndarray, y: np.ndarray) -> dict:
    wins = int((pred == y).sum())
    losses = int((pred == -y).sum())
    draws = int((y == 0).sum())
    n = wins + losses + draws
    return {"n": n, "wins": wins, "losses": losses, "draws": draws,
            "winrate": wins / n * 100 if n else 0.0,
            "ci": wilson(wins, n),
            "call": int((pred == 1).sum()), "put": int((pred == -1).sum()),
            "call_wins": int(((pred == 1) & (y == 1)).sum()),
            "put_wins": int(((pred == -1) & (y == -1)).sum())}


def evaluate(tabs: dict) -> tuple[dict, dict]:
    day0 = H._day_index(tabs)
    segments = {name: [[], []] for name in ("train", "holdout", "lockbox")}
    block_counts: dict[int, list[int]] = {}
    for symbol, table in tabs.items():
        prediction = smc_canon(table, *PARAMS)[RULE]
        day = table["t"] // H.DAY - day0
        masks = {
            "train": day < H.HOLDOUT_FROM_DAY,
            "holdout": (day >= H.HOLDOUT_FROM_DAY) & (day < H.LOCKBOX_FROM_DAY),
            "lockbox": day >= H.LOCKBOX_FROM_DAY,
        }
        for name, mask in masks.items():
            active = mask & table["ok"] & (prediction != 0)
            segments[name][0].append(prediction[active].astype(np.int8))
            segments[name][1].append(table["y"][active].astype(np.int8))
            if name == "lockbox":
                for bucket, pred, outcome in zip(
                    table["t"][active] // 300, prediction[active].astype(np.int8),
                    table["y"][active].astype(np.int8),
                ):
                    counts = block_counts.setdefault(int(bucket), [0, 0, 0])
                    counts[0] += int(pred == outcome)
                    counts[1] += int(pred == -outcome)
                    counts[2] += int(outcome == 0)

    results = {}
    for name, (predictions, labels) in segments.items():
        pred = np.concatenate(predictions)
        y = np.concatenate(labels)
        results[name] = summarize(pred, y)

    blocks = np.asarray([block_counts[key] for key in sorted(block_counts)], dtype=np.int64)
    rng = np.random.default_rng(20261018)
    bootstrap = np.empty(4000)
    for i in range(len(bootstrap)):
        sample = blocks[rng.integers(0, len(blocks), len(blocks))].sum(axis=0)
        bootstrap[i] = sample[0] / sample.sum() * 100
    results["lockbox"]["blocks_5m"] = len(blocks)
    results["lockbox"]["block_ci"] = tuple(np.percentile(bootstrap, (2.5, 97.5)))
    results["lockbox"]["symbols"] = len({
        symbol for symbol, table in tabs.items()
        if ((table["t"] // H.DAY - day0 >= H.LOCKBOX_FROM_DAY) & table["ok"]).any()
    })
    return results, {"days": sorted({int(table["t"].min() // H.DAY - day0) for table in tabs.values()})}


def render(ticks: dict, csv_audit: dict, tabs: dict, metrics: dict) -> str:
    valid_pairs = sum(int(table["ok"].sum()) for table in tabs.values())
    minute_bars = sum(len(table["t"]) for table in tabs.values())
    tick_period = f"{utc(ticks['first'])} – {utc(ticks['last'])}" if math.isfinite(ticks["first"]) else "unavailable"
    csv_period = f"{utc(csv_audit['first'])} – {utc(csv_audit['last'])}" if math.isfinite(csv_audit["first"]) else "unavailable"
    lines = [
        "# Riset Pocket Option OP 1M",
        "",
        "Tanggal audit: 2026-10-18. Semua sumber dibaca tanpa mengubah data mentah.",
        "",
        "## Temuan data",
        "",
        f"- Cache tick: {ticks['files']} arsip NPZ, {ticks['ticks']:,} tick, {ticks['bytes']:,} byte, "
        f"{len(tabs)} simbol; rentang {tick_period}.",
        f"- Pemeriksaan tick: nilai invalid {ticks['invalid']:,}; waktu mundur {ticks['unsorted']:,}; "
        f"timestamp duplikat per simbol {ticks['duplicate_times']:,}; arsip rusak {len(ticks['bad_files'])}.",
        f"- Cache candle hasil olahan: {minute_bars:,} bar M1; pasangan T-5/entry/close valid {valid_pairs:,}.",
        f"- CSV terpisah `ext_vitaly`: {csv_audit['files']} file, {csv_audit['rows']:,} baris, "
        f"{csv_audit['assets']} simbol, rentang {csv_period}; bukan dataset 42 juta tick.",
        f"- CSV: {csv_audit['bad_rows']:,} baris malformed, {csv_audit['invalid_ohlc']:,} baris OHLC "
        f"tidak konsisten, timestamp duplikat {csv_audit['duplicate_times']:,}, mundur {csv_audit['out_of_order']:,}.",
        "- Database SQLite aplikasi berisi 42,409,629 baris `ticks` dan 42,343,834 `raw_ticks`; "
        "jurnal hasil live `signal_outcomes` kosong.",
        "- Arsip tick memiliki waktu epoch dan harga; bid/ask serta bukti kecocokan harga penyelesaian dengan "
        "Pocket Option tidak tersedia. Hasil adalah proksi historis, bukan hasil broker terverifikasi.",
        "",
        "## Protokol",
        "",
        "- Keputusan pada candle A di T-5 detik: fitur hanya memakai candle yang sudah tutup dan tick A "
        "dengan offset sampai 55 detik.",
        "- Entry: tick valid pertama pada candle B berikutnya; hasil: tick terakhir candle B dibandingkan entry.",
        "- Naik = CALL menang, turun = PUT menang, sama = DRAW. Draw dihitung terpisah dan tidak menjadi win.",
        "- Pemisahan waktu nominal: train hari 0–5, holdout hari 6, dan hari 7. Namun, hasil hari 7 sudah "
        "diperiksa selama eksperimen strategi terdahulu, lalu dipakai dalam pemilihan/peninjauan strategi. "
        "Karena itu hari 7 bukan lockbox independen; seluruh hasil di bawah bersifat deskriptif.",
        "- Parameter `p=1, displacement=1.5` awalnya dicari di train. Strategi `K15_seq_htf_choch` kemudian "
        "dipilih setelah beberapa eksperimen SMC dan paparan hasil hari 7. Interval Wilson dan bootstrap "
        "blok 5-menit hanya menggambarkan variasi sampel, bukan validasi independen.",
        "",
        "## Hasil pengujian retrospektif",
        "",
        "| Periode | Sinyal | Win | Loss | Draw | Win rate | Wilson 95% | CALL menang | PUT menang |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for segment in ("train", "holdout", "lockbox"):
        result = metrics[segment]
        lo, hi = result["ci"]
        label = {"train": "hari 0–5", "holdout": "hari 6",
                 "lockbox": "hari 7 (terpapar)"}[segment]
        lines.append(
            f"| {label} | {result['n']:,} | {result['wins']:,} | {result['losses']:,} | "
            f"{result['draws']:,} | {result['winrate']:.2f}% | {lo:.2f}–{hi:.2f}% | "
            f"{result['call_wins']:,}/{result['call']:,} | {result['put_wins']:,}/{result['put']:,} |"
        )
    lock = metrics["lockbox"]
    lines.extend([
        "",
        f"Hari 7 mencakup {lock['symbols']} simbol dan {lock['blocks_5m']} bucket lima-menit. "
        f"Bootstrap blok 95% deskriptif: {lock['block_ci'][0]:.2f}–{lock['block_ci'][1]:.2f}%. "
        "Ini bukan interval dari test independen.",
        "",
        "## Pemeriksaan pembanding dan putusan",
        "",
        "- SMC kanonik: hari 7 mencatat 51.45% (26,599/51,699; 24,572 loss; 528 draw). "
        "Jauh dari target 100%; karena hari 7 telah terekspos saat iterasi, angka ini bukan performa test "
        "independen. Sampel juga hanya satu hari dan simbol saling berkorelasi.",
        "- RSI, z-score, price-action, momentum, logistic regression, kNN, dan 14 aturan SMC kanonik "
        "sudah diuji dalam runner `backtest16_reverse.py`, `backtest17_patterns.py`, "
        "`backtest20_knn_verify.py`, dan `backtest26_smc_canon.py`; hasil model/aturan lain berada dekat 50%.",
        "- Kontrol yang membaca close candle B mencapai 100% karena memakai label masa depan. Itu kebocoran "
        "informasi, bukan strategi yang dapat dipakai sebelum entry (`backtest27_lookahead_demo.py`).",
        "- **Target win rate 100% tidak terbukti.** Tidak ada test independen yang tersisa pada dataset ini. "
        "Strategi belum tervalidasi untuk forward testing; data hanya delapan hari, feed tidak dicocokkan dengan "
        "settlement broker, dan hasil model lintas strategi pernah ditinjau selama iterasi.",
        "- CSV 2024 terlalu kecil untuk validasi model dan memiliki ketidaksesuaian header OHLC pada dua berkas OTC; "
        "baris mentah tidak diperbaiki atau dimasukkan ke pengujian tick.",
        "",
        "## Reproduksi",
        "",
        "```powershell",
        "server\\.venv\\Scripts\\python.exe server\\op1m_research.py",
        "server\\.venv\\Scripts\\python.exe server\\backtest26_smc_canon.py selftest",
        "server\\.venv\\Scripts\\python.exe -m unittest discover -s server -p 'test_*.py'",
        "```",
        "",
    ])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()

    H.CACHE = args.cache
    ticks = audit_ticks(args.cache / "ticks")
    csv_audit = audit_csv(args.cache / "ext_vitaly")
    tabs = H.load_tables()
    assert len(tabs) == ticks["files"], "tick cache and minute cache symbol counts differ"
    metrics, _ = evaluate(tabs)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(render(ticks, csv_audit, tabs, metrics), encoding="utf-8")
    print(args.report)
    for name in ("train", "holdout", "lockbox"):
        result = metrics[name]
        label = "hari-7-terpapar" if name == "lockbox" else name
        print(f"{label}: {result['winrate']:.2f}% n={result['n']:,} "
              f"W={result['wins']:,} L={result['losses']:,} D={result['draws']:,}")
    print(f"ticks={ticks['ticks']:,} CSV rows={csv_audit['rows']:,} "
          f"invalid OHLC={csv_audit['invalid_ohlc']:,}")


if __name__ == "__main__":
    main()
