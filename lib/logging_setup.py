"""Structured logging bootstrap for regin.

Replaces the scattered `print()` calls across `lib/` and web blueprints
with `structlog`. One call to `configure_logging()` at process start
wires up both `logging.getLogger()` and `structlog.get_logger()` so
third-party libs (Flask, SQLAlchemy, urllib) emit into the same
pipeline.

Two rendering modes:

* **Development** (default, or `REGIN_LOG_FORMAT=console`): Rich-style
  coloured console output, one line per event, human-readable. Picked
  automatically when stderr is a TTY.
* **Production** (`REGIN_LOG_FORMAT=json`): one JSON object per line,
  ready for ingestion by log shippers. Picked when stderr is not a TTY
  or the env var is set explicitly.

Call sites:
    from lib.logging_setup import configure_logging, get_logger
    configure_logging()                       # idempotent; call at boot
    log = get_logger(__name__)
    log.info("synced repo", repo="example-service", commits=4)
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Literal

import structlog


_CONFIGURED: bool = False


def _pick_renderer() -> Literal["console", "json"]:
    explicit = os.environ.get("REGIN_LOG_FORMAT", "").strip().lower()
    if explicit in ("console", "json"):
        return explicit  # type: ignore[return-value]
    return "console" if sys.stderr.isatty() else "json"


def _pick_level() -> int:
    name = os.environ.get("REGIN_LOG_LEVEL", "INFO").strip().upper()
    return getattr(logging, name, logging.INFO)


def configure_logging(
    *, level: int | None = None, force: bool = False
) -> None:
    """Install stdlib + structlog handlers. Idempotent by default.

    Call from `create_app()` and from the CLI entrypoint once — further
    calls are no-ops unless `force=True`.
    """
    global _CONFIGURED
    if _CONFIGURED and not force:
        return

    renderer_kind = _pick_renderer()
    effective_level = level if level is not None else _pick_level()

    shared_processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if renderer_kind == "json":
        renderer: structlog.typing.Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    # Configure structlog first so its logger factory honours the
    # renderer chain.
    structlog.configure(
        processors=shared_processors + [renderer],
        wrapper_class=structlog.make_filtering_bound_logger(effective_level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )

    # Also route stdlib logging through the same pipeline so Flask,
    # urllib, etc. produce consistent output.
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processor=renderer,
            foreign_pre_chain=shared_processors,
        )
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(effective_level)

    # Werkzeug's dev server logs every request to stderr at INFO. We already
    # record per-request entries via the activity logger (feature=web), so
    # silence its access log to keep `regin serve` terminal output clean.
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    _CONFIGURED = True


def get_logger(name: str | None = None) -> structlog.typing.FilteringBoundLogger:
    """Return a bound structlog logger.

    Callers may pass `__name__` so records carry the module name,
    matching the stdlib `logging.getLogger(__name__)` idiom.
    """
    if not _CONFIGURED:
        configure_logging()
    return structlog.get_logger(name) if name else structlog.get_logger()


__all__ = ["configure_logging", "get_logger"]
