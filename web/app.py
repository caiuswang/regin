"""Flask JSON API backend for regin Vue SPA.

This file is intentionally small: it is the app factory and nothing else.
Every HTTP route lives in a blueprint under `web/blueprints/`; every
shared helper lives in `web/helpers.py` (ingest validators), `lib/trace/projection.py`
(pure span transforms), or `web/startup.py` (boot-time schema bootstrap).

A few private names (`_normalize_is_test`, `_graft_orphans`,
`_init_session_spans_schema`, …) are re-exported from their new homes so
existing tests can keep reaching them as `app_module._foo`. If you need
to modify one of those helpers, edit it in its own module — this file
should never grow them back.
"""

import logging
import os
import threading
import time
import uuid

from flask import Flask, g, jsonify, request, send_from_directory

from lib.activity_log import configure_activity_log, get_activity_logger
from lib.orm.engine import get_connection
from lib.logging_setup import configure_logging


# Re-exports for test backward-compat (see module docstring).
from web.helpers import (  # noqa: F401
    _IS_TEST_TRUTHY, _IS_TEST_WHERE, _IS_TEST_CASE,
    _normalize_is_test, _is_iso_timestamp, _is_non_blank_str,
    _INGEST_DEDUP_WINDOW_SEC,
    _INGEST_MAX_BATCH_SIZE, _ingest_max_batch_size,
    _INGEST_MAX_ATTRIBUTES_BYTES, _ingest_max_attributes_bytes,
)
from lib.trace.projection import (  # noqa: F401
    _fetch_spans, _graft_orphans, _widen_envelopes,
    _persist_projection, _build_span_tree,
)
from web.startup import (
    init_session_spans_schema as _init_session_spans_schema,
    init_sessions_schema as _init_sessions_schema,
    init_session_repos_schema as _init_session_repos_schema,
    init_session_tags_schema as _init_session_tags_schema,
    init_turn_usage_schema as _init_turn_usage_schema,
    init_prompt_images_schema as _init_prompt_images_schema,
    init_topic_proposal_schema as _init_topic_proposal_schema,
    init_session_grades_schema as _init_session_grades_schema,
    init_bridge_panes_schema as _init_bridge_panes_schema,
    init_bridge_messages_schema as _init_bridge_messages_schema,
    init_pattern_deployments_schema as _init_pattern_deployments_schema,
    init_prompt_templates_schema as _init_prompt_templates_schema,
)


def create_app():
    configure_logging()
    configure_activity_log()
    app = Flask(__name__,
                static_folder=os.path.join(os.path.dirname(__file__), 'static'))

    _install_request_logging(app)

    conn = get_connection()
    try:
        _init_session_spans_schema(conn)
        _init_sessions_schema(conn)
        _init_session_repos_schema(conn)
        _init_session_tags_schema(conn)
        _init_turn_usage_schema(conn)
        _init_prompt_images_schema(conn)
        _init_topic_proposal_schema(conn)
        _init_session_grades_schema(conn)
        _init_bridge_panes_schema(conn)
        _init_bridge_messages_schema(conn)
        _init_pattern_deployments_schema(conn)
        _init_prompt_templates_schema(conn)
    finally:
        conn.close()

    # Seed/heal builtin prompt skeletons on startup, not only on init/rebuild:
    # inserts rows for newly registered surfaces and replaces stored bodies
    # still equal to a retired default (user edits survive), so a code upgrade
    # that revises a builtin prompt reaches existing installs. Best-effort — a
    # prompt-table hiccup must never block serve.
    try:
        from lib.prompt_templates import seed_builtin_skeletons
        seed_builtin_skeletons()
    except Exception:
        logging.getLogger(__name__).exception('prompt skeleton seed failed')

    # ── Authentication, users, audit (extracted to blueprint) ─────
    from web.blueprints.auth import auth_bp
    app.register_blueprint(auth_bp)

    # (Jinja template routes removed — Vue SPA serves the UI)

    # ── Hook management (extracted to blueprint) ─────────────────
    from web.blueprints.hooks import hooks_bp
    app.register_blueprint(hooks_bp)

    # ── Rules + rule-trigger ingest (extracted to blueprint) ─────
    from web.blueprints.rules import rules_bp
    app.register_blueprint(rules_bp)

    # ── Rule engines (/api/rule-engines) ─────────────────────────
    from web.blueprints.rule_engines import rule_engines_bp
    app.register_blueprint(rule_engines_bp)

    # ── Repos (list, add, remove, detail) ────────────────────────
    from web.blueprints.repos import repos_bp
    app.register_blueprint(repos_bp)

    # ── Meta / dashboard (extracted to blueprint) ────────────────
    from web.blueprints.meta import meta_bp
    app.register_blueprint(meta_bp)

    # ── Plans + plan-sessions (extracted to blueprint) ───────────
    from web.blueprints.plans import plans_bp
    app.register_blueprint(plans_bp)

    # ── Patterns (extracted to blueprint) ─────────────────────────
    from web.blueprints.patterns import patterns_bp
    app.register_blueprint(patterns_bp)

    # ── Skills (extracted to blueprint) ───────────────────────────
    from web.blueprints.skills import skills_bp
    app.register_blueprint(skills_bp)

    # ── Tags (extracted to blueprint) ─────────────────────────────
    from web.blueprints.tags import tags_bp
    app.register_blueprint(tags_bp)

    # ── Experiments (extracted to blueprint) ──────────────────────
    from web.blueprints.experiments import experiments_bp
    app.register_blueprint(experiments_bp)

    # ── Trace (extracted to blueprint) ────────────────────────────
    from web.blueprints.trace import trace_bp
    app.register_blueprint(trace_bp)

    # ── Topics (repo-local curated graph + candidate inbox) ──────
    from web.blueprints.topics import topics_bp
    app.register_blueprint(topics_bp)

    # ── Prompt templates (injectable LLM/agent prompt fragments) ─
    from web.blueprints.prompt_templates import prompt_templates_bp
    app.register_blueprint(prompt_templates_bp)

    # ── Settings (extracted to blueprint) ─────────────────────────
    from web.blueprints.settings import settings_bp
    app.register_blueprint(settings_bp)

    # ── Payload schema drift (PostToolUse payload validation) ─────
    from web.blueprints.schema_drift import schema_drift_bp
    app.register_blueprint(schema_drift_bp)

    # ── Diagnostics master switch + payload-log browser ───────────
    from web.blueprints.diagnostics import diagnostics_bp
    app.register_blueprint(diagnostics_bp)

    # ── Agent memory (cross-session experience store) ─────────────
    from web.blueprints.memory import memory_bp
    app.register_blueprint(memory_bp)

    # ── Session grades (post-hoc rubric grader, lib/grader) ───────
    from web.blueprints.grades import grades_bp
    app.register_blueprint(grades_bp)
    from web.blueprints.grader_config import grader_config_bp
    app.register_blueprint(grader_config_bp)

    # ── Notification event-bus catalog (lib/agent_messages/events) ──
    from web.blueprints.events import events_bp
    app.register_blueprint(events_bp)

    # ── Agent bridge HTTP surface (human/system → live session) ───
    from web.blueprints.bridge import bridge_bp
    app.register_blueprint(bridge_bp)

    # Auth gate installed last so it can validate its allowlist against the
    # fully-registered route table.
    _install_auth_gate(app)

    _install_spa_routes(app)
    _start_memory_warmup()
    return app


def _start_memory_warmup() -> None:
    """Eagerly load the dense recall models in a background thread.

    The auto-inject hook borrows this process's warm models over loopback
    with a sub-second timeout; on a freshly started server the first
    request would otherwise pay the model load, time out, and silently
    degrade every early prompt to FTS-only recall. The warm-up recall also
    triggers the lazy embedding backfill, so coverage heals at boot rather
    than on the first real query. Best-effort: failures only mean the old
    lazy behavior."""
    from lib.settings import settings

    cfg = settings.agent_memory
    if not (cfg.enabled and cfg.dense_enabled):
        return

    def _warm() -> None:
        try:
            import lib.memory as memory
            memory.recall("warmup", top_k=1, mode="auto", reinforce=False)
        except Exception:
            logging.getLogger(__name__).debug(
                "memory warmup failed", exc_info=True)

    threading.Thread(target=_warm, name="memory-warmup",
                     daemon=True).start()


# Endpoints reachable without a valid JWT. Two groups:
#   1. Auth bootstrap — login/register/me must work before a token exists
#      (api_auth_me deliberately returns {user: None} rather than 401).
#   2. Machine trace ingest — Claude Code hooks (lib/hook_plugin.py) and the
#      statusline (scripts/regin-statusline) POST trace data with no
#      Authorization header. Gating these would silently break ingestion,
#      since hooks fire-and-forget and swallow errors.
# Everything else under /api/ requires auth. Opening a new endpoint is a
# deliberate act: add its Flask endpoint name (`<blueprint>.<func>`) here.
PUBLIC_API_ENDPOINTS = frozenset({
    "auth.api_auth_login",
    "auth.api_auth_register",
    "auth.api_auth_me",
    "trace.api_ingest_session_span",
    "trace.api_ingest_skill_read",
    "trace.api_ingest_turn_usage",
    "trace.api_ingest_tool_attribution",
    "trace.api_ingest_kimi_subagents",
    "trace.api_ingest_session_status",
    "trace.api_ingest_prompt_images",
    "plans.api_ingest_plan_session",
    "rules.api_ingest_rule_trigger",
    # Agent bridge: guarded by its own bearer token (require_bridge_token),
    # a credential SEPARATE from the web-UI JWT. The gate's PUBLIC branch
    # returns with no auth, so that decorator is the sole guard — intended.
    "bridge.api_bridge_post_message",
    "bridge.api_bridge_list_sessions",
    "bridge.api_bridge_list_messages",
})


_LOOPBACK_ADDRS = frozenset({"127.0.0.1", "::1"})


def _inject_recall_loopback_ok() -> bool:
    """The agent-memory auto-inject hook borrows this process's warm dense
    models via `POST /api/memory/recall`. A fresh hook process per prompt
    can't carry a JWT, so grant *that one endpoint* a loopback-only bypass
    when the feature is on. Deliberately NOT a PUBLIC_API_ENDPOINTS entry:
    that allowlist is network-public, whereas this stays bound to 127.0.0.1
    so distilled memory content never leaves the host."""
    from lib.settings import settings
    if request.endpoint != "memory.api_memory_recall":
        return False
    if not settings.agent_memory.inject_dense_via_server:
        return False
    return (request.remote_addr or "") in _LOOPBACK_ADDRS


def _install_auth_gate(app: Flask) -> None:
    """Require a valid JWT for every /api/ route outside PUBLIC_API_ENDPOINTS.

    Centralizes the auth check so read endpoints can't ship unprotected by
    omission — the policy is deny-by-default. Per-route role decorators
    (`require_editor`/`require_role`) still apply on top of this.
    """
    from lib.auth import get_current_user

    missing = PUBLIC_API_ENDPOINTS - set(app.view_functions)
    if missing:
        raise RuntimeError(
            "PUBLIC_API_ENDPOINTS references unknown endpoints: "
            f"{sorted(missing)}. A renamed or removed view would silently "
            "gate machine ingest; fix the allowlist."
        )

    @app.before_request
    def _enforce_auth():
        if request.method == "OPTIONS":
            return None
        if not request.path.startswith("/api/"):
            return None  # SPA shell + static assets are public
        if request.blueprint is None:
            return None  # app-level routes (SPA catch-all, unmatched /api/*)
        if request.endpoint in PUBLIC_API_ENDPOINTS:
            return None
        if _inject_recall_loopback_ok():
            return None  # local auto-inject hook → warm dense models
        if get_current_user() is None:
            return jsonify({"error": "Authentication required"}), 401
        return None


def _install_request_logging(app: Flask) -> None:
    """Per-request entry in the activity log tagged `feature=web`
    (method, path, status, duration).

    Static asset paths are skipped to keep the file useful. Exceptions
    that escape blueprint handlers land in the same stream as ERROR rows."""
    log = get_activity_logger("web")

    @app.before_request
    def _stamp_request():
        if request.path.startswith("/static/"):
            return
        g.activity_request_id = uuid.uuid4().hex[:12]
        g.activity_request_start = time.monotonic()

    @app.after_request
    def _emit_request(response):
        if request.path.startswith("/static/"):
            return response
        start = getattr(g, "activity_request_start", None)
        duration_ms = round((time.monotonic() - start) * 1000, 2) if start else None
        log.write(
            "http_request",
            method=request.method,
            path=request.path,
            status=response.status_code,
            duration_ms=duration_ms,
            request_id=getattr(g, "activity_request_id", None),
            user=getattr(g, "user_id", None),
        )
        return response

    @app.errorhandler(Exception)
    def _emit_unhandled(exc):
        log.error(
            "http_unhandled_exception",
            exc_info=True,
            method=request.method,
            path=request.path,
            request_id=getattr(g, "activity_request_id", None),
        )
        return jsonify({"error": "internal server error"}), 500


def _install_spa_routes(app: Flask) -> None:
    # SPA catch-all: serve Vue frontend for non-API paths
    spa_dir = os.path.join(os.path.dirname(__file__), 'static', 'dist')

    @app.route('/', defaults={'path': ''})
    @app.route('/<path:path>')
    def spa_catchall(path):
        if path.startswith('api/'):
            return jsonify({'error': 'not found'}), 404
        if path and os.path.isfile(os.path.join(spa_dir, path)):
            return send_from_directory(spa_dir, path)
        return send_from_directory(spa_dir, 'index.html')
