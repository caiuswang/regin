"""regin answering about its own tasks — the tracker with no remote.

Linear's free tier caps issue count, and reaching it stops regin being able to
*declare* a feature at all. This provider removes that dependency: a top-level
`agent_tasks` row is a feature, addressed as `regin:<id>`, and everything the
ledger asks a tracker is answered from local rows.

It is the same `IssueTrackerProvider` contract Linear implements, with two
consequences worth stating because they are what the rest of the codebase is
allowed to rely on:

* **There is no credential**, so `is_configured()` is unconditionally `True`
  and there is no settings field. A switch able to turn this off would let an
  operator break the rule that a feature key always resolves.
* **There is no network**, so the two remote failure modes are unreachable.
  `regin:999` is *no such task*, never *the tracker did not answer*, and
  `retryable` is never `True` on this provider.

`KEY_SQL` and `key_for_task` are exported so the ref format lives in exactly
one file in both languages — `lib/features` builds the same string in SQL that
this module builds in Python, and a format that drifted between the two would
put a feature in the population under a key nothing could resolve.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from lib.defects import rounds
from lib.orm import AuthSessionLocal, SessionLocal
from lib.orm.models.sync import Repo
from lib.orm.models.tasks import (
    AgentTask, TASK_STATUS_ARCHIVED, TASK_STATUS_ARMED, TASK_STATUS_DONE,
    TASK_STATUS_FAILED, TASK_STATUS_IDEA, TASK_STATUS_QUEUED,
    TASK_STATUS_RUNNING,
)
from lib.orm.models.users import User
from lib.tasks.integrations.base import (
    ExternalIssue, ExternalRef, IntegrationError, IssueTrackerProvider,
    STATE_BACKLOG, STATE_CANCELED, STATE_COMPLETED, STATE_STARTED,
    STATE_UNSTARTED, clamp_description,
)

#: `agent_tasks.source` on a row `create_issue` filed, so a task that exists
#: because a defect was pointed at it says so in the same column every other
#: capture surface signs.
SOURCE_DEFECT = "defect"

#: The scheme. Slash-free and lowercase so the key survives a URL path
#: segment, a shell argument and a branch name without being escaped.
SCHEME = "regin"

# `regin:42`, case-insensitively, and nothing else on the line. A colon is
# what makes this disjoint from Linear's `ENG-42` *by construction*: that
# pattern is a `fullmatch`, and a colon defeats it — so `AmbiguousRef` between
# the two is unreachable rather than merely unlikely.
_REF_RE = re.compile(rf"^{SCHEME}:(\d+)$", re.IGNORECASE)
# A pasted `/tasks/42` URL, host ignored. Host-matching was rejected because
# regin has no authoritative self-address to match against; the hazard — a
# colleague's URL linking the local row 42 — is the same one mistyping the
# number already carries, and the fetched title exposes it at once.
_URL_RE = re.compile(r"^https?://[^/]+/tasks/(\d+)/?(?:[?#].*)?$",
                     re.IGNORECASE)

#: The key expression in SQL, for the readers that enumerate the population
#: without loading rows. The Python twin is `key_for_task`, and the two live
#: in one file so the format cannot drift between the languages — a key built
#: one way in SQL and another in Python would put a feature in the population
#: under an identifier nothing could resolve.
KEY_SQL = f"('{SCHEME}:' || agent_tasks.id)"


def key_sql(column: str = "agent_tasks.id") -> str:
    """`KEY_SQL` over an arbitrary id column, for a query that aliases the
    table or reads the id out of `parent_id`."""
    return f"('{SCHEME}:' || {column})"

# regin's own lifecycle, in the canonical vocabulary. `idea` is `backlog`
# because arming is the commitment and an idea is not committed; `failed` is
# `started` because a feature whose every child failed is being built, not
# specified-not-started; `archived` is `canceled` rather than `unknown`, which
# is reserved for a vocabulary we cannot map and this is one we know exactly.
#
# `done` maps to `started`, NOT to `completed`. That looks wrong and is the
# whole point: `status` is the *task lifecycle*, and `complete_run` writes
# `done` onto it when a run settles — which says the work finished, never that
# the feature shipped. `completed` is reached only through `claimed_at`, below.
_STATE_CATEGORY = {
    TASK_STATUS_IDEA: STATE_BACKLOG,
    TASK_STATUS_ARMED: STATE_UNSTARTED,
    TASK_STATUS_QUEUED: STATE_UNSTARTED,
    TASK_STATUS_RUNNING: STATE_STARTED,
    TASK_STATUS_DONE: STATE_STARTED,
    TASK_STATUS_FAILED: STATE_STARTED,
    TASK_STATUS_ARCHIVED: STATE_CANCELED,
}


def _state_category(task: AgentTask) -> str:
    """The workflow position, with the claim taking precedence over status.

    A claim outranks everything except `archived`: filing a feature away is a
    decision about the row itself, while a claim is a statement about the work,
    and an archived feature is not one anybody is still calling shipped.
    """
    if task.status == TASK_STATUS_ARCHIVED:
        return STATE_CANCELED
    if task.claimed_at:
        return STATE_COMPLETED
    return _STATE_CATEGORY.get(task.status, STATE_STARTED)


def key_for_task(task_id: int) -> str:
    """The feature key of a task row. The Python twin of `KEY_SQL`."""
    return f"{SCHEME}:{int(task_id)}"


def task_id_for(key: str) -> Optional[int]:
    """The row a native key names, or None when it names none."""
    match = _REF_RE.match((key or "").strip())
    return _canonical_id(match.group(1)) if match else None


def _canonical_id(digits: str) -> Optional[int]:
    """`042` → 42, and `0` → None.

    Row ids start at 1, so `regin:0` names nothing. Refusing it here rather
    than at fetch keeps *what the provider claims* and *what exists* separate
    questions: the first is a format, and a format that admits an impossible
    id would make `regin:0` a tracker error instead of an unrecognized ref.
    """
    value = int(digits)
    return value if value > 0 else None


class ReginProvider(IssueTrackerProvider):
    """regin's own tasks, behind the tracker seam."""

    provider_id = SCHEME
    display_name = "regin"
    ref_hint = "a regin task ref like regin:42, or the /tasks/42 URL"
    #: The cached stage cannot lag the fact, because the fact is in the same
    #: database — so there is nothing to refresh, and the surface says `live`
    #: where it would otherwise say `as of T`.
    stage_is_live = True

    def is_configured(self) -> bool:
        """Always. A local provider holds no credential to be missing."""
        return True

    def parse_ref(self, text: str) -> Optional[ExternalRef]:
        """Claim `regin:<digits>`, or any `/tasks/<digits>` URL.

        A bare number is deliberately not claimed: `42` is every tracker's
        issue id, and claiming it would make the shape ambiguous the day a
        third provider lands — the exact failure Linear's `parse_ref` avoids
        by claiming `ENG-42` only when the text is nothing else.
        """
        raw = (text or "").strip()
        if not raw:
            return None
        match = _REF_RE.match(raw) or _URL_RE.match(raw)
        if not match:
            return None
        task_id = _canonical_id(match.group(1))
        return self._ref(task_id) if task_id is not None else None

    def _ref(self, task_id: int) -> ExternalRef:
        """A *relative* url, always.

        regin has no authoritative self-address, so an absolute one would be a
        guess. The SPA renders a relative url as an in-app route, keyed on the
        url's shape rather than on the provider — which is what keeps the
        renderer provider-agnostic.
        """
        return ExternalRef(provider=self.provider_id,
                           key=key_for_task(task_id),
                           url=f"/tasks/{task_id}")

    def fetch_issue(self, ref: ExternalRef) -> ExternalIssue:
        """The task row, in the canonical shape. Local, always.

        Reads `AgentTask` rather than going through `lib/tasks/store`: the
        serializer is the API's projection, and a provider is the canonical
        one — putting the first inside the second would make the tracker's
        answer depend on what the task feed happens to render.
        """
        task_id = task_id_for(ref.key)
        if task_id is None:
            raise IntegrationError(f"regin does not recognize {ref.key!r}.",
                                   retryable=False)
        with SessionLocal() as session:
            task = session.get(AgentTask, task_id)
            if task is None:
                raise IntegrationError(f"regin has no task {task_id}.",
                                       retryable=False)
            project = (session.get(Repo, task.project_id)
                       if task.project_id else None)
            extra = {"Project": project.name} if project else {}
            return self._to_issue(task, extra)

    def create_issue(self, *, parent: ExternalRef, title: str,
                     body: str) -> ExternalRef:
        """File a run of `parent` and return its ref.

        A child task shares its parent's feature key through
        `runs.py` (`parent_id or id`), so the fix's sessions and cost roll up
        to the feature that was blamed with no change to attribution. The
        grain rules apply inside `capture_task`; a parent that cannot hold a
        child surfaces as the same non-retryable error as a missing one.
        """
        from lib.tasks import grain, store  # noqa: PLC0415  (store imports this module)

        task_id = task_id_for(parent.key)
        if task_id is None:
            raise IntegrationError(f"regin does not recognize {parent.key!r}.",
                                   retryable=False)
        with SessionLocal() as session:
            if session.get(AgentTask, task_id) is None:
                raise IntegrationError(f"regin has no task {task_id}.",
                                       retryable=False)
        try:
            view, _created = store.capture_task(
                title=title, body=body, parent_id=task_id,
                source=SOURCE_DEFECT)
        except grain.GrainRejected as exc:
            raise IntegrationError(str(exc), retryable=False) from exc
        return self._ref(view["id"])

    def _to_issue(self, task: AgentTask, extra: dict) -> ExternalIssue:
        return ExternalIssue(
            provider=self.provider_id,
            key=key_for_task(task.id),
            url=f"/tasks/{task.id}",
            title=task.title,
            state=task.status,
            state_category=_state_category(task),
            priority=task.priority,
            assignee=_display_name(task.assignee),
            labels=_labels(task.tags_json),
            description=clamp_description(task.body),
            updated_at=task.updated_at,
            rounds=rounds.from_task(task),
            extra=extra,
        )


def _display_name(user_id: Optional[int]) -> Optional[str]:
    """The claimer's name, or None when the id resolves to nobody.

    None rather than a placeholder: `assignee` carries no foreign key, so an
    id pointing at a deleted user is a real state, and rendering
    `user #7` would be regin inventing a person.
    """
    if not user_id:
        return None
    with AuthSessionLocal() as session:
        user = session.get(User, user_id)
        return user.display_name if user else None


def _labels(tags_json: Optional[str]) -> tuple[str, ...]:
    if not tags_json:
        return ()
    try:
        parsed = json.loads(tags_json)
    except (json.JSONDecodeError, ValueError):
        return ()
    if not isinstance(parsed, list):
        return ()
    return tuple(str(tag) for tag in parsed if isinstance(tag, str))
