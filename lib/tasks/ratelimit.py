"""Per-API-key sliding-window rate limit for the capture endpoint.

Same shape as `lib/agent_bridge/delivery._rate_ok`: a deque of timestamps per
key under one lock, and an LRU-capped dict so a key that stops calling is
evicted instead of retained forever.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict, deque

from lib.settings import settings

_LOCK = threading.Lock()
_HISTORY: "OrderedDict[str, deque]" = OrderedDict()
_MAX_TRACKED_KEYS = 4096


def capture_allowed(key_id) -> bool:
    """Check/record one capture attempt under the lock, then release — no I/O
    or sleep is done while holding it."""
    now = time.monotonic()
    limit = settings.tasks.capture_rate_limit_per_minute
    bucket = str(key_id)
    with _LOCK:
        hist = _HISTORY.get(bucket) or deque()
        while hist and now - hist[0] >= 60.0:
            hist.popleft()
        allowed = len(hist) < limit
        if allowed:
            hist.append(now)
        if hist:
            _HISTORY[bucket] = hist
            _HISTORY.move_to_end(bucket)
            while len(_HISTORY) > _MAX_TRACKED_KEYS:
                _HISTORY.popitem(last=False)
        else:
            _HISTORY.pop(bucket, None)
        return allowed
