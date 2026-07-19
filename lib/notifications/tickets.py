"""Short-lived single-use tickets for the badge event stream.

`EventSource` cannot set an Authorization header, so the credential has to
ride in the query string — where the werkzeug access log records it verbatim,
and where browser history and any intermediate proxy keep it too. Handing out
a 30-second single-use ticket instead of the JWT bounds that exposure: by the
time anyone reads the log line the ticket is already spent or expired, and it
was never a bearer credential for anything else.

Expired entries are reaped on the next issue/redeem rather than by a timer.
"""

from __future__ import annotations

import secrets
import threading
import time

TTL_SECONDS = 30

# `_purge` is O(n) under the global lock, so every handshake pays for whatever
# is outstanding. A 30-second window makes a legitimate backlog tiny; the cap
# bounds the pathological case (a caller minting tickets it never spends)
# instead of letting it serialise every connect. Oldest go first — they are
# the closest to expiry anyway.
MAX_OUTSTANDING = 512

_lock = threading.Lock()
_tickets: dict[str, tuple[int, float]] = {}


def issue(user_id: int) -> str:
    """Mint a ticket for `user_id`, valid once and only for `TTL_SECONDS`."""
    ticket = secrets.token_urlsafe(32)
    now = time.monotonic()
    with _lock:
        _purge(now)
        _tickets[ticket] = (user_id, now + TTL_SECONDS)
        _evict_overflow()
    return ticket


def redeem(ticket: str) -> int | None:
    """Spend a ticket, returning its user id — or None if it is unknown,
    already spent, or expired."""
    if not ticket:
        return None
    now = time.monotonic()
    with _lock:
        _purge(now)
        entry = _tickets.pop(ticket, None)
    if entry is None:
        return None
    user_id, expires_at = entry
    return user_id if expires_at > now else None


def outstanding() -> int:
    with _lock:
        return len(_tickets)


def _purge(now: float) -> None:
    for key in [k for k, (_, expires_at) in _tickets.items() if expires_at <= now]:
        del _tickets[key]


def _evict_overflow() -> None:
    while len(_tickets) > MAX_OUTSTANDING:
        del _tickets[next(iter(_tickets))]


__all__ = ["issue", "redeem", "outstanding", "TTL_SECONDS", "MAX_OUTSTANDING"]
