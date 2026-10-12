"""Flask JSON API backend for regin Vue SPA.

This file is intentionally small: it is the app factory and nothing else.
Every HTTP route lives in a blueprint under `web/blueprints/`; every
shared helper lives in `web/helpers.py` (ingest validators) or
`lib/trace/projection.py` (pure span transforms).

Schema DDL is deliberately NOT run here: `create_app()` never creates or
alters tables. Fresh installs get their schema from `regin init`
(db/schema.sql, stamped at the alembic head); an existing DB is brought up
to the current schema by the explicit `regin migrate` command
(`alembic upgrade head`). Booting the server must not migrate the schema.
(It does still best-effort seed builtin prompt rows — content, not schema
— so a code upgrade's new prompt surfaces reach the install.)

A few private names (`_normalize_is_test`, `_graft_orphans`, …) are
re-exported from their new homes so existing tests can keep reaching them
as `app_module._foo`. If you need to modify one of those helpers, edit it
in its own module — this file should never grow them back.
"""

import logging
import os
import threading
import time
import uuid

from flask import Flask, g, jsonify, request, send_from_directory
from flask_compress import Compress

from lib.activity_log import configure_activity_log, get_activity_logger
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


def create_app(replica: bool = False):
    configure_logging()
    configure_activity_log()
    app = Flask(__name__,
                static_folder=os.path.join(os.path.dirname(__file__), 'static'))

    # A trace response is unbounded: one prompt's `?deep=1` subtree measured
    # 5.4MB across 1,943 descendants, of which 97% is span bodies. Werkzeug
    # sends that byte for byte, which is minutes on a weak link. gzip takes
    # the same payload to 1.36MB. `text/event-stream` is deliberately absent
    # from Flask-Compress's mimetype set, so the SSE hub keeps streaming
    # uncompressed and unbuffered.
    Compress(app)

    _install_request_logging(app)
    if replica:
        _install_replica_guard(app)

    # Seed/heal builtin prompt skeletons on startup, not only on init/rebuild:
    # inserts rows for newly registered surfaces and replaces stored bodies
    # still equal to a retired default (user edits survive), so a code upgrade
    # that revises a builtin prompt reaches existing installs. This is content
    # seeding, not schema DDL — the schema self-heal lives in `regin migrate`.
    # Best-effort — a prompt-table hiccup must never block serve.
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

    from web.blueprints.repo_bundles import repo_bundles_bp
    app.register_blueprint(repo_bundles_bp)

    # ── Meta / dashboard (extracted to blueprint) ────────────────
    from web.blueprints.meta import meta_bp
    app.register_blueprint(meta_bp)

    # ── Full-text search + the palette's address resolver ───────
    from web.blueprints.search import search_bp
    app.register_blueprint(search_bp)
    from web.blueprints.palette import palette_bp
    app.register_blueprint(palette_bp)

    # ── Search palette's empty-query desk + entity preview (read-only) ─
    from web.blueprints.palette_home import palette_home_bp
    app.register_blueprint(palette_home_bp)

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

    # ── Agent bridge WebSocket surface (a real terminal on the pane) ─
    from web.blueprints.terminal import terminal_bp, sock as terminal_sock
    app.register_blueprint(terminal_bp)
    terminal_sock.init_app(app)

    # ── Agent SDK tier (sessions regin launches and owns) ─────────
    from web.blueprints.agent_runs import agent_runs_bp
    app.register_blueprint(agent_runs_bp)
    if not replica:
        _reap_orphaned_agent_runs()

    from web.blueprints.notifications import notifications_bp
    app.register_blueprint(notifications_bp)

    # ── Task capture (API-key POST → idea rows) + the keys that reach it ──
    from web.blueprints.tasks import tasks_bp
    app.register_blueprint(tasks_bp)

    from web.blueprints.features import features_bp
    app.register_blueprint(features_bp)

    from web.blueprints.project_stages import project_stages_bp
    app.register_blueprint(project_stages_bp)

    from web.blueprints.defects import defects_bp
    app.register_blueprint(defects_bp)

    from web.blueprints.pipeline import pipeline_bp
    app.register_blueprint(pipeline_bp)

    # ── @-references in spec and task bodies (search + resolve) ──
    from web.blueprints.refs import refs_bp
    app.register_blueprint(refs_bp)

    from web.blueprints.api_keys import api_keys_bp
    app.register_blueprint(api_keys_bp)

    # ── Read-only file viewer (meta + raw bytes, R1-confined) ─────
    from web.blueprints.files import files_bp
    app.register_blueprint(files_bp)

    # Auth gate installed last so it can validate its allowlist against the
    # fully-registered route table.
    _install_auth_gate(app)

    _install_spa_routes(app)
    _start_memory_warmup()
    return app


def _reap_orphaned_agent_runs() -> None:
    """Close out the sessions the previous process took with it — the SDK runs
    it launched, and the tmux-registered ones it merely observed.

    Unconditional: a run orphaned before the tier was switched off is exactly
    the row that would otherwise sit in the UI claiming to be steerable
    forever. Never fatal to startup — a server that can't reach the table
    should still serve.
    """
    from lib.agent_sdk import store as agent_sdk_store

    try:
        agent_sdk_store.reap_orphaned_runs()
    except Exception:
        get_activity_logger("agent_sdk").error("sdk_reap_failed", exc_info=True)

    # Re-runnable rather than a one-shot migration backfill: rows that need
    # linking keep arriving — every run recorded before the column existed, and
    # any run whose child died before it named itself. A migration would heal
    # the rows present the instant it ran and leak every one after.
    try:
        agent_sdk_store.heal_cli_session_ids()
    except Exception:
        get_activity_logger("agent_sdk").error("sdk_heal_failed", exc_info=True)

    # The sweeps above answer only for runs regin launched. A session the
    # operator started in a tmux pane has no `agent_runs` row at all, so its
    # orphans are settled from the pane registry instead.
    from lib.agent_bridge import reaper as bridge_reaper

    try:
        bridge_reaper.settle_dead_tmux_sessions()
    except Exception:
        get_activity_logger("agent_bridge").error("bridge_reap_failed",
                                                  exc_info=True)

    # Task runs launched in tmux outlive the server by design, so they are
    # settled only when tmux says their session is gone — and a settle a CLI
    # made while no server was up may have left `sdk` dependents to fire here.
    from lib.tasks import runs as task_runs

    try:
        task_runs.reap_tmux_runs_at_boot()
    except Exception:
        get_activity_logger("tasks").error("task_tmux_reap_failed",
                                           exc_info=True)


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
    "bridge.api_bridge_listen",
    "bridge.api_bridge_hold",
    "bridge.api_bridge_release",
    # Task capture: guarded by its own API key (require_scope), a principal
    # SEPARATE from the web-UI JWT — resolving one never produces a user, so a
    # key fails every user-gated route. The gate's PUBLIC branch returns with
    # no auth, so that decorator is the sole guard — intended.
    "tasks.api_capture_task",
    # Badge stream: EventSource cannot send an Authorization header, so the
    # route is guarded by the single-use ticket it redeems instead — like the
    # bridge entries above, its own credential is the sole guard, intended.
    "notifications.api_notifications_stream",
    # Pane terminal: a WebSocket cannot send one either. Same ticket store,
    # and the route additionally demands the admin role on redemption — this
    # entry buys it past the header check, not past authorization.
    "bridge_terminal.api_session_terminal",
})


# Endpoints that require the admin role on top of authentication. The session
# list and its trace drill-in expose every user's activity, and sessions carry
# no per-user owner to filter on, so the whole surface is admin-only — editors
# and viewers are denied. Enforced centrally (deny-by-default) rather than via
# per-route decorators so the policy lives in one auditable place.
ADMIN_API_ENDPOINTS = frozenset({
    "trace.api_sessions",
    "trace.api_session_detail",
    "trace.api_session_span_children",
    "trace.api_session_subtrees",
    "trace.api_session_map",
    "trace.api_span_content",
    "trace.api_span_rewind",
    "trace.api_session_workflow_runs",
    "trace.api_span_ancestors",
    "trace.api_session_batch_delete",
    "trace.api_session_batch_close",
    "trace.api_session_delete",
    "trace.api_session_tags",
    "trace.api_session_tag_add",
    "trace.api_session_tag_remove",
    "trace.api_session_close",
    "trace.api_session_materialize",
    "trace.api_session_repair_spans",
    # Session-scoped detail routes that live in sibling trace modules
    # (turn_usage / agent_messages / prompt_images). agent-messages in
    # particular returns the session goal text + every send_to_user message,
    # and prompt-images serves the raw prompt image bytes — all as much a
    # per-session content leak as the map/spans above.
    "trace.api_session_features",
    "trace.api_session_tool_rollup",
    "trace.api_session_turn_usage",
    "trace.api_session_agent_messages",
    "trace.api_get_prompt_image",
    # A session's grades live in their own blueprint but are the same kind of
    # per-session content: a judge's report and detail quote the transcript
    # they graded. The cross-session `/api/grades` list stays auth-only —
    # the Grades view is not an admin route.
    "grades.api_session_grades",
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


def _notify_trigger_loopback_ok() -> bool:
    """The badge-push trigger is fired by producer processes (hooks, the MCP
    server) that write inbox/drift rows and hold no JWT. Like the recall
    bypass above it is loopback-only, and it carries no payload and returns
    no data — it only tells the web process to recompute two counters."""
    if request.endpoint != "notifications.api_internal_notify":
        return False
    return (request.remote_addr or "") in _LOOPBACK_ADDRS


def _task_finish_loopback_ok() -> bool:
    """A task's own agent reports its work finished through
    `regin task finish`, which runs in the agent's process and holds no JWT.
    Loopback-only like the two bypasses above: the caller already shares this
    host's DB, the route settles only a run id that exists, and it can start
    nothing beyond the tasks the operator already armed behind a dependency
    edge."""
    if request.endpoint != "tasks.api_finish_task_run":
        return False
    return (request.remote_addr or "") in _LOOPBACK_ADDRS


_REPLICA_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# The realtime handshake is a POST only because EventSource cannot carry a
# bearer header: the ticket and the topic set live in process memory and touch
# no store, and the SPA opens the stream on every page, so refusing them turns
# every recorded frame into a reconnect loop.
_REPLICA_WRITE_ALLOWED_ENDPOINTS = frozenset({
    "auth.api_auth_login",
    "notifications.api_auth_stream_ticket",
    "notifications.api_notifications_stream_topics",
})
# A WebSocket upgrade arrives as a GET, but the pane terminal writes keystrokes
# into live tmux panes.
_REPLICA_READ_REFUSED_ENDPOINTS = frozenset({
    "bridge_terminal.api_session_terminal",
})


def _install_replica_guard(app: Flask) -> None:
    """Refuse every /api/ write on a replica serving a copy of the live DB.

    Installed ahead of the auth gate so an anonymous write is refused as a
    replica write rather than as a missing login. Login stays open because a
    viewer has to mint a token to read at all.
    """
    @app.before_request
    def _refuse_replica_writes():
        if request.endpoint in _REPLICA_READ_REFUSED_ENDPOINTS:
            return jsonify({"error": "replica"}), 403
        if request.method in _REPLICA_READ_METHODS:
            return None
        if not request.path.startswith("/api/"):
            return None
        if request.endpoint in _REPLICA_WRITE_ALLOWED_ENDPOINTS:
            return None
        return jsonify({"error": "replica"}), 403


def _install_auth_gate(app: Flask) -> None:
    """Require a valid JWT for every /api/ route outside PUBLIC_API_ENDPOINTS.

    Centralizes the auth check so read endpoints can't ship unprotected by
    omission — the policy is deny-by-default. Per-route role decorators
    (`require_editor`/`require_role`) still apply on top of this.
    """
    from lib.auth import get_current_user

    missing = (PUBLIC_API_ENDPOINTS | ADMIN_API_ENDPOINTS) - set(app.view_functions)
    if missing:
        raise RuntimeError(
            "PUBLIC_API_ENDPOINTS / ADMIN_API_ENDPOINTS reference unknown "
            f"endpoints: {sorted(missing)}. A renamed or removed view would "
            "silently gate machine ingest or drop an admin guard; fix the "
            "allowlist."
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
        if _notify_trigger_loopback_ok():
            return None  # local producer process → badge push fan-out
        if _task_finish_loopback_ok():
            return None  # a task's own agent → settle its run, fire dependents
        user = get_current_user()
        if user is None:
            return jsonify({"error": "Authentication required"}), 401
        if request.endpoint in ADMIN_API_ENDPOINTS and user.get("role") != "admin":
            return jsonify({"error": "Admin role required"}), 403
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

    from lib.skills.skill_deployer import UnrecordedSkillDirError

    @app.errorhandler(UnrecordedSkillDirError)
    def _refuse_unrecorded_skill_dir(exc):
        return jsonify({"ok": False, "msg": str(exc), "path": exc.path}), 409

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


_FRONTEND_NOT_BUILT = (
    '<!doctype html><title>regin: frontend not built</title>'
    '<h1>regin\'s web UI has not been built</h1>'
    '<p>The API is up, but <code>web/static/dist/</code> is empty. From the '
    'checkout, run <code>./scripts/setup.sh</code> (or '
    '<code>cd frontend &amp;&amp; npm ci &amp;&amp; npx vite build</code>), '
    'then reload this page.</p>'
)


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
        if not os.path.isfile(os.path.join(spa_dir, 'index.html')):
            return _FRONTEND_NOT_BUILT, 404, {'Content-Type': 'text/html; charset=utf-8'}
        return send_from_directory(spa_dir, 'index.html')
