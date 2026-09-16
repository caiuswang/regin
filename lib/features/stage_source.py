"""Where a *web* read gets a feature's stage (CAI-245).

The live resolver stays the ledger's default, because `regin feature ledger`
deciding an escape from a stale snapshot would rewrite the numbers in its own
report. The web gets this one instead: it reads `issue_cache` and never opens
a socket.

That split is expressed as a resolver passed into `ledger.build()` rather than
as a convention, so the rule — *the ledger endpoint never calls `fetch_issue`*
— is a test name and not a comment somebody can drift away from.

Refreshing is a second event over this same write path. It is not a read that
happens to be fresh: a fetch a page fires on mount is still a read, and a
reloaded page is the poll the task-link cache already refused.
"""

from __future__ import annotations

from typing import Optional

from lib.defects import stage as stage_module
from lib.tasks import issue_cache

def _providers(feature_key: str) -> list:
    """Every provider holding a cached issue under this key.

    Empty is a real answer — nobody has fetched it — and naming a fallback
    provider here was this module's one violation of the rule that no caller
    outside a provider module branches on `provider_id`. Deleting it is
    behaviour-preserving: the fallback fired only when nothing was cached, in
    which case the lookup under it found nothing either.
    """
    return issue_cache.providers_for(feature_key)


def snapshot(feature_key: str) -> Optional[dict]:
    """The cached issue for a key, or None when nobody has fetched it."""
    for provider in _providers(feature_key):
        found, _ = issue_cache.lookup(provider, feature_key)
        if found is not None:
            return found
    return None


def synced_at(feature_key: str) -> Optional[str]:
    """When that issue was last fetched.

    Rides *beside* the stage and never inside it: `Stage.to_dict()` is flat
    and stays flat, and a null stage can carry no field at all — which is also
    why freshness and stage are never orthogonal. Only a successful fetch
    writes this, so a key with no stage has no "as of T" either.
    """
    for provider in _providers(feature_key):
        found, when = issue_cache.lookup(provider, feature_key)
        if found is not None:
            return when
    return None


def is_live(feature_key: str) -> bool:
    """Whether this key's tracker can lag the fact.

    Resolved through the registry's declared capability rather than by looking
    at the key's shape: the shape is a provider's private business, and a
    caller reading it would be the `provider_id` branch the seam forbids.

    A key nothing claims is not live — the honest answer, since an unresolvable
    key has no tracker to be live about.

    Asks which tracker *names* the key (`registry.name_ref`), never which one
    can answer for it. Two reasons: this is a pointer question rather than a
    read, so demanding a live credential would make a Linear key's liveness
    depend on whether Linear happens to be configured; and the ledger endpoint
    must not touch a fetch path at all, which is a rule a test names.
    """
    from lib.tasks.integrations import IntegrationError  # noqa: PLC0415
    from lib.tasks.integrations import registry  # noqa: PLC0415

    try:
        ref = registry.name_ref(feature_key)
    except IntegrationError:
        return False
    return registry.stage_is_live(ref.provider)


def from_cache(feature_key: str):
    """The stage a web read reports — local only.

    None, never `spec`, when the cache is empty: `spec` is a real stage and
    must not double as "nobody has looked", and `unknown` is never a stage
    name because it is a statement about us rather than about the feature.
    """
    return stage_module.stage_from_snapshot(snapshot(feature_key))


def refresh(feature_key: str) -> Optional[dict]:
    """Fetch the issue and write it to the shared cache. None on failure.

    A failure persists nothing — not the attempt, not a timestamp — which is
    what keeps freshness at two states. The caller reports the failure inline
    beside a stage that is explicitly unchanged.
    """
    from lib.defects import tracker  # noqa: PLC0415  (circular at import time)
    from lib.tasks.integrations import IntegrationError  # noqa: PLC0415
    try:
        provider, ref = tracker.resolve(feature_key)
        issue = provider.fetch_issue(ref)
    except (tracker.TrackerUnavailable, IntegrationError):
        return None
    issue_cache.remember(ref.provider, ref.key, issue)
    return issue.to_dict()
