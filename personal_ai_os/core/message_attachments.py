"""Binary attachments for the current user message (e.g. Excel from Engineer tool).

Collected during MetaAgent → sub-agent tool runs; drained by the Telegram handler.
"""

from __future__ import annotations

from contextvars import ContextVar

_var: ContextVar[list[tuple[str, bytes]] | None] = ContextVar("message_attachments", default=None)


def attachments_begin() -> None:
    """Start collecting attachments for this request (call once per incoming message)."""
    _var.set([])


def attachments_append(filename: str, content: bytes) -> None:
    bucket = _var.get()
    if bucket is None:
        return
    bucket.append((filename, content))


def attachments_drain() -> list[tuple[str, bytes]]:
    """Return pending files and clear the bucket."""
    lst = _var.get()
    _var.set(None)
    return list(lst) if lst else []
