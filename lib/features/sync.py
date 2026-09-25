"""Pulling a remote feature's state, and folding it into the log (regin:82).

Only a feature with an `origin` that has a tracker behind it reaches here. A
native one has nothing to sync: its stage is derived from events written in
this database, which is why the population no longer contains 31 features whose
stage read `null` because nobody had fetched them.

**A refresh is an event, never a poll.** It runs on an operator asking, and on
nothing else — not a timer, not a page mount, not a read. That rule came from
`lib/tasks/links.py` and survives the table it was written for.

**A fetch that learns nothing writes nothing.** `events.on_sync` appends only
when the mapped kind differs from the last event's, so pressing Refresh twice
leaves one row and a real transition always leaves one. The test is an
identifier comparison, not a time window.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from lib.activity_log import get_activity_logger
from lib.features import events as events_module
from lib.features import store as feature_store
from lib.orm import SessionLocal
from lib.orm.models.features import ORIGIN_NATIVE, Feature
from lib.tasks.integrations import IntegrationError, registry

log = get_activity_logger("defects")


class NotRemote(ValueError):
    """A native feature was asked to sync. It has no remote to ask."""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def is_remote(feature: Feature) -> bool:
    return feature.origin != ORIGIN_NATIVE and bool(feature.external_ref)


def refresh(feature_id: int) -> Optional[str]:
    """Fetch the remote issue and fold its state in. The error, or None.

    A failure records the reason on the feature and leaves the log untouched,
    so the caller can state that the stage beside it is unchanged — and every
    other figure on the page was local anyway.
    """
    feature = feature_store.require(feature_id)
    if not is_remote(feature):
        raise NotRemote(
            f"feature {feature_id} is native — its stage is derived here, so "
            "there is nothing to fetch")
    try:
        provider, ref = registry.resolve_ref(feature.external_ref,
                                             feature.origin)
        issue = provider.fetch_issue(ref)
    except IntegrationError as exc:
        _record(feature_id, sync_error=str(exc))
        log.error("feature_sync_failed", feature_id=feature_id,
                  reason=str(exc))
        return str(exc)
    _record(feature_id, title=issue.title, external_url=issue.url,
            body=issue.description, synced_at=_now(), sync_error=None)
    events_module.on_sync(feature_id, issue.state_category,
                          provider=feature.origin)
    log.write("feature_synced", feature_id=feature_id,
              state_category=issue.state_category)
    return None


def _record(feature_id: int, **fields) -> None:
    """Write what the fetch learned. `sync_error` is set on every path.

    Including the successful one, where it is cleared: a reason left standing
    after a fetch that worked is a surface telling an operator the remote is
    down while showing them what it just said.
    """
    with SessionLocal() as session:
        row = session.get(Feature, feature_id)
        if row is None:
            return
        for name, value in fields.items():
            if value is not None or name == "sync_error":
                setattr(row, name, value)
        session.add(row)
        session.commit()


def adopt(text_ref: str, *, provider_id: str = "",
          project_id: Optional[int] = None) -> Feature:
    """Create a feature from a reference an operator pasted.

    The title comes from the fetch rather than from the operator: they are
    naming an issue that already has one, and a second name for it is two
    writers on one field. A fetch that fails still produces the feature — the
    operator's assertion about where this is tracked does not depend on the
    remote being up — carrying the reason, with a title of the raw reference.

    `project_id` files a NEW row only. Re-adopting an issue already here
    returns it as it stands: its project was declared once, and a second
    adopt is not an edit.
    """
    feature_store.check_project(project_id)
    provider, ref = registry.resolve_ref(text_ref, provider_id)
    existing = feature_store.by_external(ref.provider, ref.key)
    if existing is not None:
        return existing
    try:
        issue = provider.fetch_issue(ref)
    except IntegrationError as exc:
        return feature_store.create(ref.key, origin=ref.provider,
                                    external_ref=ref.key,
                                    external_url=ref.url,
                                    sync_error=str(exc),
                                    project_id=project_id)
    feature = feature_store.create(issue.title or ref.key,
                                   body=issue.description,
                                   origin=ref.provider, external_ref=ref.key,
                                   external_url=issue.url or ref.url,
                                   project_id=project_id)
    _record(feature.id, synced_at=_now(), sync_error=None)
    events_module.on_sync(feature.id, issue.state_category,
                          provider=ref.provider)
    return feature_store.require(feature.id)
