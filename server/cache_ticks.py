"""cache_ticks.py — dump tabel `ticks` (read-only) ke satu .npz per simbol.

Satu scan berurutan (rowid) lebih cepat daripada 142 index-search acak di DB 10 GB.
Output: <out>/<symbol>.npz berisi t (float64 detik UTC) dan p (float64 harga),
terurut waktu. Dipakai research_harness.py.
"""
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

DB = Path(__file__).resolve().parent.parent / "data" / "otc_research.sqlite3"
CHUNK = 2_000_000


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    cur = con.execute("select symbol, time, price from ticks")
    parts = defaultdict(list)
    total = 0
    while rows := cur.fetchmany(CHUNK):
        sym = np.array([r[0] for r in rows], dtype=object)
        tp = np.array([(r[1], r[2]) for r in rows], dtype=np.float64)
        for s in np.unique(sym):
            parts[s].append(tp[sym == s])
        total += len(rows)
        print(f"{total:,} rows", flush=True)
    con.close()
    for s, chunks in parts.items():
        a = np.concatenate(chunks)
        a = a[np.argsort(a[:, 0], kind="stable")]
        np.savez(out / f"{s}.npz", t=a[:, 0], p=a[:, 1])
    print(f"done {len(parts)} symbols", flush=True)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
