# Fixed Architecture

## Purpose

OpenTerminal is a native Windows desktop terminal. Its primary function is Pocket Option **DEMO** market-data collection and analysis. It is not a trading client.

## Runtime tree

```text
OpenTerminal/
├── AGENTS.md                         # mandatory engineering rules
├── desktop/                           # native WPF executable only
│   ├── MainWindow.xaml                # presentation; never owns broker sockets
│   └── MainWindow.xaml.cs             # calls local API only
├── server/
│   ├── pocket_feed.py                 # only Pocket Option WebSocket client
│   ├── test_pocket_feed.py            # broker-data invariants
│   └── src/
│       ├── index.ts                   # starts one API process + one feed worker
│       └── pocket.ts                  # worker lifecycle + read-only API boundary
├── data/
│   ├── .api-key                       # local generated secret; untracked
│   └── otc_research.sqlite3           # local Pocket tick/candle journal; untracked
└── docs/architecture/
    ├── FIXED-ARCHITECTURE.md          # this document
    └── POCKET-CONNECTION.md           # connection contract
```

## Fixed request path

```text
Pocket Option DEMO WebSocket
  → server/pocket_feed.py
  → stdout JSON snapshot
  → server/src/pocket.ts
  → 127.0.0.1:4000/api/pocket/*
  → desktop/MainWindow.xaml.cs
  → WPF display
```

No other component may open a broker socket, read an SSID, write broker data, or perform a broker action.

## Ownership

| Concern | Owner | Rule |
|---|---|---|
| Broker auth/subscription/ticks | `server/pocket_feed.py` | DEMO only; data only |
| Feed process lifecycle | `server/src/pocket.ts` | one child process per API process |
| Local API authentication | `server/src/auth.ts` | `X-Api-Key` required |
| Tick/candle journal | `data/otc_research.sqlite3` | append/read-only research flow |
| Native presentation | `desktop/` | consumes API; never broker WebSocket |

## Chart structure contract

- B.O.S and CHoCH use confirmed swing pivots and a closed-candle close beyond the pivot level.
- The current candle cannot produce an arrow. A confirmed break produces one chart arrow only.
- Support/resistance levels remain visual references; bounce/rejection arrows are disabled to avoid duplicate markers.
- Chart analysis stays informational and DEMO data-only.

## Stable scope

Connection work may change feed/reconnect/storage/status behavior. It must not replace the desktop UI, add a web UI, add a broker order route, or alter the snapshot fields without explicit approval.
