"""The writer and reader of `agent_task_links`.

Every link write lands here for the same reason `store.py` owns `agent_tasks`:
the idempotency rule (re-attaching an issue updates the row the UNIQUE
constraint already holds), the JSON encoding of the snapshot, and the decision
that a failed fetch is recorded rather than raised all have to hold whichever
caller — HTTP route, CLI, a future webhook — did the attach.

The snapshot is refreshed on an *event*: the attach, an agent reporting its
run finished (`resync_task`), or an operator asking. Never on a timer and
never on read, so an open task detail page cannot turn into a poll against
someone else's API, and a snapshot's age is something the UI states
(`synced_at`) rather than something it hides.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from lib.activity_log import get_activity_logger
from lib.defects import stage as stage_module
from lib.orm import SessionLocal
from lib.orm.models.tasks import AgentTaskLink
from lib.tasks import issue_cache
from lib.tasks.integrations import IntegrationError, registry

log = get_activity_logger("tasks")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def serialize(link: AgentTaskLink) -> dict:
    """One link as the API renders it — the cached issue is nested under
    `issue`, so the client reads the same canonical shape a provider returned
    and never a per-vendor field.

    Issue and freshness both come from the feature-keyed cache. Rendering a
    per-link timestamp beside a cache-sourced stage would put the
    two-surfaces-disagree failure in the very field that claims to date it.

    `stage` is computed here rather than stored: its open-defect count changes
    independently of any sync, so a stored copy would go stale the moment a
    defect opened or closed while the snapshot stayed valid.
    """
    issue, synced_at = issue_cache.lookup(link.provider, link.external_key)
    stage = stage_module.stage_from_snapshot(issue)
    return {
        "id": link.id, "task_id": link.task_id, "provider": link.provider,
        "external_key": link.external_key, "url": link.url,
        "issue": issue, "stage": stage.to_dict() if stage else None,
        "synced_at": synced_at,
        # A registry lookup, not a comparison against a provider id: the card
        # renders `live` instead of `synced …` and drops its Refresh, and a
        # second local tracker inherits both without this file re-opening.
        "stage_live": registry.stage_is_live(link.provider),
        "sync_error": link.sync_error, "created_at": link.created_at,
    }


def list_links(task_id: int) -> list[dict]:
    with SessionLocal() as session:
        rows = session.exec(
            select(AgentTaskLink)
            .where(AgentTaskLink.task_id == task_id)
            .order_by(AgentTaskLink.id)).all()
        out = [serialize(row) for row in rows]
    log.read("task_links_listed", task_id=task_id, count=len(out))
    return out


def attach(task_id: int, text: str,
           provider_id: str = "") -> Optional[dict]:
    """Link `task_id` to the issue `text` names, then snapshot it.

    Raises `IntegrationError` when the reference cannot be resolved, and when
    the link would give one task a second feature key — see `_refuse_second_key`.
    A resolvable reference whose *fetch* fails still produces a link, carrying
    the reason: the operator asserted the relationship, and a tracker being
    down does not make that assertion wrong.

    None when the link was detached while the fetch was in flight — the round
    trip happens outside any session, so a concurrent DELETE is a real window,
    and the caller reports a vanished link rather than 500ing on it.
    """
    provider, ref = registry.resolve_ref(text, provider_id)
    _refuse_second_key(task_id, ref)
    link_id = _upsert(task_id, ref)
    return _sync(link_id, provider, ref)


def _refuse_native(ref) -> None:
    """A native ref is never attachable.

    A regin task already IS a feature, so linking one to another is exactly
    the migration `feature_key`-as-identity forbids — and there is no shape of
    it that would not leave one of the two keys orphaned. Refused at the act,
    in both `attach` and `preview`, so the composer cannot offer a shape the
    write behind it will reject.
    """
    from lib.tasks.integrations.regin import SCHEME  # noqa: PLC0415

    if ref.provider == SCHEME:
        raise IntegrationError(
            f"{ref.key} is a regin task, which is already a feature — a task "
            "cannot be linked to one. Make it a run of that feature instead, "
            "or declare a fresh feature.")


def _refuse_second_key(task_id: int, ref) -> None:
    """Refuse a link that would give one task two feature keys.

    Three doors lead to the same shape and all three refuse: a task that
    already holds a link, a task something already names as parent, and — in
    `grain` — setting a child's parent onto a linked task. `feature_key` is
    identity, so the second key is not a merge to resolve later; it is a row
    that makes both keys' ledgers wrong from the moment it is written.
    """
    _refuse_native(ref)
    from lib.orm.models.tasks import AgentTask  # noqa: PLC0415

    with SessionLocal() as session:
        held = [row.external_key for row in session.exec(
            select(AgentTaskLink)
            .where(AgentTaskLink.task_id == task_id)).all()
            if row.external_key != ref.key]
        children = session.exec(
            select(AgentTask.id)
            .where(AgentTask.parent_id == task_id)).first()
    if held:
        raise IntegrationError(
            f"task {task_id} already links {held[0]} — one task cannot hold "
            "two feature keys, and a feature is never moved between trackers. "
            "Detach that link first, or declare a fresh feature.")
    if children is not None:
        raise IntegrationError(
            f"task {task_id} is already a feature with runs of its own — one "
            "task cannot hold two feature keys. Declare a fresh feature "
            "instead of re-homing this one.")


def preview(text: str, provider_id: str = "") -> dict:
    """Read the issue `text` names without writing anything.

    The composer needs an issue's title before the task that would hold the
    link exists, and a dialog the operator closes must leave nothing behind —
    so this resolves and fetches, and never touches `agent_task_links`.

    A fetch failure raises rather than being recorded: `attach` can keep a
    failed sync because the link row *is* the operator's assertion, but here
    there is no assertion yet and no row to carry the reason.
    """
    provider, ref = registry.resolve_ref(text, provider_id)
    _refuse_native(ref)
    issue = provider.fetch_issue(ref)
    log.read("task_link_previewed", provider=ref.provider, external_key=ref.key)
    return {"provider": ref.provider, "key": ref.key,
            "url": issue.url or ref.url, "issue": issue.to_dict()}


def refresh(task_id: int, link_id: int) -> Optional[dict]:
    """Re-fetch one link's snapshot. None when the link is not this task's."""
    with SessionLocal() as session:
        link = session.get(AgentTaskLink, link_id)
        if link is None or link.task_id != task_id:
            return None
        provider_id, key, url = link.provider, link.external_key, link.url
    provider = registry.build_provider(provider_id)
    if provider is None:
        raise registry.UnknownRef(f"no tracker named {provider_id!r}")
    from lib.tasks.integrations.base import ExternalRef
    return _sync(link_id, provider,
                 ExternalRef(provider=provider_id, key=key, url=url))


def resync_task(task_id: int) -> list[dict]:
    """Re-fetch every link this task holds, and never raise.

    The event is an agent reporting its own work finished. A linked run is
    launched told to move the issue's state to match what it did, so that
    report is the moment regin's snapshot is known stale — and the operator
    who opens the task next should not have to press Refresh to be told what
    the run already recorded.

    A failure here is swallowed rather than propagated: the report settles the
    attempt and releases whatever waited on it, and a tracker being
    unreachable cannot be allowed to walk that back. The reason is already on
    the link (`sync_error`), which is what the card states.
    """
    out = []
    for row in list_links(task_id):
        try:
            link = refresh(task_id, row["id"])
        except Exception:  # noqa: BLE001 — one link cannot sink the finish
            log.error("task_link_resync_failed", task_id=task_id,
                      link_id=row["id"], exc_info=True)
            continue
        if link is not None:
            out.append(link)
    if out:
        log.write("task_links_resynced", task_id=task_id, count=len(out))
    return out


def detach(task_id: int, link_id: int) -> bool:
    with SessionLocal() as session:
        link = session.get(AgentTaskLink, link_id)
        if link is None or link.task_id != task_id:
            return False
        session.delete(link)
        session.commit()
    log.write("task_link_detached", task_id=task_id, link_id=link_id)
    return True


def _upsert(task_id: int, ref) -> int:
    """The link row's id, created or already present.

    The UNIQUE constraint resolves the race rather than a read-then-write
    check: two attaches of the same issue both try the insert, one loses, and
    the loser reads back the winner's row.
    """
    with SessionLocal() as session:
        row = AgentTaskLink(task_id=task_id, provider=ref.provider,
                            external_key=ref.key, url=ref.url,
                            created_at=_now())
        session.add(row)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            existing = session.exec(
                select(AgentTaskLink).where(
                    AgentTaskLink.task_id == task_id,
                    AgentTaskLink.provider == ref.provider,
                    AgentTaskLink.external_key == ref.key)).first()
            if existing is None:
                raise
            # A row that has never synced holds no canonical URL — only
            # whatever the first attach could infer. So a replay carrying a
            # different one (a bare key first, the workspace URL second) is
            # the better assertion and wins. A synced row keeps the canonical
            # URL the tracker itself returned.
            never_synced = issue_cache.lookup(ref.provider, ref.key)[1] is None
            if never_synced and existing.url != ref.url:
                existing.url = ref.url
                session.add(existing)
                session.commit()
            log.read("task_link_replayed", task_id=task_id,
                     provider=ref.provider, external_key=ref.key)
            return existing.id
        log.write("task_link_attached", task_id=task_id,
                  provider=ref.provider, external_key=ref.key)
        return row.id


def _sync(link_id: int, provider, ref) -> Optional[dict]:
    """Fetch `ref` and write the result: the issue to the cache, the outcome
    to the link."""
    fetched = None
    error: Optional[str] = None
    url = ref.url
    try:
        fetched = provider.fetch_issue(ref)
        url = fetched.url or ref.url
    except IntegrationError as exc:
        error = str(exc)
        log.error("task_link_sync_failed", link_id=link_id,
                  provider=provider.provider_id, retryable=exc.retryable)
    if fetched is not None:
        issue_cache.remember(ref.provider, ref.key, fetched)
    with SessionLocal() as session:
        link = session.get(AgentTaskLink, link_id)
        if link is None:
            log.read("task_link_vanished_mid_sync", link_id=link_id)
            return None
        link.sync_error = error
        if fetched is not None:
            link.url = url
        session.add(link)
        session.commit()
        out = serialize(link)
    if error is None:
        log.write("task_link_synced", link_id=link_id,
                  provider=provider.provider_id)
    return out


def feature_key_for_task(task_id: int,
                         parent_id: Optional[int] = None) -> str:
    """The one feature key a task's work counts under.

    Two tables decide it, which is why it is a function and not
    `'regin:' || id`. A child is an attempt at its parent's feature and holds
    no key of its own, so the question is always asked of the root. A root
    holding a tracker link is a *run of* that issue — the issue is the
    feature, and `_refuse_second_key` is what makes "the link's key, else the
    native one" a total rule rather than a preference between two answers.
    """
    from lib.tasks.integrations.regin import key_for_task  # noqa: PLC0415

    root = parent_id or task_id
    with SessionLocal() as session:
        linked = session.exec(
            select(AgentTaskLink.external_key)
            .where(AgentTaskLink.task_id == root)
            .order_by(AgentTaskLink.id)).first()
    return linked or key_for_task(root)
