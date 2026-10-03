# OpenTerminal Agent Rules

## Product contract

OpenTerminal is a Windows WPF desktop application focused on the Pocket Option **DEMO** market-data connection.

- Keep `desktop/` native WPF. Do not add WebView, browser UI, Next.js UI, or a localhost web frontend.
- Keep Pocket Option **data-only**. No order, buy, sell, deposit, withdrawal, account mutation, or live-account support.
- Treat `server/pocket_feed.py` as the sole broker WebSocket client.
- Treat `server/src/pocket.ts` as the sole Node owner of that worker and its API contract.
- Preserve the broker snapshot schema and its 1-minute OHLC candle semantics unless the user explicitly approves a versioned migration.
- Do not redesign the workspace, navigation, or widgets while changing connection code. Connection work changes only `server/pocket_feed.py`, `server/src/pocket.ts`, tests, and connection documentation unless required otherwise.
- Do not add dependencies for charts, UI, or broker access without explicit user approval.

## Security rules

- SSID/API keys/cookies are secrets. Never commit, log, render, return, or copy them into documentation.
- Bind the API to `127.0.0.1`; retain `X-Api-Key` protection on Pocket endpoints.
- Accept only demo SSID frames where `isDemo === 1`.
- Fail closed: unknown authentication, stale data, malformed ticks, or unavailable worker must show status only; never synthesize a trade signal or submit an action.

## Validation

Connection changes require:

```powershell
python server/test_pocket_feed.py
npm run build -w server
```

Verify `/api/pocket/credential` and `/api/pocket/snapshot` with the local API key. Verify `status=live` only with a valid DEMO session; otherwise report the actual status.

## Change control

Before changing the connection contract, list the exact route/schema/behavior change. Do not make speculative UI or architecture changes.