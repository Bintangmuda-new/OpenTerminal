"""
cmp_server_pocket_option_2026.memory
=======================

Persistent "strategy memory" for the agent — a lightweight, file-backed store so a
model can write down strategies that worked and reuse them in future sessions
(a simple form of the "skills" pattern: durable knowledge kept in a file rather
than only in the prompt).

Strategies live in ``strategies/strategies.json`` at the project root. This is the
model's own notebook — it is NOT a secret (no account token), so you can keep or
share it if you like.

Concurrency
-----------
Both stores here can be shared by several agents at once (the whole point of the
opinion board). Every write is therefore:

* **guarded by a cross-process file lock** (``<file>.lock``), so two agents never
  interleave a read-modify-write and lose each other's data, and
* **atomic** — we write to a temp file in the same directory and ``os.replace`` it
  over the target, so a crash mid-write can never leave a half-written JSON file.

Honest note: remembering strategies helps consistency and learning, but it does NOT
create profitability. A good run on OTC pairs is often luck, not an edge, so records
should include the sample size and honest caveats, not "guaranteed winner" claims.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

# --------------------------------------------------------------------------- utils

@contextmanager
def _file_lock(target: Path, *, timeout: float = 10.0, poll: float = 0.05) -> Iterator[None]:
    """A tiny, dependency-free cross-process lock based on ``O_CREAT | O_EXCL``.

    Creating ``<target>.lock`` exclusively is atomic on every OS we care about, so
    only one holder can exist at a time. If we can't acquire it within ``timeout``
    we take the lock over rather than dead-lock the caller — a stale lock left by a
    killed process must never wedge the whole system.
    """
    lock_path = target.with_suffix(target.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    fd: Optional[int] = None
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                try:
                    lock_path.unlink()  # steal a stale lock, then try once more
                except OSError:
                    pass
                deadline = time.monotonic() + timeout
                continue
            time.sleep(poll)
    try:
        yield
    finally:
        try:
            if fd is not None:
                os.close(fd)
            lock_path.unlink()
        except OSError:
            pass


def _atomic_write_json(path: Path, data: Any) -> None:
    """Serialise ``data`` and replace ``path`` atomically (temp file + os.replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)  # atomic on POSIX and Windows
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass


def _read_json(path: Path, default: Any) -> Any:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default
    return data


# ------------------------------------------------------------------ strategy store

def _slug(name: Optional[str]) -> str:
    """Filesystem-safe, lowercase slug for an agent name (e.g. 'GPT 4o' -> 'gpt_4o')."""
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", (name or "").strip()).strip("_")
    return s.lower() or "default"


def _named_path(prefix: str, agent: Optional[str] = None) -> Path:
    """Per-agent file path, e.g. strategies/strategies_gpt.json or skills/skills_grok.json."""
    root = Path(__file__).resolve().parent.parent  # project root (parent of package)
    fname = f"{prefix}_{_slug(agent)}.json" if agent else f"{prefix}.json"
    return root / prefix / fname


class _NamedJsonStore:
    """Base for a per-model notebook of named records (lock-guarded, atomic writes).

    Memory is **per model**: pass ``agent`` and the store lives in its own file,
    ``<prefix>/<prefix>_<agent>.json``. A given store only ever touches that one file,
    so one model can never read or overwrite another model's notebook.
    """

    _prefix = "store"

    def __init__(self, path: Optional[str] = None, agent: Optional[str] = None) -> None:
        self.agent = agent
        self.path = Path(path) if path else _named_path(self._prefix, agent)

    def _load(self) -> dict:
        data = _read_json(self.path, {})
        return data if isinstance(data, dict) else {}

    def save(self, name: str, **fields: Any) -> dict:
        """Create or update a record by name. Only provided (non-None) fields change."""
        with _file_lock(self.path):
            data = self._load()
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            record = dict(data.get(name, {}))
            record.update({k: v for k, v in fields.items() if v is not None})
            record["name"] = name
            record.setdefault("created_at", now)
            record["updated_at"] = now
            data[name] = record
            _atomic_write_json(self.path, data)
        return record

    def list(self) -> list:
        return list(self._load().values())

    def get(self, name: str) -> Optional[dict]:
        return self._load().get(name)

    def delete(self, name: str) -> bool:
        with _file_lock(self.path):
            data = self._load()
            if name in data:
                del data[name]
                _atomic_write_json(self.path, data)
                return True
        return False


class StrategyStore(_NamedJsonStore):
    """A model's private notebook of named trading strategies (per-agent file)."""

    _prefix = "strategies"


class SkillStore(_NamedJsonStore):
    """A model's private notebook of learned rules / skills (per-agent file).

    Same mechanics as StrategyStore, but for durable *rules and skills* the model
    wants to keep and re-read: 'always confirm demo before first trade', 'skip the
    first candle after a news spike', a checklist it developed, etc.
    """

    _prefix = "skills"


# ============================================================================
#  Multi-agent opinion board — a shared file where connected agents post their
#  view on an asset and read each other's, to compare before trading.
# ============================================================================

def _opinions_default_path() -> Path:
    return Path(__file__).resolve().parent.parent / "opinions.json"


class OpinionBoard:
    """Shared, file-backed board of agent opinions with time-to-live freshness.

    Opinions older than ``ttl_minutes`` are ignored and pruned, so stale entries
    from previous runs never pollute a new session. Multiple agents share the same
    file safely: every mutation takes the file lock and writes atomically, so no
    agent can clobber another's post through an interleaved read-modify-write.
    """

    def __init__(self, path: Optional[str] = None, ttl_minutes: int = 15) -> None:
        self.path = Path(path) if path else _opinions_default_path()
        self.ttl = timedelta(minutes=ttl_minutes)

    def _load(self) -> list:
        data = _read_json(self.path, [])
        return data if isinstance(data, list) else []

    def _fresh(self, items: list) -> list:
        cutoff = datetime.now(timezone.utc) - self.ttl
        fresh = []
        for it in items:
            try:
                ts = datetime.fromisoformat(it["timestamp"])
            except (KeyError, ValueError, TypeError):
                continue
            if ts >= cutoff:
                fresh.append(it)
        return fresh

    def prune(self) -> None:
        """Drop expired opinions (e.g. call once on startup to clear old-run garbage)."""
        with _file_lock(self.path):
            _atomic_write_json(self.path, self._fresh(self._load()))

    def post(self, agent: str, asset: str, view: str,
             reasoning: str = "", confidence: str = "") -> dict:
        entry = {
            "agent": agent,
            "asset": asset,
            "view": view,
            "reasoning": reasoning,
            "confidence": confidence,
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        with _file_lock(self.path):
            items = self._fresh(self._load())
            items.append(entry)
            _atomic_write_json(self.path, items)
        return entry

    def read(self, asset: Optional[str] = None) -> list:
        items = self._fresh(self._load())
        if asset:
            items = [i for i in items if i.get("asset") == asset]
        return items

    def clear(self) -> None:
        """Wipe the whole board (manual reset)."""
        with _file_lock(self.path):
            _atomic_write_json(self.path, [])

    def clear_own(self, agent: str) -> None:
        """Remove only this agent's opinions (clean up after yourself on exit)."""
        with _file_lock(self.path):
            kept = [i for i in self._fresh(self._load()) if i.get("agent") != agent]
            _atomic_write_json(self.path, kept)


# ============================================================================
#  Inter-agent messages — a shared "chat" so connected agents can talk directly:
#  broadcast to everyone, or address one agent by name. Same safety as the
#  opinion board (file lock + atomic writes + TTL). Unlike opinions, a sender's
#  messages are NOT wiped on exit — they stay readable (within TTL) so the
#  recipient still sees them after the sender disconnects.
# ============================================================================

def _messages_default_path() -> Path:
    return Path(__file__).resolve().parent.parent / "messages.json"


class MessageBoard:
    """Shared, file-backed direct messaging between agents, with TTL freshness."""

    def __init__(self, path: Optional[str] = None, ttl_minutes: int = 30) -> None:
        self.path = Path(path) if path else _messages_default_path()
        self.ttl = timedelta(minutes=ttl_minutes)

    def _load(self) -> list:
        data = _read_json(self.path, [])
        return data if isinstance(data, list) else []

    def _fresh(self, items: list) -> list:
        cutoff = datetime.now(timezone.utc) - self.ttl
        fresh = []
        for it in items:
            try:
                ts = datetime.fromisoformat(it["timestamp"])
            except (KeyError, ValueError, TypeError):
                continue
            if ts >= cutoff:
                fresh.append(it)
        return fresh

    def prune(self) -> None:
        """Drop expired messages (e.g. call once on startup)."""
        with _file_lock(self.path):
            _atomic_write_json(self.path, self._fresh(self._load()))

    def send(self, sender: str, text: str, to: str = "") -> dict:
        """Post a message. ``to`` empty = broadcast to everyone; else an agent name."""
        entry = {
            "from": sender,
            "to": (to or "").strip(),
            "text": text,
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        with _file_lock(self.path):
            items = self._fresh(self._load())
            items.append(entry)
            _atomic_write_json(self.path, items)
        return entry

    def read(self, agent: str, peer: str = "") -> list:
        """Messages visible to ``agent``: broadcasts + those to/from ``agent``.

        Pass ``peer`` to narrow to the conversation between ``agent`` and that peer.
        """
        items = self._fresh(self._load())
        visible = [
            m for m in items
            if not m.get("to") or m.get("to") == agent or m.get("from") == agent
        ]
        if peer:
            visible = [m for m in visible if m.get("from") == peer or m.get("to") == peer]
        return visible

    def clear(self) -> None:
        """Wipe the whole message log (manual reset)."""
        with _file_lock(self.path):
            _atomic_write_json(self.path, [])
