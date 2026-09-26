"""BMF.PO-AUTOTRADE-AI — a modern async client for the PocketOption WebSocket API.

Work in progress. Ships the transport, the high-level client, and typed models;
streams/candles and the MCP server are added incrementally.
"""
from __future__ import annotations

from ._transport import Transport, parse_ssid
from .client import AsyncPocketOption
from .constants import Region
from .exceptions import (
    AssetError,
    AuthenticationError,
    ConnectionFailedError,
    LiveTradingDisabled,
    NotAuthorizedError,
    OrderError,
    OrderTimeout,
    PocketOptionError,
)
from .models import Asset, Balance, Candle, Deal, Direction

PROJECT_NAME = "BMF.PO-AUTOTRADE-AI"

try:  # single source of truth is the installed distribution metadata
    from importlib.metadata import PackageNotFoundError, version

    try:
        __version__ = version("bmf-po-autotrade-ai")
    except PackageNotFoundError:  # running from a source checkout, not installed
        __version__ = "0.1.0"
except ImportError:  # pragma: no cover - Python <3.8 has no importlib.metadata
    __version__ = "0.1.0"

__all__ = (
    "AsyncPocketOption",
    "Transport",
    "parse_ssid",
    "Region",
    "Asset",
    "Balance",
    "Candle",
    "Deal",
    "Direction",
    "PocketOptionError",
    "ConnectionFailedError",
    "AuthenticationError",
    "NotAuthorizedError",
    "AssetError",
    "OrderError",
    "OrderTimeout",
    "LiveTradingDisabled",
    "PROJECT_NAME",
    "__version__",
)
