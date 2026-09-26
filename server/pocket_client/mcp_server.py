"""
cmp_server_pocket_option_2026.mcp_server
===========================

An MCP server that exposes the PocketOption client as tools for Claude Code
(or any MCP host). Start it over stdio; Claude discovers the tools automatically.

Run it directly for a quick check:

    $env:PO_SSID='42["auth",{...}]'        # DEMO SSID; live needs PO_ALLOW_REAL=1
    python -m cmp_server_pocket_option_2026.mcp_server

Or register it with Claude Code / Claude Desktop (see the README config block).

Notes
-----
* The SSID is read from the ``PO_SSID`` environment variable — never hardcoded.
* Real-money trading is refused unless the server is started with
  ``PO_ALLOW_REAL=1``. Demo works out of the box.
* All logging goes to **stderr**; stdout is reserved for the MCP JSON-RPC stream.
* This server is intentionally just a thin wrapper over ``AsyncPocketOption`` —
  the same core other model adapters (GPT/Grok) can reuse.
"""
from __future__ import annotations

import asyncio
import atexit
import logging
import os
import re
import sys
from collections import OrderedDict
from typing import Optional

from mcp.server.fastmcp import FastMCP

from .client import AsyncPocketOption
from .exceptions import NotAuthorizedError
from .memory import MessageBoard, OpinionBoard, SkillStore, StrategyStore
from .models import Deal

# Logs to stderr only — stdout carries the protocol.
logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("cmp_server_pocket_option_2026.mcp")


def _resolve_manual_path(root: str, agent: str) -> Optional[str]:
    """First existing manual: per-model in agents/, shared in agents/, then legacy root."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", (agent or "").strip()).strip("_").lower() or "default"
    for candidate in (
        os.path.join(root, "agent_manuals", f"agent_instructions_{slug}.md"),  # per-model role
        os.path.join(root, "agent_manuals", "agent_instructions.md"),          # shared default
        os.path.join(root, "agent_instructions.md"),                    # legacy location
    ):
        if os.path.isfile(candidate):
            return candidate
    return None


def _load_manual() -> str:
    """Read the operating manual for this agent (see _resolve_manual_path for order)."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    agent = os.environ.get("AGENT_NAME", "agent")
    path = _resolve_manual_path(root, agent)
    if path:
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            logger.info("Operating manual for agent %r -> %s", agent, path)
            return text
        except OSError:
            pass
    logger.warning(
        "No operating manual found under %s (agents/ or root); using the built-in fallback",
        root,
    )
    return (
        "You are trading on PocketOption (binary options) via these tools. Demo by "
        "default. Expiration is any number of seconds the asset allows; pull candles "
        "at multiple timeframes and compute your own indicators. OTC pairs use "
        "synthetic prices. Validate the asset/expiration before place_trade."
    )


_MANUAL = _load_manual()

mcp = FastMCP("BMF.PO-AUTOTRADE-AI", instructions=_MANUAL)


@mcp.tool()
async def get_operating_manual() -> str:
    """READ THIS FIRST. Explains the platform (PocketOption), how binary options work,
    every tool you have, your freedoms (any expiration in seconds, any/multiple
    timeframes, compute your own indicators), OTC vs real pairs, where to get extra
    data, and the most efficient workflow. Read it once before doing anything else."""
    return _MANUAL

# --- lazily-connected shared client -------------------------------------------
_client: Optional[AsyncPocketOption] = None
_connect_lock = asyncio.Lock()

# Trades placed this session, by requestId, so check_result can find them.
# Bounded so a long-running server never leaks memory: oldest entries are
# evicted once we exceed the cap (you never need to look up ancient trades —
# trade_history covers those from the server-side record).
_MAX_RECENT_DEALS = 512
_recent_deals: "OrderedDict[int, Deal]" = OrderedDict()


def _remember_deal(deal: Deal) -> None:
    _recent_deals[deal.request_id] = deal
    _recent_deals.move_to_end(deal.request_id)
    while len(_recent_deals) > _MAX_RECENT_DEALS:
        _recent_deals.popitem(last=False)


async def _get_client() -> AsyncPocketOption:
    """Connect on first use and reuse the connection for the whole session."""
    global _client
    if _client is not None and _client.connected:
        return _client
    async with _connect_lock:
        if _client is not None and _client.connected:
            return _client
        explicit_ssid = os.environ.get("PO_SSID")
        auto_auth = os.environ.get("PO_AUTO_AUTH") == "1"
        allow_real = os.environ.get("PO_ALLOW_REAL") == "1"

        if explicit_ssid:
            ssid = explicit_ssid
        else:
            # No SSID given — use a saved browser session (or capture one).
            from .auth import resolve_ssid
            ssid = await resolve_ssid(auto_auth=auto_auth)

        client = AsyncPocketOption(ssid, allow_real=allow_real)
        try:
            region = await client.connect()
        except NotAuthorizedError:
            # A saved/auto session went stale — log in again if we own the session.
            if explicit_ssid:
                raise  # user supplied it directly; they must refresh it themselves
            from .auth import SessionManager
            logger.info("Saved session was rejected; opening the browser to log in again.")
            data = await SessionManager().login()
            client = AsyncPocketOption(data["ssid"], allow_real=allow_real)
            region = await client.connect()

        logger.info("Connected to %s (%s)", region.name, "demo" if client.is_demo else "LIVE")
        _client = client
        return _client


# ============================ account & market data ==========================

@mcp.tool()
async def get_balance() -> dict:
    """Get the current account balance and whether it's a demo or live account."""
    po = await _get_client()
    bal = await po.get_balance()
    return {"balance": bal.balance, "is_demo": bal.is_demo, "currency": "USD"}


@mcp.tool()
async def list_assets(
    active_only: bool = True,
    otc_only: bool = False,
    asset_type: str = "",
    search: str = "",
    limit: int = 50,
) -> dict:
    """List tradeable assets with payout %% and available expirations (in seconds).

    Filters: active_only (open for trading now), otc_only (synthetic OTC pairs),
    asset_type ('currency'/'stock'/'crypto'/'commodity'/'index'), search (substring
    of symbol or name). Sorted by payout, capped at `limit`.
    """
    po = await _get_client()
    assets = po.assets(active_only=active_only)
    if otc_only:
        assets = [a for a in assets if a.is_otc]
    if asset_type:
        assets = [a for a in assets if a.type == asset_type]
    if search:
        s = search.lower()
        assets = [a for a in assets if s in a.symbol.lower() or s in a.name.lower()]
    assets = sorted(assets, key=lambda a: -a.payout)[: max(1, limit)]
    return {
        "count": len(assets),
        "assets": [
            {
                "symbol": a.symbol,
                "name": a.name,
                "type": a.type,
                "payout": a.payout,
                "is_otc": a.is_otc,
                "expirations": a.expirations,
            }
            for a in assets
        ],
    }


@mcp.tool()
async def get_asset_info(symbol: str) -> dict:
    """Details for one asset: payout, whether it's open, and allowed expirations."""
    po = await _get_client()
    a = po.asset(symbol)
    if a is None:
        return {"error": f"Unknown asset {symbol!r}. Use list_assets to see valid symbols."}
    return {
        "symbol": a.symbol,
        "name": a.name,
        "type": a.type,
        "payout": a.payout,
        "is_otc": a.is_otc,
        "is_active": a.is_active,
        "expirations": a.expirations,
    }


@mcp.tool()
async def get_candles(asset: str, timeframe: int = 60, count: int = 100) -> dict:
    """Fetch OHLCV candles for analysis.

    ``timeframe`` is the candle size in seconds (60=M1, 300=M5, 900=M15, 3600=H1,
    14400=H4). Returns candles oldest-first, each with time/open/high/low/close/volume.
    Call this for several timeframes to analyse multiple horizons at once.
    """
    po = await _get_client()
    try:
        candles = await po.candles(asset, timeframe, count=count)
    except Exception as exc:
        return {"error": str(exc)}
    return {
        "asset": asset,
        "timeframe": timeframe,
        "count": len(candles),
        "candles": [
            {
                "time": c.time,
                "open": c.open,
                "high": c.high,
                "low": c.low,
                "close": c.close,
                "volume": c.volume,
            }
            for c in candles
        ],
    }


@mcp.tool()
async def get_price(asset: str) -> dict:
    """Get the current live price for an asset (subscribes and waits for a tick)."""
    po = await _get_client()
    price = await po.wait_price(asset, timeout=5.0)
    if price is None:
        return {"asset": asset, "price": None, "note": "no tick received yet"}
    return {"asset": asset, "price": price}


# ================================== trading ==================================

@mcp.tool()
async def place_trade(asset: str, direction: str, amount: float, expiration: int) -> dict:
    """Open a binary-options trade.

    ``direction``: 'call'/'up' (price goes up) or 'put'/'down' (price goes down).
    ``amount``: stake in the account currency.
    ``expiration``: duration in SECONDS — any value the asset allows (e.g. 60, 180,
    300). Check get_asset_info for the allowed list.

    Returns the opened trade including ``request_id``; pass it to check_result to
    learn win/loss. Real-money orders are refused unless the server was started
    with PO_ALLOW_REAL=1.
    """
    po = await _get_client()
    try:
        deal = await po.buy(asset, direction, amount, expiration)
    except Exception as exc:
        return {"error": str(exc)}
    _remember_deal(deal)
    return {
        "request_id": deal.request_id,
        "id": deal.id,
        "asset": deal.asset,
        "direction": deal.direction.value,
        "amount": deal.amount,
        "open_price": deal.open_price,
        "potential_profit": deal.profit,
        "payout_percent": deal.percent_profit,
        "expiration_seconds": expiration,
    }


@mcp.tool()
async def check_result(request_id: int) -> dict:
    """Wait for a placed trade to close, then return win/loss and profit.

    Pass the ``request_id`` returned by place_trade. Blocks until the trade's
    expiration passes.
    """
    po = await _get_client()
    deal = _recent_deals.get(request_id)
    if deal is None:
        return {"error": f"No trade with request_id {request_id} was placed this session."}
    try:
        result = await po.result(deal)
    except Exception as exc:
        return {"error": str(exc)}
    return {
        "request_id": request_id,
        "asset": result.asset,
        "won": result.won,
        "profit": result.profit,
        "open_price": result.open_price,
        "close_price": result.close_price,
    }


# ============================ positions & history ============================

@mcp.tool()
async def open_positions() -> dict:
    """List trades currently open on the account."""
    po = await _get_client()
    deals = po.open_deals()
    return {
        "count": len(deals),
        "positions": [
            {
                "id": d.id,
                "asset": d.asset,
                "direction": d.direction.value,
                "amount": d.amount,
                "open_price": d.open_price,
                "close_timestamp": d.close_timestamp,
            }
            for d in deals
        ],
    }


@mcp.tool()
async def trade_history(limit: int = 20) -> dict:
    """Recent closed trades (most recent first) with win/loss and profit."""
    po = await _get_client()
    deals = po.history(limit=limit)
    return {
        "count": len(deals),
        "history": [
            {
                "asset": d.asset,
                "direction": d.direction.value,
                "amount": d.amount,
                "won": d.won,
                "profit": d.profit,
                "close_timestamp": d.close_timestamp,
            }
            for d in deals
        ],
    }


@mcp.tool()
async def performance() -> dict:
    """Win rate and net profit over the trades seen this session."""
    po = await _get_client()
    return po.stats()


# ============================ strategy memory ================================
# Persistent notebook so you can carry strategies across sessions.
#
# Memory is PER MODEL. Each agent gets its own file, strategies/strategies_<agent>.json,
# keyed by AGENT_NAME. A model only ever reads and writes its own notebook and can
# never see or overwrite another model's strategies. (The opinion board further down
# is the one deliberately shared surface.)

_AGENT_NAME = os.environ.get("AGENT_NAME", "agent")
_strategies = StrategyStore(agent=_AGENT_NAME)
logger.info("Strategy memory for agent %r -> %s", _AGENT_NAME, _strategies.path)


@mcp.tool()
async def list_strategies() -> dict:
    """List the trading strategies YOU saved in previous sessions (your own file only).

    This notebook is private to you (keyed by AGENT_NAME); you never see other models'
    strategies here. Call this at the START of a session to build on past experience
    instead of starting blank. Each entry has your entry logic, the asset/timeframe it
    suited, and its recorded results.
    """
    strategies = _strategies.list()
    return {"agent": _AGENT_NAME, "count": len(strategies), "strategies": strategies}


@mcp.tool()
async def get_strategy(name: str) -> dict:
    """Get one saved strategy by its name."""
    record = _strategies.get(name)
    return record or {"error": f"No saved strategy named {name!r}."}


@mcp.tool()
async def save_strategy(
    name: str,
    description: Optional[str] = None,
    entry_rules: Optional[str] = None,
    assets: Optional[str] = None,
    timeframe: Optional[str] = None,
    trades: Optional[int] = None,
    wins: Optional[int] = None,
    net_profit: Optional[float] = None,
    notes: Optional[str] = None,
) -> dict:
    """Save or update a trading strategy in persistent memory to reuse later.

    Use a clear, unique ``name``. Record the ``entry_rules`` (the exact condition that
    triggers a call/put), which ``assets`` and ``timeframe`` it suits, and its observed
    results (``trades``, ``wins``, ``net_profit``). Add honest ``notes`` — including the
    sample size, since a short good run may be luck rather than an edge. Saving an
    existing name updates only the fields you pass.

    Save a strategy only after it has shown genuinely good results over a meaningful
    number of trades — not after one or two lucky wins.
    """
    record = _strategies.save(
        name,
        description=description,
        entry_rules=entry_rules,
        assets=assets,
        timeframe=timeframe,
        trades=trades,
        wins=wins,
        net_profit=net_profit,
        notes=notes,
    )
    return {"saved": record}


@mcp.tool()
async def delete_strategy(name: str) -> dict:
    """Delete a saved strategy that has proven unreliable."""
    return {"deleted": _strategies.delete(name)}


# ============================== skills / rules ===============================
# A second private notebook, per model, for durable RULES and SKILLS the model
# wants to keep and re-read (distinct from strategies, which are trade setups).
# Same isolation guarantee: your own file only, never another model's.

_skills = SkillStore(agent=_AGENT_NAME)
logger.info("Skills memory for agent %r -> %s", _AGENT_NAME, _skills.path)


@mcp.tool()
async def list_skills() -> dict:
    """List the rules / skills YOU wrote down before (your own private file only).

    Read this at the START of every session together with list_strategies, so you
    remember the rules you set for yourself and don't repeat past mistakes. You never
    see another model's skills here.
    """
    skills = _skills.list()
    return {"agent": _AGENT_NAME, "count": len(skills), "skills": skills}


@mcp.tool()
async def get_skill(name: str) -> dict:
    """Get one saved skill/rule by its name."""
    record = _skills.get(name)
    return record or {"error": f"No saved skill named {name!r}."}


@mcp.tool()
async def save_skill(
    name: str,
    rule: Optional[str] = None,
    category: Optional[str] = None,
    notes: Optional[str] = None,
) -> dict:
    """Save or update a rule/skill in your persistent, private skills file.

    Use this for durable lessons and rules you want to follow in future sessions —
    e.g. 'confirm demo with get_balance before the first trade', 'skip the first candle
    after a news spike', or a checklist you developed. Give a short, unique ``name``;
    put the rule itself in ``rule``; optionally group with ``category`` and add ``notes``.
    Saving an existing name updates only the fields you pass.
    """
    record = _skills.save(name, rule=rule, category=category, notes=notes)
    return {"saved": record}


@mcp.tool()
async def delete_skill(name: str) -> dict:
    """Delete a saved skill/rule that's no longer useful."""
    return {"deleted": _skills.delete(name)}


# ========================= multi-agent opinion board =========================
# A shared board so several connected agents can compare views before trading.
# Opinions expire (TTL) so stale ones from previous runs don't linger.
# (Unlike strategy memory above, this board is intentionally SHARED across agents.)

_opinions = OpinionBoard(ttl_minutes=int(os.environ.get("OPINIONS_TTL_MINUTES", "15")))
_opinions.prune()  # on startup, clear expired opinions left over from previous runs
atexit.register(lambda: _opinions.clear_own(_AGENT_NAME))  # clean up our own on exit


@mcp.tool()
async def read_opinions(asset: str = "") -> dict:
    """Read recent opinions from other connected agents (and your own).

    If other agents are running, use this to compare views before deciding.
    ``other_agents_seen`` lists who else has posted recently — if it's empty, you're
    likely trading solo. Filter to one asset with ``asset``.
    """
    ops = _opinions.read(asset or None)
    others = sorted({o.get("agent") for o in ops if o.get("agent") and o.get("agent") != _AGENT_NAME})
    return {"you": _AGENT_NAME, "other_agents_seen": others, "count": len(ops), "opinions": ops}


@mcp.tool()
async def post_opinion(
    asset: str,
    view: str,
    reasoning: str = "",
    confidence: str = "",
    agent_name: str = "",
) -> dict:
    """Share your view on an asset with other connected agents.

    ``view`` e.g. 'call' / 'put' / 'neutral'; ``reasoning`` your justification;
    ``confidence`` e.g. 'low' / 'medium' / 'high'. Post before important entries when
    other agents are connected, then call read_opinions to see theirs and compare.
    """
    entry = _opinions.post(agent_name or _AGENT_NAME, asset, view, reasoning, confidence)
    return {"posted": entry}


# ---- direct messaging: agents talking to each other ----
# A shared chat (broadcast or addressed to one agent). Messages persist for their
# TTL even after the sender exits, so the recipient still receives them.

_messages = MessageBoard(ttl_minutes=int(os.environ.get("MESSAGES_TTL_MINUTES", "30")))
_messages.prune()  # clear expired messages left over from previous runs


@mcp.tool()
async def send_message(text: str, to: str = "") -> dict:
    """Send a message to other connected agents.

    Leave ``to`` empty to broadcast to everyone, or set it to another agent's name
    (see ``other_agents_seen`` from read_opinions/read_messages) to address one directly.
    Use this to actually coordinate: ask a peer a question, flag a setup, agree a plan,
    or hand off ("you take EURUSD, I'll watch GBPUSD"). Messages expire after their TTL.
    """
    entry = _messages.send(_AGENT_NAME, text, to)
    return {"sent": entry}


@mcp.tool()
async def read_messages(from_agent: str = "") -> dict:
    """Read recent messages sent to you or broadcast to everyone (plus your own).

    Filter to one conversation with ``from_agent``. ``other_agents_seen`` lists who else
    is talking. Check this at the start of a session and before important decisions when
    other agents are connected — a peer may have flagged something for you.
    """
    msgs = _messages.read(_AGENT_NAME, from_agent)
    others = sorted({
        a for m in msgs for a in (m.get("from"), m.get("to"))
        if a and a != _AGENT_NAME
    })
    return {"you": _AGENT_NAME, "other_agents_seen": others, "count": len(msgs), "messages": msgs}


@mcp.tool()
async def clear_opinions() -> dict:
    """Wipe the shared opinion board for a clean slate (e.g. starting a new session)."""
    _opinions.clear()
    return {"cleared": True}


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
