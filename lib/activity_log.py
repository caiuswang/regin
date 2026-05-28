"""Single-stream activity log for regin's own internal operations.

Distinct from `lib/trace/`, which captures *Claude Code session traces*
(prompts and tool calls regin observes). This module captures *regin's*
activity: CLI commands, HTTP requests, hook handler dispatches, DB
mutations.

Convention (mechanically enforced — see `ActivityLogger`):

    log = get_activity_logger("patterns")
    log.read("pattern_loaded", pattern_id="…")    # → DEBUG
    log.write("pattern_imported", pattern_id="…") # → INFO
    log.error("import_failed", exc_info=True)     # → ERROR

`.info()` / `.debug()` are intentionally absent on the wrapper so the
read/write distinction can't be quietly violated. `log.info(...)` raises
AttributeError.

All features write to one rotating JSONL file at
`settings.log_dir/regin.log`, tagged with `extra.feature`. Cross-feature
correlation works directly — sort by timestamp or filter by `request_id`.

CLI: `regin logs {list,tail,grep,prune,path}` — `--feature` is a filter,
not a positional arg.
"""

from __future__ import annotations

import os
import threading
import time
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger as _loguru

from lib.settings import settings


_LOG_FILENAME = "regin.log"


# Keys (case-insensitive) whose values get scrubbed before serialization.
_REDACT_KEYS: frozenset[str] = frozenset({
    "password", "passwd", "token", "secret", "api_key", "apikey",
    "authorization", "auth", "cookie", "set_cookie", "session",
    "access_token", "refresh_token",
})
_REDACTED_VALUE: str = "<redacted>"

_HANDLER_ID: int | None = None
_CONFIG_LOCK = threading.Lock()
_CONFIGURED: bool = False
_LOG_DIR: Path | None = None
_WARNED_FEATURES: set[str] = set()


@dataclass(frozen=True)
class FeatureFileInfo:
    """One row in `regin logs list` — per-feature counts derived from
    a single pass over the single-stream log file."""
    feature: str
    event_count: int
    error_count: int
    last_seen: float | None


def _redact_patcher(record: dict[str, Any]) -> None:
    extra = record.get("extra")
    if not extra:
        return
    for key in list(extra.keys()):
        if key.lower() in _REDACT_KEYS:
            extra[key] = _REDACTED_VALUE


def _resolve_log_dir(override: Path | None) -> Path:
    if override is not None:
        return Path(os.path.expanduser(str(override)))
    env = os.environ.get("REGIN_ACTIVITY_LOG_DIR")
    if env:
        return Path(os.path.expanduser(env))
    return settings.log_dir


def _log_level() -> str:
    name = os.environ.get("REGIN_LOG_LEVEL", "INFO").strip().upper()
    return name or "INFO"


def configure_activity_log(
    *,
    log_dir: Path | None = None,
    enqueue: bool = True,
    force: bool = False,
) -> None:
    """Idempotent. Registers one loguru sink (`<log_dir>/regin.log`),
    removes loguru's default stderr handler (we own all sinks here so
    records don't double-write).

    Args:
        log_dir: override the resolved log directory. Useful for tests.
        enqueue: route writes through a multiprocessing queue for
            multi-process safety. Set False in tests to make writes
            synchronous and assertable without `logger.complete()`.
        force: rebuild the sink even if already configured. Required
            when settings or `log_dir` change at runtime.
    """
    global _CONFIGURED, _LOG_DIR, _HANDLER_ID

    if os.environ.get("REGIN_ACTIVITY_LOG_DISABLED", "").strip() == "1":
        # Drop loguru's default stderr handler so writes silently no-op
        # instead of bleeding into stderr and corrupting `--help` output.
        try:
            _loguru.remove()
        except ValueError:
            pass
        return

    with _CONFIG_LOCK:
        if _CONFIGURED and not force:
            return

        # Drop our existing sink first, then loguru's default stderr
        # (only on the first call — `force` rebuilds our sink but should
        # not re-remove the default that was already removed).
        if _HANDLER_ID is not None:
            try:
                _loguru.remove(_HANDLER_ID)
            except ValueError:
                pass
            _HANDLER_ID = None

        if not _CONFIGURED:
            try:
                _loguru.remove()
            except ValueError:
                pass

        _loguru.configure(patcher=_redact_patcher)

        log_dir_resolved = _resolve_log_dir(log_dir)
        log_dir_resolved.mkdir(parents=True, exist_ok=True)
        _LOG_DIR = log_dir_resolved

        level = _log_level()
        rotation_bytes = int(settings.log_max_bytes_per_file)
        retention = f"{int(settings.log_retention_days)} days"

        sink_path = log_dir_resolved / _LOG_FILENAME
        _HANDLER_ID = _loguru.add(
            str(sink_path),
            level=level,
            rotation=rotation_bytes,
            retention=retention,
            enqueue=enqueue,
            serialize=True,
            backtrace=False,
            diagnose=False,
        )

        _CONFIGURED = True


def _ensure_feature(feature: str) -> str:
    if feature in settings.activity_log_features:
        return feature
    if feature not in _WARNED_FEATURES:
        _WARNED_FEATURES.add(feature)
        warnings.warn(
            f"activity_log: unknown feature {feature!r}; tagging as 'other'. "
            f"Add it to settings.activity_log_features to silence this warning.",
            stacklevel=3,
        )
    return "other"


class ActivityLogger:
    """Thin wrapper enforcing read=DEBUG / write=INFO.

    `.info()` / `.debug()` are intentionally absent. Attempting them
    raises AttributeError so the convention is enforced at call time."""

    __slots__ = ("_lg", "_feature")

    def __init__(self, bound_logger: Any, feature: str) -> None:
        self._lg = bound_logger
        self._feature = feature

    def read(self, event: str, **fields: Any) -> None:
        self._lg.bind(**fields).debug(event)

    def write(self, event: str, **fields: Any) -> None:
        self._lg.bind(**fields).info(event)

    def warn(self, event: str, **fields: Any) -> None:
        self._lg.bind(**fields).warning(event)

    def error(self, event: str, exc_info: bool = False, **fields: Any) -> None:
        target = self._lg.opt(exception=exc_info) if exc_info else self._lg
        target.bind(**fields).error(event)

    def bind(self, **fields: Any) -> "ActivityLogger":
        return ActivityLogger(self._lg.bind(**fields), self._feature)

    @property
    def feature(self) -> str:
        return self._feature


def get_activity_logger(feature: str) -> ActivityLogger:
    """Return an ActivityLogger tagged with `feature`. Lazy-configures
    the underlying sink on first call. Unknown features get tagged
    'other' with a one-time stderr warning."""
    if not _CONFIGURED and os.environ.get("REGIN_ACTIVITY_LOG_DISABLED", "").strip() != "1":
        configure_activity_log()
    routed = _ensure_feature(feature)
    return ActivityLogger(_loguru.bind(feature=routed), routed)


def log_path() -> Path | None:
    """Absolute path to the single activity log file, if configured."""
    if _LOG_DIR is None:
        if os.environ.get("REGIN_ACTIVITY_LOG_DISABLED", "").strip() == "1":
            return None
        configure_activity_log()
    if _LOG_DIR is None:
        return None
    return _LOG_DIR / _LOG_FILENAME


def iter_features() -> list[FeatureFileInfo]:
    """Single pass over `regin.log` bucketing records by `extra.feature`.

    Returns one row per observed feature with the event count, error
    count, and the most-recent timestamp seen."""
    import json

    if _LOG_DIR is None:
        return []
    path = _LOG_DIR / _LOG_FILENAME
    if not path.exists() or path.stat().st_size == 0:
        return []
    buckets: dict[str, list[int | float | None]] = {}
    with path.open("r") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw).get("record", {})
            except (json.JSONDecodeError, AttributeError):
                continue
            feat = (rec.get("extra") or {}).get("feature") or "other"
            level = (rec.get("level") or {}).get("name", "")
            ts = (rec.get("time") or {}).get("timestamp")
            slot = buckets.setdefault(feat, [0, 0, None])
            slot[0] = int(slot[0]) + 1
            if level == "ERROR":
                slot[1] = int(slot[1]) + 1
            if ts is not None:
                prev = slot[2]
                if prev is None or ts > prev:
                    slot[2] = ts
    return [
        FeatureFileInfo(
            feature=feat,
            event_count=int(slot[0]),
            error_count=int(slot[1]),
            last_seen=slot[2],
        )
        for feat, slot in sorted(buckets.items())
    ]


def prune(
    older_than_days: int = 14,
    dry_run: bool = False,
) -> list[Path]:
    """Delete rotated archives of `regin.log` older than the cutoff.
    The active `regin.log` is never touched.

    loguru's own `retention=` handles the steady-state case. This
    command exists for forced cleanup or non-default cutoffs."""
    if _LOG_DIR is None or not _LOG_DIR.exists():
        return []
    cutoff = time.time() - older_than_days * 86400
    active = _LOG_DIR / _LOG_FILENAME
    deleted: list[Path] = []
    for entry in _LOG_DIR.glob("regin.*"):
        if entry == active or not entry.is_file():
            continue
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            continue
        if mtime > cutoff:
            continue
        deleted.append(entry)
        if not dry_run:
            try:
                entry.unlink()
            except OSError:
                pass
    return deleted


def feature_path(feature: str) -> Path:
    """Path to the single activity log. The `feature` argument is kept
    for backward compatibility — every feature now shares one file."""
    _ensure_feature(feature)  # keep the typo-guard warning
    if _LOG_DIR is None:
        if not _CONFIGURED and os.environ.get("REGIN_ACTIVITY_LOG_DISABLED", "").strip() != "1":
            configure_activity_log()
    assert _LOG_DIR is not None
    return _LOG_DIR / _LOG_FILENAME


__all__ = [
    "ActivityLogger",
    "FeatureFileInfo",
    "configure_activity_log",
    "feature_path",
    "get_activity_logger",
    "iter_features",
    "log_path",
    "prune",
]
