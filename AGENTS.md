# OpenTerminal Agent Rules

## Product contract

OpenTerminal is a Windows WPF desktop application focused on the Pocket Option **DEMO** market-data connection.

- Keep `desktop/` native WPF. Do not add WebView, browser UI, Next.js UI, or a localhost web frontend.
- Pocket Option order tools (order, buy/CALL, sell/PUT) are allowed on the **DEMO** account only. No deposit, withdrawal, other account mutation, or live-account support.
- Every order must be explicit (asset, direction, amount, expiry), validated before sending, and its broker result reported back; never auto-retry a failed order.
- Treat `server/pocket_feed.py` as the sole broker WebSocket client.
- Treat `server/src/pocket.ts` as the sole Node owner of that worker and its API contract.
- Preserve the broker snapshot schema and its 1-minute OHLC candle semantics unless the user explicitly approves a versioned migration.
- Do not redesign the workspace, navigation, or widgets while changing connection code. Connection work changes only `server/pocket_feed.py`, `server/src/pocket.ts`, tests, and connection documentation unless required otherwise.
- Do not add dependencies for charts, UI, or broker access without explicit user approval.

## Security rules

- SSID/API keys/cookies are secrets. Never commit, log, render, return, or copy them into documentation.
- Bind the API to `127.0.0.1`; retain `X-Api-Key` protection on Pocket endpoints.
- Accept only demo SSID frames where `isDemo === 1`.
- Fail closed: unknown authentication, stale data, malformed ticks, or unavailable worker must show status only; never synthesize a trade signal and never submit an order in that state.

## Validation

Connection changes require:

```powershell
python server/test_pocket_feed.py
npm run build -w server
```

Verify `/api/pocket/credential` and `/api/pocket/snapshot` with the local API key. Verify `status=live` only with a valid DEMO session; otherwise report the actual status.

## Change control

Before changing the connection contract, list the exact route/schema/behavior change. Do not make speculative UI or architecture changes.

## Zero-defect delivery (aturan pemilik proyek)

- Pemilik proyek tidak mentoleransi kesalahan sekecil apa pun. Jangan pernah melaporkan "selesai" sebelum hasil diverifikasi berulang dan terbukti berjalan.
- Setiap perubahan desktop wajib: build Release `0 warning 0 error`, `desktop\dist` tersinkron (hash DLL identik dengan `bin\Release`), aplikasi di-restart dari `dist`, proses `Responding=True`, tidak ada `OpenTerminal-crash.log` dan tidak ada error baru di Event Log.
- Perubahan visual wajib diverifikasi dengan screenshot aplikasi yang sedang berjalan, bukan hanya dari kode.
- Perubahan koneksi wajib menjalankan validasi di bagian Validation dan memastikan `/api/pocket/snapshot` `status=live` DEMO.
- Yang ditampilkan ke pemilik proyek adalah hasil FINAL yang sudah berhasil; jika ada yang belum terbukti, nyatakan secara eksplisit sebagai belum terverifikasi.