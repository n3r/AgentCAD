"""Per-project turn locks and client identity plumbing (stdlib only).

A turn lock is advisory-but-enforced: any client may acquire the turn on a
project; while it is held, persistent writes by *other* clients are rejected
with a ConflictError naming the holder, until release or TTL expiry. With no
lock held, nothing changes — every write works exactly as before.

Identity travels on a ContextVar so it flows naturally through the service
layer regardless of entry point (HTTP middleware sets it per request from the
``X-Agent-Id`` header; the chat engine sets it to ``"chat"`` inside its tool
executor; plain library use defaults to ``"local"``).
"""

from __future__ import annotations

import contextvars
import threading
import time

from .model import ConflictError

DEFAULT_TTL_S = 120.0
MIN_TTL_S = 5.0
MAX_TTL_S = 3600.0

client_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "agentcad_client_id", default="local"
)


def current_client_id() -> str:
    """The calling context's client identity ("local" when never set)."""
    return client_id_var.get()


def set_client_id(cid: str) -> None:
    """Set the calling context's client identity."""
    client_id_var.set(cid)


class TurnLock:
    """Thread-safe per-project turn locks with wall-clock TTL expiry."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # project -> (holder, expires_at); expired entries are treated as free.
        self._held: dict[str, tuple[str, float]] = {}

    @staticmethod
    def _conflict(holder: str, expires_at: float) -> ConflictError:
        return ConflictError(
            f"project is locked by {holder}",
            {"holder": holder, "expires_at": expires_at},
        )

    def acquire(self, project: str, holder: str,
                ttl_s: float = DEFAULT_TTL_S) -> dict:
        """Take (or refresh) the turn. Succeeds when the lock is free, expired,
        or already held by ``holder``; otherwise raises ConflictError."""
        ttl = min(max(float(ttl_s), MIN_TTL_S), MAX_TTL_S)
        now = time.time()
        with self._lock:
            current = self._held.get(project)
            if current is not None:
                other, expires_at = current
                if other != holder and expires_at > now:
                    raise self._conflict(other, expires_at)
            expires_at = now + ttl
            self._held[project] = (holder, expires_at)
            return {"holder": holder, "expires_at": expires_at}

    def release(self, project: str, holder: str) -> dict:
        """Release the turn. Not held (or expired) is a no-op returning
        ``{"released": False}``; held by another raises ConflictError."""
        now = time.time()
        with self._lock:
            current = self._held.get(project)
            if current is None or current[1] <= now:
                self._held.pop(project, None)
                return {"released": False}
            other, expires_at = current
            if other != holder:
                raise self._conflict(other, expires_at)
            del self._held[project]
            return {"released": True}

    def get(self, project: str) -> dict | None:
        """Current lock info, or None when free/expired."""
        now = time.time()
        with self._lock:
            current = self._held.get(project)
            if current is None:
                return None
            holder, expires_at = current
            if expires_at <= now:
                del self._held[project]
                return None
            return {"holder": holder, "expires_at": expires_at}

    def check(self, project: str, client_id: str) -> None:
        """Raise ConflictError when the turn is held by someone else and
        unexpired. Free, expired, or own lock: fine."""
        now = time.time()
        with self._lock:
            current = self._held.get(project)
            if current is None:
                return
            holder, expires_at = current
            if holder != client_id and expires_at > now:
                raise self._conflict(holder, expires_at)
