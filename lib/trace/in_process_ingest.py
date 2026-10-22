"""Route `lib.hook_plugin` ingest posts straight into `trace_service`.

The transcript posters only know how to POST to a running `regin serve`. A
CLI import has to work without one, so inside `in_process_ingest()` each post
is dispatched to the same service call the endpoint makes, after the same
span validation. Live-UI announcements are skipped: nobody is watching an
import.
"""

from __future__ import annotations

import json
import urllib.error
from contextlib import contextmanager
from urllib.parse import urlsplit


def _ingest_spans(data) -> None:
    from lib.trace import trace_service
    from web.blueprints.trace.spans_ingest import (
        _ingest_max_attributes_bytes, _validate_span,
    )
    spans = data if isinstance(data, list) else [data]
    max_bytes = _ingest_max_attributes_bytes()
    normalised = []
    for span in spans:
        attrs, reason = _validate_span(span, max_bytes)
        if reason is not None:
            raise ValueError(f'invalid span {span.get("span_id")!r}: {reason}')
        normalised.append((span, attrs))
    trace_service.ingest_session_spans(normalised)


def _ingest_turn_usage(data) -> None:
    from lib.trace import trace_service
    trace_service.ingest_turn_usage(data if isinstance(data, list) else [data])


def _ingest_tool_attribution(data) -> None:
    from lib.trace import trace_service
    trace_service.ingest_tool_attribution(data)


_DISPATCH = {
    '/api/session-spans': _ingest_spans,
    '/api/turn-usage': _ingest_turn_usage,
    '/api/turn-usage/tool-attribution': _ingest_tool_attribution,
}


def _rejected(url: str, reason: str) -> urllib.error.HTTPError:
    # A 4xx so post_event does not retry: the same payload fails the same way.
    return urllib.error.HTTPError(url, 422, reason, None, None)


def _dispatcher(errors: list[str]):
    def dispatch(url: str, body: bytes):
        handler = _DISPATCH.get(urlsplit(url).path)
        if handler is None:
            reason = f'no in-process ingest for {url}'
        else:
            try:
                handler(json.loads(body))
                return True, None
            except Exception as exc:  # noqa: BLE001 — post_event never raises
                reason = f'{type(exc).__name__}: {exc}'
        errors.append(reason)
        return False, _rejected(url, reason)
    return dispatch


@contextmanager
def in_process_ingest():
    """Yields the list of rejected posts, so a caller can tell an import
    that landed from one that only ran."""
    from lib import hook_plugin
    errors: list[str] = []
    previous = hook_plugin._transport
    hook_plugin._transport = _dispatcher(errors)
    try:
        yield errors
    finally:
        hook_plugin._transport = previous
