"""Agent provider adapters."""

from lib.providers.base import AgentProvider, ProviderCapabilities
from lib.providers.registry import (
    ProviderConfig,
    ProviderPathOverrides,
    active_provider_id,
    active_provider_skill_paths,
    build_provider,
    canonical_agent_kind,
    enabled_provider_ids,
    enabled_provider_skill_paths,
    get_active_provider,
    get_enabled_providers,
    is_provider_id,
    list_provider_ids,
    list_visible_provider_ids,
    provider_capability_rows,
    provider_handler_config,
    provider_id_from_model,
    resolve_provider,
)

__all__ = [
    "AgentProvider",
    "ProviderCapabilities",
    "ProviderConfig",
    "ProviderPathOverrides",
    "active_provider_id",
    "active_provider_skill_paths",
    "build_provider",
    "canonical_agent_kind",
    "enabled_provider_ids",
    "enabled_provider_skill_paths",
    "get_active_provider",
    "get_enabled_providers",
    "is_provider_id",
    "list_provider_ids",
    "list_visible_provider_ids",
    "provider_capability_rows",
    "provider_handler_config",
    "provider_id_from_model",
    "resolve_provider",
]
