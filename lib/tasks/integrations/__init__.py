"""Third-party issue trackers linked to regin tasks (see `base.py`)."""

from lib.tasks.integrations.base import (
    ExternalIssue, ExternalRef, IntegrationError, IssueTrackerProvider,
    NotConfigured, STATE_CATEGORIES,
)
from lib.tasks.integrations.registry import (
    AmbiguousRef, UnknownRef, build_provider, configured_providers,
    provider_ids, provider_rows, register_provider, resolve_ref,
)

__all__ = [
    "AmbiguousRef", "ExternalIssue", "ExternalRef", "IntegrationError",
    "IssueTrackerProvider", "NotConfigured", "STATE_CATEGORIES", "UnknownRef",
    "build_provider", "configured_providers", "provider_ids", "provider_rows",
    "register_provider", "resolve_ref",
]
