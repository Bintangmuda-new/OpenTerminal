"""inspect_db.py — lihat skema & ukuran data riset."""
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "otc_research.sqlite3"
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)

for name, sql in con.execute("select name, sql from sqlite_master where type='table'"):
    print("TABLE:", name)
    print(sql)
    print("---")

print("symbols:", con.execute("select count(distinct symbol) from candles_m1").fetchone()[0])
print("rows:", con.execute("select count(*) from candles_m1").fetchone()[0])
print("range:", con.execute("select min(time), max(time) from candles_m1").fetchone())
print("sample:", con.execute("select * from candles_m1 limit 3").fetchall())
con.close()
