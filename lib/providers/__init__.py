"""Agent provider adapters."""

from lib.providers.base import AgentProvider, ProviderCapabilities
from lib.providers.registry import (
    active_provider_id,
    build_provider,
    get_active_provider,
    is_provider_id,
    list_provider_ids,
    list_visible_provider_ids,
    provider_capability_rows,
    resolve_provider,
)

__all__ = [
    "AgentProvider",
    "ProviderCapabilities",
    "active_provider_id",
    "build_provider",
    "get_active_provider",
    "is_provider_id",
    "list_provider_ids",
    "list_visible_provider_ids",
    "provider_capability_rows",
    "resolve_provider",
]
