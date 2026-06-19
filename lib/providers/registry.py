"""Provider registry + active-provider resolver."""

from __future__ import annotations

import os
from dataclasses import asdict

from lib.providers.base import AgentProvider
from lib.providers.claude import ClaudeProvider
from lib.providers.codex import CodexProvider
from lib.providers.generic import GenericProvider
from lib.providers.kimi import KimiProvider
from lib import settings as settings_mod


# Re-export provider config classes so callers can reference them without
# importing from lib.settings directly.
from lib.settings import ProviderConfig, ProviderPathOverrides


_PROVIDER_BUILDERS = {
    "claude": ClaudeProvider,
    "codex": CodexProvider,
    "generic": GenericProvider,
    "kimi": KimiProvider,
}


def _provider_config(provider_id: str) -> ProviderConfig | None:
    """Return the persisted ProviderConfig for a provider, if any."""
    entry = settings_mod.settings.providers.get(provider_id)
    if entry is None:
        return None
    if isinstance(entry, ProviderConfig):
        return entry
    if isinstance(entry, dict):
        return ProviderConfig(**entry)
    return None


def _provider_overrides(provider_id: str) -> dict:
    """Path overrides only (kept for provider constructor compatibility).

    Selects exactly the path-override fields rather than stripping known
    behavioral keys, so a new non-path field on ProviderConfig can't leak
    into a provider constructor.
    """
    cfg = _provider_config(provider_id)
    if cfg is None:
        return {}
    return {
        field: value
        for field in ProviderPathOverrides.model_fields
        if (value := getattr(cfg, field)) is not None
    }


def list_provider_ids() -> list[str]:
    return sorted(_PROVIDER_BUILDERS.keys())


def list_visible_provider_ids() -> list[str]:
    """Provider IDs that UI surfaces should expose.

    Returns the full set when `settings.experimental_providers` is true.
    Otherwise returns just `claude` plus the active provider (so a user who
    has switched the active provider to codex/generic via settings can still
    manage it without flipping the experimental flag).
    """
    if settings_mod.settings.experimental_providers:
        return list_provider_ids()
    visible = {"claude", active_provider_id()}
    return sorted(visible & _PROVIDER_BUILDERS.keys())


def is_provider_id(provider_id: str | None) -> bool:
    return (provider_id or "").strip().lower() in _PROVIDER_BUILDERS


def build_provider(provider_id: str) -> AgentProvider:
    pid = (provider_id or "").strip().lower()
    builder = _PROVIDER_BUILDERS.get(pid)
    if builder is None:
        raise ValueError(f"unknown provider: {provider_id}")
    overrides = _provider_overrides(pid)
    if pid == "claude":
        # Keep legacy settings.skills_dir behavior as Claude fallback.
        return builder(overrides, legacy_skills_dir=settings_mod.settings.skills_dir)
    return builder(overrides)


def active_provider_id() -> str:
    return (settings_mod.settings.active_provider or "claude").strip().lower()


def get_active_provider() -> AgentProvider:
    return build_provider(active_provider_id())


def provider_handler_config(provider_id: str) -> dict:
    """Return the handler overrides configured for a provider.

    Shape matches hook_manager/config.py expectations:
      { "disabled_handlers": [...], "priority_overrides": {...} }
    """
    cfg = _provider_config(provider_id)
    if cfg is None:
        return {"disabled_handlers": [], "priority_overrides": {}}
    return {
        "disabled_handlers": list(cfg.disabled_handlers or []),
        "priority_overrides": dict(cfg.priority_overrides or {}),
    }


def enabled_provider_ids() -> list[str]:
    """All provider IDs that should participate in multi-provider ops.

    Always includes the active provider. Other providers are included when
    their settings entry has ``enabled: true``.
    """
    active = active_provider_id()
    enabled = {active}
    for pid, cfg in settings_mod.settings.providers.items():
        if not is_provider_id(pid):
            continue
        if isinstance(cfg, dict):
            if cfg.get("enabled"):
                enabled.add(pid)
        elif getattr(cfg, "enabled", False):
            enabled.add(pid)
    # Preserve stable registry order.
    return [pid for pid in list_provider_ids() if pid in enabled]


def get_enabled_providers() -> list[AgentProvider]:
    """Build all enabled providers."""
    return [build_provider(pid) for pid in enabled_provider_ids()]


def _provider_id_from_tag(payload: dict | None) -> str | None:
    """Provider id from an explicit agent_type/provider_id tag, if registered."""
    raw = (payload or {}).get("agent_type") or (payload or {}).get("provider_id")
    if isinstance(raw, str) and raw.strip():
        pid = raw.strip().lower()
        if pid in _PROVIDER_BUILDERS:
            return pid
    return None


def provider_id_from_model(model: object) -> str | None:
    """Best-effort provider id from a model identifier (fallback only).

    The single source of truth for model→provider inference: the
    SessionStart agent-type fallback and the payload resolver both route
    here so the per-vendor prefix table lives in one place instead of being
    copied (and drifting) across handlers.
    """
    if not isinstance(model, str):
        return None
    m = model.strip().lower()
    if m.startswith("claude-"):
        return "claude"
    if "kimi" in m:
        return "kimi"
    if (
        m.startswith("gpt-")
        or m.startswith("o1")
        or m.startswith("o3")
        or m.startswith("o4")
        or m.startswith("o5")
    ):
        return "codex"
    return None


def canonical_agent_kind(agent_type: object) -> str | None:
    """Map a stored `sessions.agent_type` string to a canonical provider id
    for UI grouping ('claude' | 'codex' | 'kimi' | 'generic' | None).

    `agent_type` is free-form vendor text persisted at SessionStart (it can
    read 'claude', 'openai', 'kimi', 'workflow-subagent', …). Centralized
    here so UI surfaces don't re-implement the vendor→kind mapping with their
    own substring chains. Returns None for an empty/unknown-empty value and
    'generic' for any non-empty value that matches no known vendor."""
    raw = str(agent_type or "").strip().lower()
    if not raw:
        return None
    # Reuse the model→provider vendor table (claude-/gpt-/o-series/kimi) so the
    # prefix knowledge isn't copied here and can't drift from it.
    by_model = provider_id_from_model(raw)
    if by_model:
        return by_model
    # Bare agent_type aliases the model sniffer doesn't carry: 'claude'/'codex'
    # have no model-prefix, 'openai' is Codex's vendor word.
    if "claude" in raw:
        return "claude"
    if "codex" in raw or "openai" in raw:
        return "codex"
    return "generic"


def resolve_provider(payload: dict | None = None) -> AgentProvider:
    """Return the provider implied by the payload, or the global active provider.

    Resolution order:
    1. payload['agent_type'] or payload['provider_id'] (validated against registry)
    2. payload['model'] sniff ('claude-*' → claude, contains 'kimi' → kimi)
    3. settings.active_provider
    4. 'generic' on any failure
    """
    try:
        pid = _provider_id_from_tag(payload) or provider_id_from_model((payload or {}).get("model"))
        return build_provider(pid or active_provider_id())
    except Exception:
        return build_provider("generic")


def _collapse_home(abs_path: str) -> str:
    """Collapse a leading $HOME to ``~`` for display paths."""
    home = os.path.expanduser('~')
    if abs_path == home or abs_path.startswith(home + os.sep):
        return '~' + abs_path[len(home):]
    return abs_path


def provider_skill_paths(provider: AgentProvider) -> dict:
    """Human-friendly skill-path metadata for one provider.

    Used by web blueprints so the Vue UI can render provider-specific
    deployment labels (e.g. `~/.kimi-code/skills` for Kimi) instead of
    hard-coding Claude paths.
    """
    return {
        'id': provider.provider_id,
        'name': provider.display_name,
        'global_dir': _collapse_home(str(provider.global_skills_dir())),
        'project_subpath': '/'.join(provider.project_skills_subpath()),
    }


def active_provider_skill_paths() -> dict:
    """Skill-path metadata for the active provider."""
    return provider_skill_paths(get_active_provider())


def enabled_provider_skill_paths() -> list[dict]:
    """Skill-path metadata for every enabled provider."""
    return [provider_skill_paths(p) for p in get_enabled_providers()]


def provider_capability_rows(*, include_experimental: bool | None = None) -> list[dict]:
    """Provider rows for UI surfaces.

    By default honours `settings.experimental_providers`; pass
    `include_experimental=True` to force all providers (used by `doctor`,
    which is a diagnostic tool and should not hide anything).
    """
    if include_experimental is None:
        ids = list_visible_provider_ids()
    else:
        ids = list_provider_ids() if include_experimental else list_visible_provider_ids()
    rows = []
    active = active_provider_id()
    for pid in ids:
        p = build_provider(pid)
        rows.append({
            "id": p.provider_id,
            "name": p.display_name,
            "active": p.provider_id == active,
            "capabilities": asdict(p.capabilities),
        })
    return rows
