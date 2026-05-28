"""Typed settings for regin (pydantic-settings).

Single declarative source for every path, port, and mode the app reads
at boot. Replaces the hand-rolled merge of `config/settings.json` +
`config/settings.local.json` + `REGIN_*` env vars that used to live in
`lib/config.py`.

Precedence (highest to lowest), mirroring the old behavior:

    REGIN_* environment variable
    > config/settings.local.json (machine-local, gitignored)
    > config/settings.json        (shared, git-tracked)
    > field default (derived from REGIN_DATA_DIR / XDG_DATA_HOME / ~)

The legacy module-level constants that used to live in `lib/config.py`
(PATTERNS_DIR, PROJECT_ROOT, …) plus the settings.json CRUD helpers
(save_settings, get_current_values, SETTINGS_SCHEMA) now live at the
bottom of this module. New code should prefer the `settings` instance.

Hot-path env vars consumed inside request handlers (`REGIN_INGEST_*`,
`REGIN_TRACE_TEST*`) are intentionally NOT surfaced here — those are
read at call time so tests can monkey-patch them. They will migrate
when their consuming modules are refactored.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal, Tuple, Type

from pydantic import BaseModel, Field
from pydantic_settings import (
    BaseSettings,
    JsonConfigSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)


class RuleEngineConfig(BaseModel):
    """One rule engine's wiring.

    `kind` selects the adapter class. `id` is the per-instance identifier
    used to look up the engine via `lib.rule_engines.get(id)`. Engines are
    free to define their own extra fields (e.g. `grit_dir` for the grit
    engine, `bundle_root` for the generic bundle engine).
    """

    id: str
    kind: str = "grit"
    enabled: bool = True
    grit_dir: Path | None = None
    bundle_root: Path | None = None
    language_ids: tuple[str, ...] = ("python",)
    # Radon-specific (cyclomatic complexity threshold + violation severity).
    # Both optional: the engine falls back to its own defaults when unset.
    min_grade: str | None = None
    severity: str | None = None


class ProviderPathOverrides(BaseModel):
    """Optional per-provider path overrides.

    These keys are intentionally path-only in the first milestone; they
    let us redirect integration points without changing provider code.
    """

    skills_dir: Path | None = None
    plans_dir: Path | None = None
    traces_dir: Path | None = None
    hook_settings_path: Path | None = None
    hook_manager_config_path: Path | None = None
    hook_payload_log_path: Path | None = None
    transcript_projects_dir: Path | None = None


class RuleTriggerThresholds(BaseModel):
    """Thresholds for classifying rule health on the /trace/triggers tab.

    A rule is `noisy` when its trigger rate over the active range meets
    BOTH `noisy_min_rate_pct` AND `noisy_min_fires` — a pure-% gate
    misfires for low-N rules (1/2 = 50%). It's `dead` when fires == 0
    AND checks >= `dead_min_checks` (so a brand-new rule with 0/0 isn't
    flagged before it's been exercised). Otherwise it's `active`.
    """

    noisy_min_rate_pct: int = 30
    noisy_min_fires: int = 5
    dead_min_checks: int = 3
    default_range: Literal["24h", "7d", "30d", "all"] = "7d"


class TopicProposalExternalAgent(BaseModel):
    """One external command that can draft topic proposals."""

    command: str
    args: list[str] = Field(default_factory=list)
    timeout_seconds: int = 600
    cwd: Path | None = None


# Project-root-relative paths — fixed by where this file lives.
_PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
_CONFIG_DIR: Path = _PROJECT_ROOT / "config"
_SHARED_SETTINGS_PATH: Path = _CONFIG_DIR / "settings.json"
_LOCAL_SETTINGS_PATH: Path = _CONFIG_DIR / "settings.local.json"


def _xdg_data_home() -> Path:
    """XDG_DATA_HOME or its portable default (`~/.local/share`)."""
    env = os.environ.get("XDG_DATA_HOME")
    return Path(env) if env else Path.home() / ".local" / "share"


def _default_data_dir() -> Path:
    """Honour REGIN_DATA_DIR for the user-local data root. Evaluated at
    class-definition time so `Settings()` with no env picks up the right
    path from the process env."""
    env = os.environ.get("REGIN_DATA_DIR")
    return Path(env) if env else _xdg_data_home() / "regin"


_DATA_DIR_DEFAULT: Path = _default_data_dir()


class Settings(BaseSettings):
    """Declarative view of every configurable value regin reads at boot."""

    model_config = SettingsConfigDict(
        env_prefix="REGIN_",
        extra="ignore",
        case_sensitive=False,
        # JSON sources plug in via `settings_customise_sources` below.
    )

    # ── Paths ────────────────────────────────────────────────
    # The repository checkout root. Fixed by where this file lives, not
    # user-configurable; exposed as a field (rather than a constant) so
    # callers read it via `settings.project_root` and tests can redirect
    # it for isolation.
    project_root: Path = Field(default_factory=lambda: _PROJECT_ROOT)

    # The user-local data root. Overridable via REGIN_DATA_DIR.
    data_dir: Path = Field(default_factory=_default_data_dir)

    # Where procedure guides (patterns) live. Default:
    # $REGIN_DATA_DIR/patterns. Honours REGIN_PATTERNS_DIR.
    patterns_dir: Path = Field(default=_DATA_DIR_DEFAULT / "patterns")

    # GritQL rule sources + generated indexes. Default:
    # $REGIN_DATA_DIR/grit. Honours REGIN_GRIT_DIR. Used as the default
    # grit dir for a `rule_engines` entry of kind 'grit' that doesn't set
    # its own `grit_dir`.
    grit_dir: Path = Field(default=_DATA_DIR_DEFAULT / "grit")

    # Rule engines (linters, structural rewriters) regin should load.
    # An empty list means regin runs as a generic harness with no rule
    # enforcement at all (unless `bundle_autoload` discovers a bundle).
    rule_engines: list[RuleEngineConfig] = Field(default_factory=list)

    # When true, scan `patterns_dir/*/regin-bundle.{yaml,json}` and load
    # each as a `BundleEngine`. Explicit `rule_engines` entries with the
    # same `id` always win — auto-discovered entries only fill gaps.
    bundle_autoload: bool = True

    # Config-only language→file-extension overrides for the PostToolUse
    # rule gate (hook_manager/handlers/rule_check.py). Maps a language id
    # to the file extensions (leading dot, e.g. ".kt") that identify it.
    # Lets you point a rule engine at a brand-new language with no code
    # change: declare the id→extensions here and list the id in an
    # engine's `language_ids`. Consulted BEFORE the lib/languages registry
    # and the handler's built-in fallback map, so it overrides either.
    language_extensions: dict[str, list[str]] = Field(default_factory=dict)

    # User-curated tag definitions YAML.
    tags_path: Path = Field(default=_DATA_DIR_DEFAULT / "config" / "tags.yaml")

    # ── Activity logs (per-feature JSONL via loguru) ─────────
    # Where activity log files live. Default: <data_dir>/logs.
    # Honours REGIN_LOG_DIR. See lib/activity_log.py.
    log_dir: Path = Field(default=_DATA_DIR_DEFAULT / "logs")
    # Age-based retention for rotated activity logs (days).
    log_retention_days: int = 14
    # Size cap per pre-rotation file. Default 50 MB.
    log_max_bytes_per_file: int = 50 * 1024 * 1024
    # Feature registry — typo guard. Unknown features get tagged
    # `feature=other` with a one-time stderr warning. All features
    # share `regin.log`; this list controls validation only.
    activity_log_features: list[str] = Field(
        default_factory=lambda: [
            "hooks", "patterns", "sync", "web", "cli", "rules",
            "trace_ingest", "topics", "auth", "rebuild", "other",
        ]
    )

    # Auto-tagging rules YAML.
    auto_tag_rules_path: Path = Field(default=_DATA_DIR_DEFAULT / "config" / "auto_tag_rules.yaml")

    # Per-user overlay for PostToolUse payload schemas. The validator
    # merges <overlay>/<agent>/<tool>.schema.json on top of the repo-
    # tracked baseline at lib/trace/payload_schemas/<agent>/. Ratifying
    # a drift finding writes to the overlay, never to the baseline, so
    # `git pull` never conflicts with local schema customizations.
    payload_schemas_overlay_dir: Path = Field(
        default=_DATA_DIR_DEFAULT / "payload_schemas",
    )

    # Master switch for the harness Diagnostics surface: PostToolUse
    # payload schema validation, drift recording, and ~/.claude/
    # hook-payloads.jsonl appends. Default OFF — this is a maintainer
    # tool, and common users shouldn't pay the per-hook overhead they
    # didn't ask for. Toggleable from the Diagnostics page or settings.
    diagnostics_enabled: bool = False

    # ── Provider deploy targets ────────────────────────────
    active_provider: Literal["claude", "codex", "generic"] = "claude"
    providers: dict[str, ProviderPathOverrides] = Field(default_factory=dict)

    # When false, only the `claude` provider (plus the active provider, if it
    # was explicitly switched away from claude) is exposed to UI surfaces like
    # SettingsView / /api/hooks / /api/providers. Flip to true to surface the
    # experimental `codex` and `generic` providers.
    experimental_providers: bool = False

    # Gate for the SKILL.md concealment-experiments feature. When false
    # (default), the Experiments nav link, the pattern-detail Experiments
    # tab + create-experiment affordance, and the /experiments routes are
    # hidden in the UI. The backend table and conceal filter remain so
    # any rows already written still drive deploy behavior.
    experimental_conceal: bool = False

    # Gate for the dense (semantic) pattern search UI on the Patterns
    # page. When false (default), the "Dense search" toggle, query
    # input, and Route button are hidden. The /patterns/route backend
    # remains reachable so any direct callers keep working.
    experimental_dense_search: bool = False

    # Hybrid pattern-search reranker threshold. The SkillRouter
    # cross-encoder pass runs only when the fused candidate set has at
    # least this many items. Default = 1 (rerank-always, matching
    # SkillRouter's evaluated pipeline). Raise to skip rerank on tiny
    # candidate sets where it adds latency without lift.
    dense_rerank_min_corpus: int = 1

    # When False, `pattern_router.route()` skips the SkillRouter dense
    # leg and ranks via BM25/FTS5 only — no embedding model load, no
    # ~1.2 GB download, no rerank. Ablation at this corpus size
    # (scripts/ablate_pattern_router.py) shows top-1 unchanged vs the
    # full hybrid; flip back to True once the pattern catalog grows
    # past ~100 overlapping items. Distinct from
    # `experimental_dense_search`, which is a UI feature gate.
    pattern_router_dense_enabled: bool = True

    # Legacy Claude-specific path knob kept for back-compat while
    # provider adapters are introduced.
    skills_dir: Path = Field(default_factory=lambda: Path.home() / ".claude" / "skills")

    # ── Discovery ───────────────────────────────────────────
    # Explicit list of registered repository paths. Each entry MUST point
    # at a git working tree; managed through the /repos web UI or the
    # `regin add-repo` / `regin remove-repo` CLI commands.
    repo_paths: list[Path] = Field(default_factory=list)

    # ── Web ─────────────────────────────────────────────────
    web_port: int = 8321

    # ── Mode + external services ────────────────────────────
    # 'standalone' = local SQLite for auth/audit. 'shared' = MySQL.
    mode: Literal["standalone", "shared"] = "standalone"

    # MySQL URL (shared mode). Honours REGIN_DATABASE_URL.
    database_url: str | None = None

    # Optional regin-skillhub server (for `pattern promote`).
    skillhub_url: str = "http://127.0.0.1:8322"

    # Topic proposals are drafted by an external tool-using agent.
    topic_proposal_external_agents: dict[str, TopicProposalExternalAgent] = Field(default_factory=dict)

    # How many non-latest, non-pinned `graph_snapshots` rows to retain
    # per repo. `apply_diff` prunes beyond this after every accept/merge/
    # replace. Pinned rows and `is_latest=1` always survive. Set to 0 to
    # disable inline pruning entirely.
    topic_snapshot_keep: int = 50

    # ── Rule trigger health ─────────────────────────────────
    # Thresholds for classifying each rule as active / noisy / dead on
    # the /trace/triggers tab. Editable via /settings → rule-trigger
    # thresholds card (PR-3 onward).
    rule_trigger_thresholds: RuleTriggerThresholds = Field(default_factory=RuleTriggerThresholds)

    # ── Trace ───────────────────────────────────────────────
    # Capture each assistant turn's response text into session_spans
    # (`assistant_response` spans). Off-switch for users who don't want
    # response text persisted in the trace DB.
    capture_assistant_response: bool = True
    # Per-response byte cap. Spans are bulk-loaded with the session
    # detail response, so the cap stays conservative — anything larger
    # is truncated with a marker before being POSTed to /api/session-spans.
    assistant_response_max_bytes: int = 50_000
    # User-submitted images in prompts: per-image byte cap (drop if over)
    # and per-prompt image-count cap.
    capture_prompt_images: bool = True
    prompt_image_max_bytes: int = 5_000_000   # 5 MB
    prompt_images_max_count: int = 10

    # Per-model context-window overrides (model id -> token count). Merged
    # on top of the built-in table in `lib/tokens/model_windows.py`. Use
    # this to track windows for in-house or preview models, or to correct
    # the default if Anthropic ships a new window size mid-cycle.
    model_context_windows: dict[str, int] = Field(default_factory=dict)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: Type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> Tuple[PydanticBaseSettingsSource, ...]:
        """Layer: env > settings.local.json > settings.json > defaults."""
        shared = JsonConfigSettingsSource(settings_cls, json_file=_SHARED_SETTINGS_PATH)
        local = JsonConfigSettingsSource(settings_cls, json_file=_LOCAL_SETTINGS_PATH)
        return (init_settings, env_settings, local, shared, file_secret_settings)

    def model_post_init(self, _context) -> None:
        """If path fields were left at their original defaults, rewrite them
        relative to the resolved `data_dir`. Users who set only
        REGIN_DATA_DIR expect every downstream path to follow."""
        if self.data_dir != _DATA_DIR_DEFAULT:
            self._rebase_default_paths()

        # Expand `~` in path fields so downstream `os.path.join(str(path), ...)`
        # doesn't accidentally produce literal `~` components.
        for field in ("patterns_dir", "grit_dir", "tags_path",
                      "auto_tag_rules_path", "skills_dir", "data_dir",
                      "log_dir", "payload_schemas_overlay_dir"):
            current = getattr(self, field)
            expanded = Path(os.path.expanduser(str(current)))
            if expanded != current:
                object.__setattr__(self, field, expanded)

        # Repo paths may be specified as strings in settings.json; expand `~`.
        self._expand_repo_paths()
        return

    # Tuple of (field_name, subpath under _DATA_DIR_DEFAULT) for every
    # path that should follow data_dir when only REGIN_DATA_DIR was set.
    _DEFAULT_REBASE_FIELDS = (
        ("patterns_dir", ("patterns",)),
        ("grit_dir", ("grit",)),
        ("tags_path", ("config", "tags.yaml")),
        ("auto_tag_rules_path", ("config", "auto_tag_rules.yaml")),
        ("log_dir", ("logs",)),
        ("payload_schemas_overlay_dir", ("payload_schemas",)),
    )

    def _rebase_default_paths(self) -> None:
        for field, parts in self._DEFAULT_REBASE_FIELDS:
            default_value = _DATA_DIR_DEFAULT.joinpath(*parts)
            if getattr(self, field) == default_value:
                object.__setattr__(self, field, self.data_dir.joinpath(*parts))

    def _expand_repo_paths(self) -> None:
        expanded_repos = [Path(os.path.expanduser(str(p))) for p in self.repo_paths]
        if expanded_repos != list(self.repo_paths):
            object.__setattr__(self, "repo_paths", expanded_repos)

        # Provider path overrides are optional; expand any ~ values.
        for provider_id, override in self.providers.items():
            for field in (
                "skills_dir",
                "plans_dir",
                "traces_dir",
                "hook_settings_path",
                "hook_manager_config_path",
                "hook_payload_log_path",
                "transcript_projects_dir",
            ):
                current = getattr(override, field)
                if current is None:
                    continue
                expanded = Path(os.path.expanduser(str(current)))
                if expanded != current:
                    setattr(override, field, expanded)


# Module-level singleton. Re-exported so callers can do:
#
#   from lib.settings import settings
#
# If a caller needs a fresh parse (e.g. after `save_settings()` updated
# the JSON files), they should import `reload_settings` and call it.
settings = Settings()


def reload_settings() -> Settings:
    """Re-read env + JSON files and refresh the module singleton IN PLACE.

    The `settings` object's identity is preserved, so modules and tests
    that captured `from lib.settings import settings` at import time see
    the refreshed values — and `monkeypatch.setattr(settings, ...)` always
    lands on the live instance. Returns the same, mutated instance.
    """
    fresh = Settings()
    for _name in type(settings).model_fields:
        object.__setattr__(settings, _name, getattr(fresh, _name))
    return settings


# ── Config-file paths for the settings.json CRUD below ────────────
#
# Kept as module-level constants because the CRUD helpers
# (save_settings / _load_settings / get_current_values), the /settings
# web UI, and several tests reference and monkeypatch them. Every other
# legacy constant was eliminated — callers now read the typed `settings`
# instance directly (e.g. `settings.patterns_dir`, `settings.mode`).

CONFIG_DIR: str = str(_CONFIG_DIR)
SETTINGS_PATH: str = str(_SHARED_SETTINGS_PATH)
SETTINGS_LOCAL_PATH: str = str(_LOCAL_SETTINGS_PATH)


# Settings that are machine-specific (not shared via git). Keyed by raw
# key name because `save_settings(scope)` and web/blueprints/settings.py
# key on the string. `diagnostics_enabled` is per-machine on purpose: a
# laptop can run diagnostics ON while a shared deploy stays OFF, and
# routing it through local keeps the diagnostics pill and the /settings
# page writing to the same file.
LOCAL_SETTINGS_KEYS: set[str] = {
    "repo_paths", "active_provider", "providers",
    "skills_dir", "skillhub_url",
    "patterns_dir", "grit_dir", "tags_path",
    "auto_tag_rules_path",
    "diagnostics_enabled",
}


# Schema the /settings page renders. Each entry is (key, default,
# description); the default mirrors the corresponding Settings field.
# NOTE: `repo_paths` is intentionally omitted — the /repos UI (and
# `regin add-repo`/`remove-repo`) manage it.
SETTINGS_SCHEMA: list[tuple[str, object, str]] = [
    ("web_port", 8321,
     "Web dashboard port"),
    ("active_provider", "claude",
     "Active agent provider (claude, codex, generic)"),
    ("experimental_providers", False,
     "Surface experimental agent providers (codex, generic) in the Settings hook-manager UI. When off, only claude is shown."),
    ("experimental_conceal", False,
     "Surface the SKILL.md concealment-experiments UI (Experiments nav link, pattern-detail tab, /experiments routes). When off, the broken-by-design feature is hidden."),
    ("experimental_dense_search", False,
     "Surface the dense (semantic) pattern search UI on the Patterns page (toggle, query input, Route button). When off, only the standard tag/category filters are shown."),
    ("dense_rerank_min_corpus", 1,
     "Hybrid pattern-search reranker threshold: the SkillRouter cross-encoder runs only when the fused candidate set has at least this many items. Default 1 = rerank always."),
    ("skills_dir", str(Path.home() / ".claude" / "skills"),
     "Claude Code skills deploy directory"),
    ("mode", "standalone",
     "Server mode: standalone (local SQLite) or shared (MySQL for users/audit)"),
    ("skillhub_url", "http://127.0.0.1:8322",
     "Base URL of the optional regin-skillhub server (for `pattern promote`)"),
    ("patterns_dir", str(_xdg_data_home() / "regin" / "patterns"),
     "Directory where procedure guides (patterns) are stored (user-local data)"),
    ("grit_dir", str(_xdg_data_home() / "regin" / "grit"),
     "Directory where GritQL rule sources and generated indexes live (user-local data)"),
    ("tags_path", str(_xdg_data_home() / "regin" / "config" / "tags.yaml"),
     "Path to user-curated tag definitions YAML (user-local data)"),
    ("auto_tag_rules_path", str(_xdg_data_home() / "regin" / "config" / "auto_tag_rules.yaml"),
     "Path to auto-tagging rules YAML: repo-name patterns, annotations, base classes (user-local data)"),
    ("capture_assistant_response", True,
     "Capture each assistant turn's response text into session_spans (assistant_response spans)"),
    ("assistant_response_max_bytes", 50_000,
     "Per-response byte cap before truncation (spans are bulk-loaded with session detail)"),
    ("diagnostics_enabled", False,
     "Maintainer Diagnostics: payload schema validation, drift detection, and raw payload log appends. Default off; turn on if you're debugging the harness or tracking Anthropic payload-shape changes."),
]


# ── settings.json CRUD (backs the /settings web UI) ───────────────

# Keys whose values are file paths — `~` expanded at read time.
_PATH_KEYS: set[str] = {
    "repo_paths", "skills_dir", "patterns_dir",
    "grit_dir", "tags_path", "auto_tag_rules_path",
    "log_dir", "payload_schemas_overlay_dir", "data_dir",
}


def _load_json(path: str) -> dict:
    """Load a JSON file, returning {} on error."""
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _load_settings() -> dict:
    """Merged settings dict: shared (settings.json) + local overrides.

    Returns a plain dict (not the Settings class) because call sites
    dump this to JSON for the dashboard. Prefer the typed `settings`
    instance in new code.
    """
    shared = _load_json(SETTINGS_PATH)
    local = _load_json(SETTINGS_LOCAL_PATH)
    return {**shared, **local}


def _expand_paths(value):
    """Expand `~` in a path string or list of path strings."""
    if isinstance(value, str):
        return os.path.expanduser(value)
    if isinstance(value, list):
        return [os.path.expanduser(v) if isinstance(v, str) else v for v in value]
    return value


def _get(key: str, default):
    """Read a setting from the merged JSON files, preferring the value
    found there over `default`. Path values get `~` expansion.

    Back-compat for call sites wanting a bespoke setting not on the typed
    Settings class. Prefer adding a typed field for new settings.
    """
    value = _load_settings().get(key, default)
    if key in _PATH_KEYS:
        return _expand_paths(value)
    return value


def _save_to_file(path: str, updates: dict) -> None:
    """Merge updates into a JSON file."""
    existing = _load_json(path)
    existing.update(updates)
    with open(path, "w") as f:
        json.dump(existing, f, indent=2)


def save_settings(updates: dict, scope: str = "auto") -> None:
    """Merge updates into the appropriate settings file and write back.

    scope: 'shared' → settings.json (git-tracked), 'local' →
    settings.local.json (gitignored), 'auto' → route each key by
    LOCAL_SETTINGS_KEYS.

    The process-wide `settings` singleton is refreshed via
    `reload_settings()` so long-running web processes pick up UI edits.
    """
    os.makedirs(CONFIG_DIR, exist_ok=True)

    if scope == "auto":
        shared_updates = {k: v for k, v in updates.items() if k not in LOCAL_SETTINGS_KEYS}
        local_updates = {k: v for k, v in updates.items() if k in LOCAL_SETTINGS_KEYS}
        if shared_updates:
            _save_to_file(SETTINGS_PATH, shared_updates)
        if local_updates:
            _save_to_file(SETTINGS_LOCAL_PATH, local_updates)
    elif scope == "local":
        _save_to_file(SETTINGS_LOCAL_PATH, updates)
    else:
        _save_to_file(SETTINGS_PATH, updates)

    try:
        reload_settings()
    except Exception:
        pass


def get_current_values() -> list[dict]:
    """Return all settings with current values and metadata (for the UI)."""
    shared = _load_json(SETTINGS_PATH)
    local = _load_json(SETTINGS_LOCAL_PATH)
    merged = {**shared, **local}
    result = []
    for key, default, description in SETTINGS_SCHEMA:
        value = merged.get(key, default)
        is_local = key in LOCAL_SETTINGS_KEYS
        result.append({
            "key": key,
            "default": default,
            "value": value,
            "description": description,
            "is_list": isinstance(default, list),
            "is_bool": isinstance(default, bool),
            "overridden": key in merged,
            "scope": "local" if is_local else "shared",
        })
    return result


__all__ = [
    "ProviderPathOverrides",
    "RuleEngineConfig",
    "RuleTriggerThresholds",
    "Settings",
    "TopicProposalExternalAgent",
    "settings",
    "reload_settings",
    # Config-file paths + settings.json CRUD (relocated from lib/config.py).
    "CONFIG_DIR", "SETTINGS_PATH", "SETTINGS_LOCAL_PATH",
    "LOCAL_SETTINGS_KEYS", "SETTINGS_SCHEMA",
    "save_settings", "get_current_values",
    "_load_settings", "_get",
]
