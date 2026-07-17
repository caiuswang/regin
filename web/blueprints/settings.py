"""Settings GET/POST endpoints.

Repo registration moved to ``web.blueprints.repos`` (the /repos page
manages it explicitly). The legacy ``/api/settings/rescan`` endpoint
was removed alongside the auto-discovery model.
"""

from dataclasses import asdict

from flask import Blueprint, request, jsonify

from lib.auth import require_editor
from lib.providers import (
    list_visible_provider_ids, build_provider, is_provider_id, active_provider_id,
)
from lib.settings import (
    get_current_values, save_settings, _load_settings, SETTINGS_SCHEMA,
    ProviderConfig, ProviderPathOverrides,
)


settings_bp = Blueprint('settings', __name__)


# Nested pydantic settings blocks (agent_memory, agent_messages) can't ride
# the flat SETTINGS_SCHEMA mechanism — that writes flat keys pydantic won't
# read back. Each is round-tripped whole (like rule_trigger_thresholds): the
# field registries below drive a generic GET/PUT per block, and the UI renders
# one card per `group`. Field `type`: bool | int | float | string | choice
# (needs `options`); `min`/`max`/`step` apply to numbers.
_AGENT_MEMORY_FIELDS: list[dict] = [
    # ── General ──
    {"key": "enabled", "group": "General", "type": "bool",
     "label": "Memory engine enabled",
     "description": "Master switch for the cross-session memory engine."},
    {"key": "auto_inject", "group": "General", "type": "bool",
     "label": "Auto-inject recalled experience",
     "description": "Inject matching past experience into each prompt as a "
                    "<recalled_experience> block."},
    {"key": "dense_enabled", "group": "General", "type": "bool",
     "label": "Dense recall on long-lived surfaces",
     "description": "Use embedding + cross-encoder recall on the MCP/web/CLI "
                    "surfaces (degrades to FTS when the models are missing)."},
    {"key": "scope_policy", "group": "General", "type": "choice",
     "options": ["global", "per-repo", "per-repo-tagged"],
     "label": "Scope policy",
     "description": "How captures are scoped and recall narrowed. "
                    "per-repo-tagged: stamp the repo on writes, recall globally."},
    # ── Distillation ──
    {"key": "distill_min_importance", "group": "Distillation", "type": "float",
     "min": 0, "max": 1, "step": 0.05, "label": "Drop below importance",
     "description": "Distilled drafts the LLM self-scores below this are "
                    "dropped, never stored. Higher = more selective."},
    {"key": "auto_approve_importance", "group": "Distillation", "type": "float",
     "min": 0, "max": 1, "step": 0.05, "label": "Auto-approve at/above importance",
     "description": "Drafts scored at/above this skip the human review queue "
                    "and become active. The gray band stays 'proposed'."},
    # ── Recall ──
    {"key": "recall_min_score", "group": "Recall", "type": "float",
     "min": 0, "max": 1, "step": 0.05, "label": "Min rerank score",
     "description": "Minimum cross-encoder confidence for a reranked hit to "
                    "surface. FTS/RRF results are rank-gated by top-k instead."},
    {"key": "recall_quality_weighting", "group": "Recall", "type": "bool",
     "label": "Quality-weight recall ranking",
     "description": "Re-rank recalls by importance · veracity · deliberate-recall "
                    "count · recency, not lexical/dense match alone."},
    {"key": "recall_mode", "group": "Recall", "type": "choice",
     "options": ["inline", "subagent"],
     "label": "Deliberate recall mode",
     "description": "inline: the main agent infers intent and calls recall "
                    "directly. subagent: it dispatches a memory-research "
                    "subagent that sifts candidates in isolated context and "
                    "returns a digest — keeps the search out of main context. "
                    "Orthogonal to the always-on auto-inject hook."},
    {"key": "recall_recency_half_life_days", "group": "Recall", "type": "float",
     "min": 0, "step": 1, "label": "Recency half-life (days)",
     "description": "How fast a memory's recency weight decays in recall "
                    "ranking; a deliberate recall resets its clock. 0 = off."},
    # ── Auto-inject ──
    {"key": "inject_top_k", "group": "Auto-inject", "type": "int", "min": 1, "step": 1,
     "label": "Max memories per prompt",
     "description": "How many recalled memories the auto-inject block carries."},
    {"key": "inject_max_chars", "group": "Auto-inject", "type": "int",
     "min": 0, "step": 100, "label": "Inject block budget (chars)",
     "description": "Character budget for the rendered <recalled_experience> block."},
    {"key": "inject_min_overlap", "group": "Auto-inject", "type": "int",
     "min": 0, "step": 1, "label": "Min token overlap to inject",
     "description": "A memory must share at least this many distinct content "
                    "tokens with the prompt to auto-inject (BM25 always ranks "
                    "something). 0 disables the gate."},
    {"key": "inject_dedup_session", "group": "Auto-inject", "type": "bool",
     "label": "Dedup within a session",
     "description": "Skip re-injecting a memory already injected earlier this "
                    "session; a re-match instead reinforces it once."},
    {"key": "inject_dense_via_server", "group": "Auto-inject", "type": "bool",
     "label": "Dense inject via warm server",
     "description": "Borrow the warm `regin serve` for dense + rerank recall at "
                    "inject time (loopback POST), falling back to in-process FTS."},
    {"key": "inject_server_url", "group": "Auto-inject", "type": "string",
     "label": "Warm server URL",
     "description": "Base URL of the serve process the inject hook borrows."},
    {"key": "inject_server_timeout_seconds", "group": "Auto-inject", "type": "float",
     "min": 0, "step": 0.1, "label": "Inject recall timeout (s)",
     "description": "How long the inject hook waits on the warm server before "
                    "falling back to FTS."},
    {"key": "trace_recall", "group": "Auto-inject", "type": "bool",
     "label": "Trace injections",
     "description": "Record each injection as a memory.recall span on the "
                    "session trace for per-prompt auditability."},
    {"key": "inject_skip_commands", "group": "Auto-inject", "type": "list",
     "label": "Skip auto-inject on these commands",
     "description": "Slash commands whose own machinery already pulls recall, so "
                    "the auto-inject block is redundant noise on them. Matched on "
                    "the command token only (first word), case-insensitively, "
                    "leading slash optional per entry — '/goal' never matches "
                    "'/goalpost'. Empty list = inject on every eligible prompt."},
    # ── Topic routing ──
    {"key": "topic_route_inject", "group": "Topic routing", "type": "bool",
     "label": "Inject routed topic context",
     "description": "Route each prompt through the authoritative topic graph and "
                    "prepend a pointer-only <topic_context> block (label + intent "
                    "+ ref paths) for the matched topic. The full wiki stays "
                    "opt-in via the /topic-router skill."},
    {"key": "topic_context_max_chars", "group": "Topic routing", "type": "int",
     "min": 0, "step": 100, "label": "Topic context budget (chars)",
     "description": "Character budget for the rendered <topic_context> block "
                    "(refs trimmed first, then the intent)."},
    {"key": "topic_route_querylog_min_queries", "group": "Topic routing",
     "type": "int", "min": 0, "step": 10,
     "label": "Query-log weighting min prompts",
     "description": "The keyword router down-weights words that saturate this "
                    "repo's own past prompts (on top of the always-on English-"
                    "frequency prior). It stays inert until the cached prompt "
                    "log reaches this many routed prompts, so a sparse log "
                    "can't distort routing. Very high = effectively disabled."},
    {"key": "topic_route_querylog_floor", "group": "Topic routing",
     "type": "float", "min": 0, "max": 1, "step": 0.05,
     "label": "Query-log weighting floor",
     "description": "Lower bound on the query-log down-weight multiplier. A "
                    "word in nearly every prompt shrinks to this factor but "
                    "never to zero, so a repo-ubiquitous term still counts a "
                    "little when it's the only hit."},
    # ── Consolidation ──
    {"key": "dream_enabled", "group": "Consolidation", "type": "bool",
     "label": "Dream (LLM consolidation)",
     "description": "reflect()'s single agentic stage: one call per run decides "
                    "every working row's fate, judges suspect pairs, and may "
                    "synthesize. Off (or no agent): surviving working rows are "
                    "blind-promoted and nothing else is judged."},
    {"key": "promote_allow_retire", "group": "Consolidation", "type": "bool",
     "label": "Let the dream retire rows",
     "description": "Honour the dream's drop/merge verdicts (reversible "
                    "supersede). Off: those verdicts degrade to 'hold' and the "
                    "model can only promote or keep a row working."},
    {"key": "forget_after_days", "group": "Consolidation", "type": "int",
     "min": 0, "step": 1, "label": "Forget never-recalled after (days)",
     "description": "reflect() retires episodic memories this old that were "
                    "never deliberately recalled (recall_count == 0). 0 disables."},
    {"key": "dedup_cosine_threshold", "group": "Consolidation", "type": "float",
     "min": 0, "max": 1, "step": 0.01, "label": "Dedup cosine threshold",
     "description": "reflect() merges two memories when their embedding cosine "
                    "similarity is at least this."},
    {"key": "dedup_text_threshold", "group": "Consolidation", "type": "float",
     "min": 0, "max": 1, "step": 0.01, "label": "Dedup text threshold",
     "description": "Fallback merge threshold on text similarity when no "
                    "embedder is available."},
    {"key": "contradiction_budget", "group": "Consolidation", "type": "int",
     "min": 0, "step": 1, "label": "Suspect pairs per dream",
     "description": "Cap on suspect episodic pairs (same scope, sharing a repo "
                    "file path) offered to one dream. Judged pairs are never "
                    "re-bought; 0 offers none without forgetting the ledger."},
    {"key": "decay_ignored_threshold", "group": "Consolidation", "type": "int",
     "min": 0, "step": 1, "label": "Decay after N ignored verdicts",
     "description": "reflect() decays an unproven episodic memory's importance "
                    "(−0.1/run, floored) once it draws this many feedback "
                    "'ignored' verdicts. Grade-time signal only. 0 disables."},
    {"key": "decay_injected_threshold", "group": "Consolidation", "type": "int",
     "min": 0, "step": 1, "label": "Decay after N un-reinforced injections",
     "description": "reflect() decays an unproven episodic memory once it was "
                    "auto-injected this many times with zero reinforcement "
                    "(always-on signal, no grade needed). 0 disables."},
]


_AGENT_MESSAGES_FIELDS: list[dict] = [
    {"key": "webhook_url", "group": "Webhook", "type": "string",
     "label": "Webhook URL",
     "description": "POST high-severity send_to_user messages here (ntfy / Slack "
                    "/ phone). Empty = webhook off. Stored machine-local."},
    {"key": "webhook_min_severity", "group": "Webhook", "type": "choice",
     "options": ["progress", "note", "lesson", "result", "summary",
                 "warning", "blocker"],
     "label": "Minimum severity",
     "description": "Only messages at or above this severity fire the webhook, "
                    "so a background run can page you on a blocker without spam."},
    {"key": "webhook_timeout_seconds", "group": "Webhook", "type": "float",
     "min": 0, "step": 0.5, "label": "Webhook timeout (s)",
     "description": "How long to wait on the webhook POST before giving up."},
    {"key": "telegram_bot_token", "group": "Telegram", "type": "string",
     "label": "Bot token",
     "description": "Token from @BotFather. Empty = Telegram off. Stored "
                    "machine-local."},
    {"key": "telegram_chat_id", "group": "Telegram", "type": "string",
     "label": "Chat id",
     "description": "Target chat/user id (read it from the bot's getUpdates "
                    "after messaging it once). Required alongside the token."},
    {"key": "telegram_min_severity", "group": "Telegram", "type": "choice",
     "options": ["progress", "note", "lesson", "result", "summary",
                 "warning", "blocker"],
     "label": "Minimum severity",
     "description": "Only messages at or above this severity are sent to "
                    "Telegram."},
    {"key": "telegram_timeout_seconds", "group": "Telegram", "type": "float",
     "min": 0, "step": 0.5, "label": "Telegram timeout (s)",
     "description": "How long to wait on the Telegram API call before giving up."},
    {"key": "lark_webhook_url", "group": "Lark / Feishu", "type": "string",
     "label": "Custom-bot webhook URL",
     "description": "Incoming-webhook URL of a Lark group custom bot (group "
                    "Settings → Bots → Add Bot → Custom Bot). Empty = Lark off. "
                    "Stored machine-local."},
    {"key": "lark_secret", "group": "Lark / Feishu", "type": "string",
     "label": "Signing secret",
     "description": "Only if the bot has signature verification enabled — each "
                    "request is then signed. Leave empty otherwise."},
    {"key": "lark_min_severity", "group": "Lark / Feishu", "type": "choice",
     "options": ["progress", "note", "lesson", "result", "summary",
                 "warning", "blocker"],
     "label": "Minimum severity",
     "description": "Only messages at or above this severity are sent to Lark."},
    {"key": "lark_timeout_seconds", "group": "Lark / Feishu", "type": "float",
     "min": 0, "step": 0.5, "label": "Lark timeout (s)",
     "description": "How long to wait on the Lark webhook POST before giving up."},
    {"key": "push_permission_events", "group": "Interaction events", "type": "bool",
     "label": "Push permission prompts",
     "description": "Surface a pending permission prompt / AskUserQuestion as a "
                    "blocker inbox card that also fans out to the channels above."},
    {"key": "push_plan_events", "group": "Interaction events", "type": "bool",
     "label": "Push plan-ready",
     "description": "Surface a plan ready for review (ExitPlanMode) as a warning "
                    "inbox card that also fans out to the channels above."},
    {"key": "base_url", "group": "General", "type": "string",
     "label": "Base URL",
     "description": "Woven into every push payload so the notification links "
                    "back to the originating session in the regin UI."},
    {"key": "retention_days", "group": "Retention", "type": "int", "min": -1,
     "step": 1, "null_as": -1, "label": "Auto-prune after (days)",
     "description": "Hard-delete inbox messages older than this many days after "
                    "each send_to_user write, keeping the otherwise grow-forever "
                    "inbox bounded with no manual step. -1 = keep forever "
                    "(default). Manual `regin messages prune` is always available."},
    {"key": "retention_keep_pinned", "group": "Retention", "type": "bool",
     "label": "Keep pinned messages",
     "description": "Shield pinned inbox cards from the automatic age-based prune "
                    "above (they can still be removed manually)."},
]

_TOPIC_EVOLUTION_FIELDS: list[dict] = [
    # ── General ──
    {"key": "evolution_enabled", "group": "General", "type": "bool",
     "label": "Topic evolution enabled",
     "description": "Master switch for code-driven topic/memory co-evolution "
                    "(content-drift detection, refresh proposals, expiry). "
                    "Everything below is inert until this is on."},
    {"key": "mechanical_autoapply", "group": "General", "type": "bool",
     "label": "Auto-apply mechanical drift (rename-follow)",
     "description": "On commit, follow git renames into topic refs (written to "
                    "the local overlay, never the approved graph) and memory paths, and "
                    "cascade deleted refs onto linked memories."},
    {"key": "auto_spawn_agents", "group": "General", "type": "bool",
     "label": "Auto-spawn the drafting agent for refresh proposals",
     "description": "Hand content-drift refresh proposals to the external "
                    "drafting agent (needs a configured proposal agent). A real "
                    "cost — off by default even when evolution is on."},
    # ── Detection ──
    {"key": "content_drift_cosine", "group": "Detection", "type": "float",
     "min": 0, "max": 1, "step": 0.05, "label": "Content-drift cosine floor",
     "description": "When a changed ref file's embedding sits at/above this "
                    "cosine to its stored digest, the change is treated as "
                    "trivial and spared; below it, the topic is flagged drifted."},
    # ── Proposals ──
    {"key": "drift_proposal_batch_max", "group": "Proposals", "type": "int",
     "min": 0, "step": 1, "label": "Max proposals per evolve pass",
     "description": "Cap on refresh proposals emitted (and agents spawned) in "
                    "one evolve pass, so a large change can't flood the queue. "
                    "0 = unbounded."},
    {"key": "auto_proposal_expire_days", "group": "Proposals", "type": "int",
     "min": 0, "step": 1, "label": "Auto-expire unreviewed proposals (days)",
     "description": "Auto-generated proposals left unreviewed this many days are "
                    "retired (ignored) so the review queue can't rot. 0 = never."},
    {"key": "auto_review_notes", "group": "Proposals", "type": "bool",
     "label": "Auto-write LLM review notes on proposal runs",
     "description": "When a proposal run completes, an LLM reviewer attaches a "
                    "review note (regenerate/accept/dismiss recommendation) as a "
                    "feedback thread that carries into the next run. Needs a "
                    "configured proposal agent — a real cost, off by default even "
                    "when evolution is on."},
]


def _settings_blocks() -> dict:
    """Registry of round-trippable nested settings blocks. `scope` routes the
    write: agent_messages goes local because webhook_url can hold a secret
    token that must not land in git-tracked settings.json."""
    from lib.settings import (AgentMemoryConfig, AgentMessagesConfig,
                              TopicEvolutionConfig)
    return {
        "agent-memory": {"attr": "agent_memory", "model": AgentMemoryConfig,
                         "fields": _AGENT_MEMORY_FIELDS, "scope": "shared",
                         "label": "Agent memory settings"},
        "agent-messages": {"attr": "agent_messages", "model": AgentMessagesConfig,
                           "fields": _AGENT_MESSAGES_FIELDS, "scope": "local",
                           "label": "Agent message settings"},
        "topic-evolution": {"attr": "topic_evolution",
                            "model": TopicEvolutionConfig,
                            "fields": _TOPIC_EVOLUTION_FIELDS, "scope": "shared",
                            "label": "Topic evolution settings"},
    }


def _to_bool(val) -> bool:
    if isinstance(val, str):
        return val.strip().lower() in ('true', '1', 'yes', 'on')
    return bool(val)


def _coerce_number(val, kind: str):
    try:
        return int(val) if kind == 'int' else float(val)
    except (TypeError, ValueError):
        return None


def _bounded(field: dict, num) -> "str | None":
    lo, hi = field.get('min'), field.get('max')
    if lo is not None and num < lo:
        return f"{field['key']} must be ≥ {lo}"
    if hi is not None and num > hi:
        return f"{field['key']} must be ≤ {hi}"
    return None


def _coerce_list(field: dict, val) -> tuple:
    """Strip each entry and drop the empties → list[str]."""
    if not isinstance(val, list):
        return None, f"{field['key']} must be a list"
    return [s for v in val if (s := str(v).strip())], None


def _coerce_choice(field: dict, val) -> tuple:
    sval = str(val)
    if sval in field.get('options', []):
        return sval, None
    return None, f"{field['key']} must be one of {field.get('options')}"


def _coerce_scalar(field: dict, val) -> tuple:
    """(value, error) for one field — exactly one is non-None."""
    kind = field['type']
    if kind == 'bool':
        return _to_bool(val), None
    if kind == 'string':
        return ('' if val is None else str(val).strip()), None
    if kind == 'list':
        return _coerce_list(field, val)
    if kind == 'choice':
        return _coerce_choice(field, val)
    num = _coerce_number(val, kind)
    if num is None:
        return None, f"{field['key']} must be a number"
    err = _bounded(field, num)
    if err:
        return None, err
    # A `null_as` sentinel (e.g. -1) is how the UI represents the model's None
    # in a plain number input; fold it back to None so persisted settings stay
    # canonical (the inverse of the GET-side substitution in _field_payload).
    if field.get('null_as') is not None and num == field['null_as']:
        return None, None
    return num, None


def _coerce_block(fields: list[dict], body: dict) -> "tuple[dict, list[str]]":
    """Coerce + validate only the exposed fields present in `body`."""
    updates: dict = {}
    errors: list[str] = []
    for field in fields:
        if field['key'] not in body:
            continue
        value, err = _coerce_scalar(field, body[field['key']])
        if err:
            errors.append(err)
        else:
            updates[field['key']] = value
    return updates, errors


def _field_payload(current, defaults, field: dict) -> dict:
    """One field's metadata + live value + default, mapping a None model value
    to the field's `null_as` sentinel so a plain number input can represent it
    (the inverse of the PUT-side fold in _coerce_scalar)."""
    value = getattr(current, field["key"])
    default = getattr(defaults, field["key"])
    sentinel = field.get("null_as")
    if sentinel is not None:
        value = sentinel if value is None else value
        default = sentinel if default is None else default
    return {**field, "value": value, "default": default}


def _block_get(name: str):
    """Field metadata + current value + default for one registered block."""
    block = _settings_blocks().get(name)
    if block is None:
        return jsonify({'error': 'unknown settings block'}), 404
    from lib.settings import settings as _settings
    current = getattr(_settings, block['attr'])
    defaults = block['model']()
    fields = [_field_payload(current, defaults, f) for f in block['fields']]
    return jsonify({"fields": fields})


def _scope_block_base(attr: str, scope: str) -> dict:
    """The block dict from the file the save will target, so a partial PUT
    merges against the full on-disk block and cannot drop the fields it did
    not send.

    `_load_settings()` shallow-merges the two files (`{**shared, **local}`), so
    for a block present in BOTH it returns only the *local* copy — using that as
    the merge base silently wipes every shared-only field when the block is
    re-saved to the shared file. The scope's own file is the field-preserving
    base; local-only overrides stay in local and are read-merged at load."""
    from lib import settings as _cfg
    path = _cfg.SETTINGS_LOCAL_PATH if scope == 'local' else _cfg.SETTINGS_PATH
    return dict(_cfg._load_json(path).get(attr) or {})


def _block_put(name: str):
    """Validate + persist edited fields for one block, preserving unexposed
    on-disk overrides, and reload the singleton so it applies live."""
    block = _settings_blocks().get(name)
    if block is None:
        return jsonify({'ok': False, 'error': 'unknown settings block'}), 404
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({'ok': False, 'error': 'invalid JSON body'}), 400
    updates, errors = _coerce_block(block['fields'], body)
    if errors:
        return jsonify({'ok': False, 'errors': errors}), 400
    merged = _scope_block_base(block['attr'], block['scope'])
    merged.update(updates)
    try:
        block['model'](**merged)  # full pydantic re-validation
    except Exception as exc:
        return jsonify({'ok': False, 'errors': [str(exc)]}), 400
    save_settings({block['attr']: merged}, scope=block['scope'])
    return jsonify({'ok': True, 'msg': f"{block['label']} saved."})


@settings_bp.route('/api/settings/agent-memory')
def api_agent_memory_settings():
    return _block_get('agent-memory')


@settings_bp.route('/api/settings/agent-memory', methods=['PUT'])
@require_editor
def api_update_agent_memory_settings():
    return _block_put('agent-memory')


@settings_bp.route('/api/settings/agent-messages')
def api_agent_messages_settings():
    return _block_get('agent-messages')


@settings_bp.route('/api/settings/agent-messages', methods=['PUT'])
@require_editor
def api_update_agent_messages_settings():
    return _block_put('agent-messages')


@settings_bp.route('/api/settings/topic-evolution')
def api_topic_evolution_settings():
    return _block_get('topic-evolution')


@settings_bp.route('/api/settings/topic-evolution', methods=['PUT'])
@require_editor
def api_update_topic_evolution_settings():
    return _block_put('topic-evolution')


@settings_bp.route('/api/settings')
def api_settings():
    return jsonify(get_current_values())


def _coerce_setting_value(default, val):
    """Coerce one POSTed flat-settings value to the type of its schema
    default (list/bool/int/str). `bool` is checked before `int` because
    `bool` subclasses `int`, so the int branch would otherwise match and
    `int("false")` would raise + fall through to a raw string."""
    if isinstance(default, list):
        return [v.strip() for v in val if v.strip()] if isinstance(val, list) else val
    if isinstance(default, bool):
        if isinstance(val, str):
            return val.strip().lower() in ('true', '1', 'yes', 'on')
        return bool(val)
    if isinstance(default, int):
        try:
            return int(val)
        except (ValueError, TypeError):
            return val
    return val


@settings_bp.route('/api/settings', methods=['POST'])
@require_editor
def api_settings_save():
    data = request.get_json(silent=True) or {}
    updates = {}
    for key, default, _ in SETTINGS_SCHEMA:
        if key not in data:
            existing = _load_settings()
            existing.pop(key, None)
            save_settings(existing)
            continue
        updates[key] = _coerce_setting_value(default, data[key])
    save_settings(updates)
    return jsonify({'ok': True, 'msg': 'Settings saved. Restart server to apply.'})


# ── Provider settings (multi-provider enablement + per-provider overrides) ──

# Derived from the typed model so a new path override can't be added there
# without the endpoint picking it up (and vice versa).
_PROVIDER_PATH_FIELDS = list(ProviderPathOverrides.model_fields)


def _provider_default_paths(provider) -> dict:
    """Resolved default paths for a provider when no override is set."""
    try:
        return {
            'skills_dir': str(provider.global_skills_dir()),
            'plans_dir': str(provider.plans_dir()),
            'traces_dir': str(provider.traces_dir()),
            'hook_settings_path': str(provider.hook_settings_path()),
            'hook_manager_config_path': str(provider.hook_manager_config_path()),
            'hook_payload_log_path': str(provider.hook_payload_log_path()),
            'transcript_projects_dir': str(provider.transcript_projects_dir()),
        }
    except Exception:
        return {field: '' for field in _PROVIDER_PATH_FIELDS}


def _handler_defaults() -> list[dict]:
    """Registry-defined handler metadata for the provider-config UI.

    Returns default priorities independent of any per-provider overrides so
    the UI can show "default N" when editing overrides.
    """
    from hook_manager.registry import REGISTRY
    return [
        {
            'name': h.name,
            'label': h.label,
            'kind': h.kind,
            'events': list(h.events),
            'default_priority': h.priority,
        }
        for h in REGISTRY
    ]


def _provider_config_row(provider_id: str) -> dict:
    """Current settings-based config for one provider, plus derived metadata."""
    merged = _load_settings()
    providers_cfg = merged.get('providers') or {}
    cfg = providers_cfg.get(provider_id) or {}
    if isinstance(cfg, ProviderConfig):
        cfg = cfg.model_dump()
    elif not isinstance(cfg, dict):
        cfg = {}

    provider = build_provider(provider_id)
    default_paths = _provider_default_paths(provider)
    active = active_provider_id()

    return {
        'id': provider_id,
        'name': provider.display_name,
        'active': provider_id == active,
        'enabled': bool(cfg.get('enabled', provider_id == active)),
        'capabilities': asdict(provider.capabilities),
        'default_paths': default_paths,
        'path_overrides': {
            field: cfg.get(field) if cfg.get(field) not in (None, '') else None
            for field in _PROVIDER_PATH_FIELDS
        },
        'disabled_handlers': list(cfg.get('disabled_handlers') or []),
        'priority_overrides': dict(cfg.get('priority_overrides') or {}),
    }


@settings_bp.route('/api/settings/providers')
def api_provider_settings():
    return jsonify({
        'providers': [_provider_config_row(pid) for pid in list_visible_provider_ids()],
        'handler_defaults': _handler_defaults(),
    })


def _coerce_priority_overrides(pid: str, overrides: dict, errors: list) -> dict:
    """Coerce `{handler: priority}` to ints, appending per-key errors."""
    cleaned: dict = {}
    for k, v in overrides.items():
        if not k:
            continue
        try:
            cleaned[str(k)] = int(v)
        except (TypeError, ValueError):
            errors.append(f'{pid}.priority_overrides.{k} must be an integer')
    return cleaned


def _clean_path_overrides(raw: dict) -> dict:
    """Path-override fields present in `raw`, normalized ('' → None)."""
    out: dict = {}
    for field in _PROVIDER_PATH_FIELDS:
        if field in raw:
            val = raw[field]
            out[field] = None if val in (None, '') else str(val)
    return out


def _clean_provider_entry(pid: str, raw: dict, errors: list) -> dict:
    """Coerce one provider's POST body into a settings entry, collecting
    validation problems in `errors`. Only keys present in `raw` are written,
    so partial updates leave untouched fields alone."""
    entry: dict = _clean_path_overrides(raw)
    if 'enabled' in raw:
        entry['enabled'] = bool(raw['enabled'])

    if 'disabled_handlers' in raw:
        handlers = raw['disabled_handlers']
        if isinstance(handlers, list):
            entry['disabled_handlers'] = [str(h) for h in handlers if h]
        else:
            errors.append(f'{pid}.disabled_handlers must be a list')

    if 'priority_overrides' in raw:
        overrides = raw['priority_overrides']
        if isinstance(overrides, dict):
            entry['priority_overrides'] = _coerce_priority_overrides(pid, overrides, errors)
        else:
            errors.append(f'{pid}.priority_overrides must be an object')

    return entry


def _collect_provider_entries(providers_update: dict, existing: dict) -> tuple:
    """(cleaned_entries, errors) for the POSTed provider map, each entry
    merged over its existing on-disk config so partial updates accrete."""
    cleaned: dict = {}
    errors: list[str] = []
    for pid, raw in providers_update.items():
        if not is_provider_id(pid):
            errors.append(f'unknown provider: {pid}')
            continue
        if not isinstance(raw, dict):
            errors.append(f'{pid}: expected object')
            continue
        entry = _clean_provider_entry(pid, raw, errors)
        if entry:
            cleaned[pid] = {**(existing.get(pid) or {}), **entry}
    return cleaned, errors


def _nonempty_provider_configs(providers: dict) -> dict:
    """Drop entries that carry only defaults so we don't persist noise."""
    return {
        pid: cfg for pid, cfg in providers.items()
        if cfg and any(v not in (None, [], {}, '') for v in cfg.values())
    }


def _first_invalid_provider(providers: dict) -> "str | None":
    """Validate each config against the typed model; first error or None."""
    for pid, cfg in providers.items():
        try:
            ProviderConfig(**cfg)
        except Exception as exc:
            return f'{pid}: {exc}'
    return None


@settings_bp.route('/api/settings/providers', methods=['PUT'])
@require_editor
def api_update_provider_settings():
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict) or 'providers' not in body:
        return jsonify({'ok': False, 'error': 'providers object required'}), 400

    providers_update = body['providers']
    if not isinstance(providers_update, dict):
        return jsonify({'ok': False, 'error': 'providers must be an object'}), 400

    existing = _load_settings().get('providers') or {}
    if not isinstance(existing, dict):
        existing = {}

    cleaned, errors = _collect_provider_entries(providers_update, existing)
    if errors:
        return jsonify({'ok': False, 'errors': errors}), 400

    # Preserve providers that were not sent (partial updates), then drop
    # entries left holding only defaults.
    new_providers = _nonempty_provider_configs({**existing, **cleaned})

    invalid = _first_invalid_provider(new_providers)
    if invalid:
        return jsonify({'ok': False, 'errors': [invalid]}), 400

    save_settings({'providers': new_providers}, scope='local')
    return jsonify({'ok': True, 'msg': 'Provider settings saved.'})
