# Pocket Option Connection Contract

## Scope

- Account: **DEMO only**.
- Capability: market metadata, authenticated subscriptions, valid realtime ticks, local M1 candles, local research journal.
- Explicitly disabled: orders, buy/sell actions, balance mutation, withdrawals, deposits, live-account sessions, automation.

## Credential contract

1. The user provides a Pocket Option DEMO SSID locally.
2. `POST /api/pocket/credential` accepts only an `auth` frame with `isDemo: 1` and a non-empty session.
3. The credential remains in the configured local file. It is never returned by any route.
4. Updating the credential restarts only the feed worker.

## Snapshot contract

`GET /api/pocket/snapshot?symbol=<broker-symbol>` returns the latest broker state.

Required fields:

| Field | Meaning |
|---|---|
| `status` | `live`, `stale`, `connecting`, or `disconnected` |
| `mode` | Always `DEMO` |
| `markets` | Broker market metadata |
| `ticks` | Latest validated tick per symbol |
| `candles` | Requested symbol's persisted M1 OHLCV only |
| `liveSymbols` | Symbols with a tick no older than 10 seconds |
| `receivedAt` | Local receipt time of latest worker snapshot |
| candle interval | `60` seconds for M1 candle boundaries |

The route surface is data-only: no broker action endpoint exists. The UI must render the DEMO/read-only boundary explicitly.

A UI must show stale/error state as-is. It must not call stale data realtime or infer missing prices.

## Chart integrity status

The desktop chart reports only `NORMAL` or `ANOMALI`. This is a local data-quality monitor, not evidence that a broker manipulated a market.

- `NORMAL`: the selected market has a live broker tick, at least 50 persisted M1 candles, a valid last candle, and a current range within the configured local baseline.
- `ANOMALI`: one or more checks fail: stale tick, insufficient history, invalid candle, or current range outside the local median-range threshold.

The status is recalculated on every one-second snapshot refresh. It never creates an order, a trade signal, or an accusation.

## Data validation

- Reject non-finite, non-positive, out-of-window, and out-of-order ticks.
- Persist raw ticks and derived M1 candles locally.
- Keep the latest 500 M1 candles per requested symbol.
- Use broker time normalized to UTC; research queries may render WIB.
- The desktop treats the latest broker candle as active and earlier received candles as closed. This avoids PC clock offsets suppressing confirmed B.O.S/CHoCH markers.

## Reconnect policy

The feed reconnects with bounded exponential backoff: 5 seconds, doubling to a maximum of 60 seconds. Authentication timeout/failure is surfaced in `status`; no silent fallback provider is used for Pocket symbols.

## API boundary

All `/api/pocket/*` routes require the local API key. Any unsupported method returns HTTP 405 with `Pocket Option data-only: orders are disabled`.
