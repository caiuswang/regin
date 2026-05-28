"""Provider registry + active-provider resolver."""

from __future__ import annotations

from dataclasses import asdict

from lib.providers.base import AgentProvider
from lib.providers.claude import ClaudeProvider
from lib.providers.codex import CodexProvider
from lib.providers.generic import GenericProvider
from lib import settings as settings_mod


_PROVIDER_BUILDERS = {
    "claude": ClaudeProvider,
    "codex": CodexProvider,
    "generic": GenericProvider,
}


def _provider_overrides(provider_id: str) -> dict:
    entry = settings_mod.settings.providers.get(provider_id)
    if entry is None:
        return {}
    if hasattr(entry, "model_dump"):
        return {k: v for k, v in entry.model_dump().items() if v is not None}
    if isinstance(entry, dict):
        return {k: v for k, v in entry.items() if v is not None}
    return {}


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


def resolve_provider(payload: dict | None = None) -> AgentProvider:
    """Return the provider implied by the payload, or the global active provider.

    Resolution order:
    1. payload['agent_type'] or payload['provider_id'] (validated against registry)
    2. payload['model'] starting with 'claude-' → claude
    3. settings.active_provider
    4. 'generic' on any failure
    """
    try:
        raw = (payload or {}).get("agent_type") or (payload or {}).get("provider_id")
        if isinstance(raw, str) and raw.strip():
            pid = raw.strip().lower()
            if pid in _PROVIDER_BUILDERS:
                return build_provider(pid)

        model = (payload or {}).get("model")
        if isinstance(model, str) and model.startswith("claude-"):
            return build_provider("claude")

        return build_provider(active_provider_id())
    except Exception:
        return build_provider("generic")


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
