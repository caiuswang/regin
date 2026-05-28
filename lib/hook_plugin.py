"""Unified hook plugin for Claude Code trace hooks.

Provides payload parsing, trace emission, response formatting, and span lifecycle
management so that each hook script can focus on business logic.

Typical usage in a hook script:

    from lib.hook_plugin import HookContext, emit_response

    ctx = HookContext(expected_event='PostToolUse')
    if ctx.skipped:
        emit_response(ctx.hook_event or 'Unknown', 'skipped', suppress_output=True)
        sys.exit(0)

    ctx.post_span(
        name=f"tool.{ctx.tool_name}",
        attributes={'tool_name': ctx.tool_name},
    )
    emit_response(ctx.hook_event, f"traced {ctx.tool_name}")
"""

import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime

from lib.providers import build_provider, get_active_provider, is_provider_id, resolve_provider

_ACTIVE_PROVIDER = get_active_provider()
_INGEST_ERROR_LOG = os.path.join(str(_ACTIVE_PROVIDER.traces_dir()), 'ingest-errors.jsonl')

# ── Shared hook helpers (previously duplicated across hook scripts) ────
# Keep these at module level so all trace hooks share the same definition.
# The regex previously had an UNESCAPED leading dot, which silently matched
# any character — so e.g. `xclaude/skills/foo/content.md` was accepted as
# a skill read. The escape fix is covered by tests/test_hook_plugin.py.

PLANS_DIR = str(_ACTIVE_PROVIDER.plans_dir())


def provider_from_agent_type(agent_type: str | None):
    if is_provider_id(agent_type):
        return build_provider(str(agent_type))
    return _ACTIVE_PROVIDER


def provider_from_payload(payload: dict | None):
    if isinstance(payload, dict):
        try:
            return resolve_provider(payload)
        except Exception:
            pass
    return _ACTIVE_PROVIDER


def ingest_error_log_path(provider=None) -> str:
    if provider is None or provider.provider_id == _ACTIVE_PROVIDER.provider_id:
        return _INGEST_ERROR_LOG
    return os.path.join(str(provider.traces_dir()), 'ingest-errors.jsonl')


def find_latest_plan(plans_dir: str = PLANS_DIR) -> str | None:
    """Return the filename of the most-recently-modified plan, or None."""
    if not os.path.isdir(plans_dir):
        return None
    latest: str | None = None
    latest_mtime = 0.0
    for fname in os.listdir(plans_dir):
        if not fname.endswith('.md'):
            continue
        fpath = os.path.join(plans_dir, fname)
        try:
            mtime = os.stat(fpath).st_mtime
        except OSError:
            continue
        if mtime > latest_mtime:
            latest_mtime = mtime
            latest = fname
    return latest


def normalize_tool_name(tool_name: str) -> str:
    """Normalize tool names for safe span naming (no dots, slashes, colons)."""
    return tool_name.replace('.', '_').replace(':', '_').replace('/', '_')


def extract_file_path(tool_input: dict, tool_response: dict) -> str | None:
    """Preferred order: tool_response.filePath, then tool_input.file_path."""
    for candidate in (
        (tool_response or {}).get('filePath'),
        (tool_input or {}).get('file_path'),
    ):
        if candidate and isinstance(candidate, str):
            return candidate
    return None


def skill_id_from_read_path(file_path: str, home: str | None = None, agent_type: str | None = None) -> str | None:
    """Return the skill id if `file_path` is a deployed skill content.md,
    else None. Handles the home-directory normalization that every hook
    previously re-implemented inline.
    """
    return provider_from_agent_type(agent_type).skill_id_from_read_path(file_path, home=home)


def build_tool_attributes(tool_name: str, tool_input: dict) -> dict:
    """Generic per-tool attribute bag for tool.{name} / pre_tool.{name} spans."""
    attrs: dict = {
        'tool_name': tool_name,
        'tool_input_keys': list((tool_input or {}).keys()),
    }
    if tool_name == 'Bash':
        desc = (tool_input or {}).get('command') or (tool_input or {}).get('description') or ''
        attrs['command_preview'] = str(desc)[:200]
    elif tool_name == 'Skill':
        attrs['skill_name'] = (tool_input or {}).get('skill', '')
    elif tool_name.startswith('mcp__'):
        attrs['mcp_tool'] = tool_name
    return attrs


# Default hard cap on ingest-errors.jsonl size. A sustained failure mode
# (server down, broken producer in a loop) would otherwise let this file
# grow unbounded and eventually fill the disk. One rotation is kept as
# `.1` so recent history survives the cut; older `.1` gets overwritten.
_INGEST_LOG_MAX_BYTES_DEFAULT = 1 * 1024 * 1024  # 1 MB


def _ingest_log_max_bytes() -> int:
    """Read the log-size cap from env with a floor of 1 KB to avoid
    degenerate configurations where every write causes a rotation."""
    raw = os.environ.get('REGIN_INGEST_LOG_MAX_BYTES')
    if raw:
        try:
            v = int(raw)
            if v >= 1024:
                return v
        except ValueError:
            pass
    return _INGEST_LOG_MAX_BYTES_DEFAULT


def _rotate_ingest_log_if_needed(path: str) -> None:
    """Rotate `path` → `path + '.1'` when size ≥ the configured cap.

    Best-effort: any OSError (missing file, permission issue, concurrent
    rotation from another hook process) is swallowed so it never breaks
    the write path.
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        return
    if size < _ingest_log_max_bytes():
        return
    backup = path + '.1'
    try:
        # Remove any stale backup first; os.replace on the active file
        # atomically moves it to `.1` and leaves the original missing,
        # so the next write recreates it fresh.
        try:
            os.remove(backup)
        except OSError:
            pass
        os.replace(path, backup)
    except OSError:
        pass


def _log_ingest_error(endpoint: str, url: str, exc: BaseException,
                      attempt: int = 1, max_attempts: int = 1,
                      gave_up: bool = False,
                      provider=None) -> None:
    """Append a single JSON line describing an ingest failure. Best-effort.

    `attempt` / `max_attempts` / `gave_up` let operators distinguish transient
    retryable failures from permanent give-ups when triaging the log. The
    log is rotated once it hits `_ingest_log_max_bytes()` so an outage
    can't grow it without bound.
    """
    try:
        path = ingest_error_log_path(provider)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        _rotate_ingest_log_if_needed(path)
        entry: dict = {
            'timestamp': datetime.now().isoformat(),
            'endpoint': endpoint,
            'url': url,
            'error_type': type(exc).__name__,
            'error': str(exc),
            'attempt': attempt,
            'max_attempts': max_attempts,
            'gave_up': gave_up,
        }
        if isinstance(exc, urllib.error.HTTPError):
            entry['http_status'] = exc.code
        with open(path, 'a') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
    except OSError:
        pass

class _Unset:
    """Sentinel for distinguishing 'not provided' from None."""
    pass

_UNSET = _Unset()

# Ingest endpoint base URL. Default port comes from this checkout's
# `settings.web_port` so two regin instances on one machine don't cross-post
# spans into whichever one happens to own 8321. Override the whole base with
# `REGIN_INGEST_BASE_URL` or override individual endpoints with
# `REGIN_<ENDPOINT>_TRACE_URL` for advanced setups.
def _default_ingest_base() -> str:
    override = os.environ.get('REGIN_INGEST_BASE_URL')
    if override:
        return override.rstrip('/')
    from lib.settings import settings as _regin_settings
    return f'http://127.0.0.1:{_regin_settings.web_port}'.rstrip('/')


_DEFAULT_BASE = _default_ingest_base()

_ENDPOINT_PATHS = {
    'session_spans': '/api/session-spans',
    'skill_reads': '/api/skill-reads',
    'plan_sessions': '/api/plan-sessions',
    'rule_triggers': '/api/rule-triggers',
    'turn_usage': '/api/turn-usage',
    'tool_attribution': '/api/turn-usage/tool-attribution',
    'prompt_images': '/api/prompt-images',
}

DEFAULT_URLS = {name: _DEFAULT_BASE + path for name, path in _ENDPOINT_PATHS.items()}


def _get_url(endpoint: str) -> str | None:
    env_var = f"REGIN_{endpoint.upper()}_TRACE_URL"
    # Legacy env-var fallback for rule_triggers endpoint
    legacy_env_var = None
    if endpoint == 'rule_triggers':
        legacy_env_var = 'REGIN_RULE_TRACE_URL'
    for ev in (env_var, legacy_env_var):
        if ev and ev in os.environ:
            return os.environ[ev]
    return DEFAULT_URLS.get(endpoint)


def emit_response(hook_event_name: str, additional_context: str, suppress_output: bool = True) -> None:
    """Emit the JSON hook response to stdout."""
    resp = {
        'hookSpecificOutput': {
            'hookEventName': hook_event_name,
            'additionalContext': additional_context,
        },
        'suppressOutput': suppress_output,
    }
    json.dump(resp, sys.stdout)
    sys.stdout.write('\n')
    sys.stdout.flush()


def _env_int(name: str, default: int, lo: int = 0, hi: int = 60000) -> int:
    """Read a positive int env var, clamped to [lo, hi]; returns default on error."""
    raw = os.environ.get(name)
    if raw is None or raw == '':
        return default
    try:
        v = int(raw)
    except ValueError:
        return default
    return max(lo, min(hi, v))


def _is_retryable(exc: BaseException) -> bool:
    """Classify an ingest error as transient (retryable) vs permanent.

    HTTP 4xx means the caller sent something the server can't accept — retrying
    with the same payload won't help, so don't waste the latency budget. HTTP
    5xx, network errors, and timeouts are all treated as transient.
    """
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code >= 500
    if isinstance(exc, urllib.error.URLError):
        return True
    if isinstance(exc, OSError):  # ConnectionRefused, timeout, etc.
        return True
    return False


_JITTER_LOW = 0.5
_JITTER_HIGH = 1.5

# Cache of Random() instances keyed by seed string. When the test or
# operator sets REGIN_INGEST_BACKOFF_JITTER_SEED, we keep ONE stateful
# generator for that seed so successive calls produce a deterministic
# *sequence* (not the same value every time, which would recreate the
# thundering-herd problem jitter is meant to solve).
_jitter_rngs: dict[str, random.Random] = {}


def _get_jitter_rng():
    seed = os.environ.get('REGIN_INGEST_BACKOFF_JITTER_SEED')
    if seed is None:
        return random  # module-level RNG, uses OS entropy
    rng = _jitter_rngs.get(seed)
    if rng is None:
        rng = random.Random(seed)
        _jitter_rngs[seed] = rng
    return rng


def _reset_jitter_rngs() -> None:
    """Clear the seed-to-RNG cache. Test-only hook so one test's seeded
    sequence can't leak into the next."""
    _jitter_rngs.clear()


def _jittered_backoff_ms(base_ms: int) -> int:
    """Return `base_ms` multiplied by a uniform random factor in
    [_JITTER_LOW, _JITTER_HIGH]. With many hooks failing in lockstep
    (e.g. during a Flask restart), deterministic backoff makes them
    all retry at the same instants and pile onto the recovering
    server. A ±50% jitter spreads the retries so the server sees a
    trickle instead of a wave.

    When `base_ms` is 0 (no backoff configured), returns 0 without
    invoking the RNG.

    Setting `REGIN_INGEST_BACKOFF_JITTER_SEED` pins the jitter sequence
    so tests and field reproductions can replay the exact retry
    cadence. The cached generator advances its state across calls.
    """
    if base_ms <= 0:
        return 0
    return int(base_ms * _get_jitter_rng().uniform(_JITTER_LOW, _JITTER_HIGH))


# Built once so every span post reuses the same opener. ProxyHandler({})
# disables env-driven proxies (HTTP_PROXY / HTTPS_PROXY / ALL_PROXY) for
# ingest traffic: hooks always talk to a localhost ingest endpoint, and
# any inherited AI-CLI proxy (e.g. CLIPROXY at 127.0.0.1:15501) will
# 403-Forbid the loopback POST, silently dropping every span. Hooks fire
# from subprocesses spawned by the web server, which inherits those env
# vars from the user's shell, so this isn't a hypothetical case.
_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _post_once(url: str, body: bytes, timeout_sec: float) -> tuple[bool, BaseException | None]:
    """Single POST attempt. Returns (ok, exc)."""
    try:
        req = urllib.request.Request(
            url,
            data=body,
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        _NO_PROXY_OPENER.open(req, timeout=timeout_sec).read()
        return True, None
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
        return False, exc


def post_event(endpoint: str, data: dict | list, agent_type: str | None = None) -> bool:
    """Best-effort POST to an ingest endpoint, with bounded retry.

    Retries transient failures (network errors, HTTP 5xx) up to
    `REGIN_INGEST_RETRIES` times (default 3) with exponential backoff starting
    at `REGIN_INGEST_BACKOFF_MS` (default 100 ms). Each attempt uses a
    `REGIN_INGEST_TIMEOUT_MS` (default 500 ms) timeout. Worst-case total
    latency is bounded by `retries * timeout + sum(backoffs)`.

    4xx responses are NOT retried — they indicate a payload the server
    rejected, and retrying will just burn the latency budget before
    failing again.

    Every failed attempt is recorded to `ingest-errors.jsonl`; the final
    attempt carries `gave_up: true` so operators can distinguish noise
    from permanent data loss. Never raises.

    Returns True iff a request attempt completed with HTTP 2xx; False on
    every other outcome (no URL configured, payload not serializable,
    all retries exhausted). Callers can use this to gate state mutations
    that would otherwise lock out re-processing — e.g. turn_trace's
    seen-uuid cache: marking a uuid seen after a failed post would
    permanently lose the span.
    """
    url = _get_url(endpoint)
    provider = provider_from_agent_type(agent_type)
    if not url:
        _log_ingest_error(endpoint, '',
                          ValueError(f'no URL configured for endpoint {endpoint!r}'),
                          gave_up=True,
                          provider=provider)
        return False

    max_attempts = max(1, _env_int('REGIN_INGEST_RETRIES', 3, lo=1, hi=10))
    timeout_sec = _env_int('REGIN_INGEST_TIMEOUT_MS', 500, lo=50, hi=10000) / 1000.0
    backoff_ms = _env_int('REGIN_INGEST_BACKOFF_MS', 100, lo=0, hi=5000)

    try:
        body = json.dumps(data).encode('utf-8')
    except (TypeError, ValueError) as exc:
        # Payload can't even be serialized — nothing to retry.
        _log_ingest_error(endpoint, url, exc, gave_up=True, provider=provider)
        return False

    for attempt in range(1, max_attempts + 1):
        ok, exc = _post_once(url, body, timeout_sec)
        if ok:
            return True
        # _post_once returns ok=False only from the except branch, so exc
        # is always populated here — but keep the guard for the type
        # checker's benefit.
        if exc is None:
            return False
        retryable = _is_retryable(exc)
        final = attempt == max_attempts or not retryable
        _log_ingest_error(endpoint, url, exc, attempt=attempt,
                          max_attempts=max_attempts, gave_up=final,
                          provider=provider)
        if final:
            return False
        # Exponential backoff with per-attempt multiplier, jittered so
        # a burst of simultaneous hook failures doesn't produce a
        # synchronised retry wave at the recovering server.
        if backoff_ms:
            base = backoff_ms * (2 ** (attempt - 1))
            time.sleep(_jittered_backoff_ms(base) / 1000.0)
    return False


# Set once per hook subprocess by the runner (or by tests). build_span
# auto-injects it into every span's attrs so the ingest-side fallback
# in `lib/trace/trace_service/ingest.py` can pick up agent_type even if
# the `session.start` span never makes it (Claude Code resume / compact
# sometimes skips that event). Cheap: one module global + one setdefault
# per span build.
_active_agent_type: str | None = None


def set_active_agent_type(value: str | None) -> None:
    global _active_agent_type
    _active_agent_type = value.strip() if isinstance(value, str) and value.strip() else None


def build_span(
    trace_id: str,
    name: str,
    attributes: dict | None = None,
    parent_id: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    duration_ms: int = 0,
    status_code: str = 'OK',
    span_id: str | None = None,
) -> dict:
    """Build an OTel-inspired span dict.

    If the env var `REGIN_TRACE_TEST` is truthy when a hook process runs, every
    span produced will carry `is_test: True` in its attributes so that the
    `/api/sessions` endpoint (and anything downstream) can filter those
    sessions out by default.
    """
    now = datetime.now().isoformat()
    resolved_span_id = span_id or uuid.uuid4().hex[:16]
    attrs = dict(attributes) if attributes else {}
    if _active_agent_type is not None:
        attrs.setdefault('agent_type', _active_agent_type)
    if os.environ.get('REGIN_TRACE_TEST', '').lower() in ('1', 'true', 'yes'):
        attrs['is_test'] = True
        test_name = os.environ.get('REGIN_TRACE_TEST_NAME', '').strip()
        if test_name:
            attrs['test_name'] = test_name
    return {
        'trace_id': trace_id,
        'span_id': resolved_span_id,
        'parent_id': parent_id,
        'name': name,
        'kind': 'internal',
        'start_time': start_time or now,
        'end_time': end_time or now,
        'duration_ms': duration_ms,
        'attributes': attrs,
        'status_code': status_code,
    }


def post_span(
    trace_id: str | None,
    name: str,
    attributes: dict | None = None,
    parent_id: str | None = None,
    duration_ms: int = 0,
    start_time: str | None = None,
    end_time: str | None = None,
    status_code: str = 'OK',
    span_id: str | None = None,
) -> bool:
    """Build and POST a single session span.

    Returns True iff the ingest accepted the span (HTTP 2xx). Callers
    that gate state on persistence (e.g. turn_trace's seen-uuid cache)
    should check this; callers that don't care can ignore it.
    """
    if not trace_id:
        return False
    span = build_span(
        trace_id=trace_id,
        name=name,
        attributes=attributes,
        parent_id=parent_id,
        start_time=start_time,
        end_time=end_time,
        duration_ms=duration_ms,
        status_code=status_code,
        span_id=span_id,
    )
    return post_event('session_spans', span)


class HookContext:
    """Parsed Claude Code hook payload with helper methods."""

    def __init__(self, expected_event: str | None = None):
        self.skipped = False
        self.payload = self._read_payload()
        self.hook_event = self.payload.get('hook_event_name')
        self.tool_name = self.payload.get('tool_name')
        self.session_id = self.payload.get('session_id')
        self.prompt = self._extract_prompt()
        self.tool_input = self.payload.get('tool_input') or {}
        self.tool_response = self.payload.get('tool_response') or {}

        if expected_event and self.hook_event != expected_event:
            self.skipped = True

    def _read_payload(self) -> dict:
        try:
            return json.load(sys.stdin)
        except (json.JSONDecodeError, ValueError):
            return {}

    def _extract_prompt(self) -> str:
        """Extract user prompt text from various possible payload fields.

        Claude Code sends the prompt in different fields depending on context
        (regular chat, plan review text area, tool confirmation, etc.).
        """
        p = self.payload
        candidates = [
            p.get('prompt'),
            p.get('text'),
            p.get('message'),
            (p.get('tool_input') or {}).get('text'),
            (p.get('tool_input') or {}).get('message'),
            (p.get('tool_input') or {}).get('prompt'),
            (p.get('tool_input') or {}).get('description'),
            p.get('input'),
        ]
        for c in candidates:
            if isinstance(c, str) and c.strip():
                return c.strip()
        return ''

    def start_span(self, name: str, attributes: dict | None = None, persistent: bool = False, parent_id: str | None = None) -> dict:
        """Start a span on the active trace context stack."""
        from lib.trace.trace_context import start_span
        return start_span(self.session_id, name, attributes, persistent=persistent, parent_id=parent_id)

    def end_span(self, name: str | None = None) -> dict | None:
        """End a span on the active trace context stack."""
        from lib.trace.trace_context import end_span
        return end_span(self.session_id, name)

    def current_span(self) -> dict | None:
        """Return the current active span."""
        from lib.trace.trace_context import current_span
        return current_span(self.session_id)

    def pop_all_spans(self, preserve_persistent: bool = False) -> list[dict]:
        """End all active spans and return them."""
        from lib.trace.trace_context import pop_all
        return pop_all(self.session_id, preserve_persistent=preserve_persistent)

    def post_span(
        self,
        name: str,
        attributes: dict | None = None,
        parent_id = _UNSET,
        duration_ms: int = 0,
        start_time: str | None = None,
        end_time: str | None = None,
        status_code: str = 'OK',
        span_id: str | None = None,
    ) -> None:
        """POST a session span, using the current active span as parent if parent_id is not provided."""
        resolved_parent = parent_id
        if resolved_parent is _UNSET and self.session_id:
            parent = self.current_span()
            if parent:
                resolved_parent = parent.get('span_id')
        if resolved_parent is _UNSET:
            resolved_parent = None
        hook_plugin_post_span(
            trace_id=self.session_id,
            name=name,
            attributes=attributes,
            parent_id=resolved_parent,
            duration_ms=duration_ms,
            start_time=start_time,
            end_time=end_time,
            status_code=status_code,
            span_id=span_id,
        )

    def post_event(self, endpoint: str, data: dict | list) -> None:
        """POST to a named ingest endpoint."""
        post_event(endpoint, data)

    def emit(self, hook_event_name: str, additional_context: str, suppress_output: bool = True) -> None:
        """Emit the JSON hook response to stdout."""
        emit_response(hook_event_name, additional_context, suppress_output)


def hook_plugin_post_span(
    trace_id: str | None,
    name: str,
    attributes: dict | None = None,
    parent_id: str | None = None,
    duration_ms: int = 0,
    start_time: str | None = None,
    end_time: str | None = None,
    status_code: str = 'OK',
    span_id: str | None = None,
) -> None:
    """Module-level post_span so HookContext.post_span can delegate without recursion confusion."""
    post_span(
        trace_id=trace_id,
        name=name,
        attributes=attributes,
        parent_id=parent_id,
        duration_ms=duration_ms,
        start_time=start_time,
        end_time=end_time,
        status_code=status_code,
        span_id=span_id,
    )
