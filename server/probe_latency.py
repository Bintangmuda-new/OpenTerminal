"""probe_latency.py - ukur latensi feed broker tanpa order: tick.time (server) vs receivedAt (lokal), dan jitter batas menit. 60 sampel @1s."""
import json, time, urllib.request, pathlib, statistics as st
key = pathlib.Path("data/.api-key").read_text().strip()
lat, mins = [], {}
for i in range(60):
    req = urllib.request.Request("http://127.0.0.1:4000/api/pocket/snapshot?symbol=EURUSD_otc", headers={"x-api-key": key})
    t0 = time.time(); s = json.load(urllib.request.urlopen(req, timeout=5)); t1 = time.time()
    tk = s["ticks"].get("EURUSD_otc"); 
    if tk: lat.append((t1 - tk["time"]) * 1000)
    c = s["candles"]["EURUSD_otc"]
    if c: mins[c[-1].get("time") or c[-1].get("t")] = (c[-1], round(t1 % 60, 1))
    if i == 0: print("status", s["status"], "mode", s["mode"], "liveSymbols", len(s["liveSymbols"]), "rtt_ms", round((t1-t0)*1000))
    time.sleep(1)
if lat: print(f"latensi tick server->lokal: median {st.median(lat):.0f} ms  p90 {sorted(lat)[int(len(lat)*.9)]:.0f} ms  min {min(lat):.0f} max {max(lat):.0f}  n={len(lat)}")
print("candle terakhir sample:", list(mins.items())[-2:])
