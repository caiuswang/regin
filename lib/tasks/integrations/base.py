"""The seam between regin's tasks and an issue tracker.

The tracker may be another system — Linear — or regin itself: a top-level
`agent_tasks` row is a feature, and `lib/tasks/integrations/regin.py` answers
for it behind this same contract. So "tracker" below never means "somewhere
else"; it means whatever holds the issue.

Where the tracker IS another system, a regin task carries no assignee, no
cycle, no comment thread and no workflow state beyond its own — that detail
lives in whatever project-management system the team already runs, and a task
**links** to the issue and renders a normalized snapshot of it.

Everything vendor-specific is expressed as an `IssueTrackerProvider` subclass,
the same shape `lib/providers/` uses for coding-agent CLIs: identity, a
`parse_ref` that claims the refs it recognizes, and a `fetch_issue` that
returns the *canonical* `ExternalIssue`. No caller outside a provider module
may branch on `provider_id` — that is the property that makes the second
platform an additive change.

The canonical shape is deliberately the intersection every tracker can answer,
not the union: title, url, a normalized state category, a priority expressed in
regin's own words, assignee, labels, description. A field only one vendor has
belongs in `extra`, which the UI renders as opaque key/value rows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# The workflow position of an issue, normalized. Linear names these
# exactly; Jira's status categories and a GitHub issue's open/closed both fold
# into the same six, so the UI colors one vocabulary instead of N.
STATE_TRIAGE = "triage"
STATE_BACKLOG = "backlog"
STATE_UNSTARTED = "unstarted"
STATE_STARTED = "started"
STATE_COMPLETED = "completed"
STATE_CANCELED = "canceled"
STATE_UNKNOWN = "unknown"
STATE_CATEGORIES: tuple[str, ...] = (
    STATE_TRIAGE, STATE_BACKLOG, STATE_UNSTARTED, STATE_STARTED,
    STATE_COMPLETED, STATE_CANCELED, STATE_UNKNOWN,
)

# A spec lives in the body a feature is declared with, and the largest real
# artifact measured is 29,488 characters. The bound therefore has to clear
# 32,000 rather than sit at it, and it **refuses** rather than truncating —
# two live cache rows were silently clipped to exactly 8,000 before this.
DESCRIPTION_MAX = 64_000


class IntegrationError(RuntimeError):
    """A link operation the caller should surface verbatim to the operator.

    `retryable` separates "the remote said no, stop asking" (a bad key, an
    issue that does not exist) from "the remote was unreachable" — the route
    stores the latter as a sync error against a link that still exists,
    because the link is a user's assertion and a network blip must not undo it.
    """

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class NotConfigured(IntegrationError):
    """The provider has no credential, so it cannot answer anything."""


@dataclass(frozen=True)
class ExternalRef:
    """A provider's claim on one identifier: which issue, and where it lives.

    `key` is the human identifier the tracker itself prints (`ENG-42`), not an
    opaque uuid, because it is what an operator recognizes in the UI and what
    they paste back. It is also the dedup key, so a provider must return the
    same `key` for a full URL and for the bare id.
    """

    provider: str
    key: str
    url: str


@dataclass(frozen=True)
class ExternalIssue:
    """One issue, in regin's vocabulary — whichever tracker holds it."""

    provider: str
    key: str
    url: str
    title: str
    state: str = ""
    state_category: str = STATE_UNKNOWN
    priority: Optional[str] = None
    assignee: Optional[str] = None
    labels: tuple[str, ...] = ()
    description: Optional[str] = None
    updated_at: Optional[str] = None
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "provider": self.provider, "key": self.key, "url": self.url,
            "title": self.title, "state": self.state,
            "state_category": self.state_category, "priority": self.priority,
            "assignee": self.assignee, "labels": list(self.labels),
            "description": self.description, "updated_at": self.updated_at,
            "extra": dict(self.extra),
        }


class IssueTrackerProvider:
    """Base class; a subclass overrides identity plus the three methods."""

    provider_id: str = ""
    display_name: str = ""
    # What an operator has to paste, shown in the attach field's placeholder.
    ref_hint: str = ""
    # Whether this tracker's cached stage can lag the fact. A provider whose
    # issues live in regin's own database cannot, so its surface says `live`
    # instead of `as of T` and offers no Refresh. Declared as a capability
    # rather than read off `provider_id`, which is what keeps every caller
    # outside a provider module free of a vendor branch.
    stage_is_live: bool = False

    def is_configured(self) -> bool:
        """True when a credential exists. False makes the provider invisible
        rather than a source of runtime 500s."""
        return False

    def parse_ref(self, text: str) -> Optional[ExternalRef]:
        """Claim `text`, or return None to let the next provider try."""
        raise NotImplementedError

    def fetch_issue(self, ref: ExternalRef) -> ExternalIssue:
        """The current state of `ref`, or raise `IntegrationError`."""
        raise NotImplementedError

    def create_issue(self, *, parent: ExternalRef, title: str,
                     body: str) -> ExternalRef:
        """File a child of `parent` and return a ref to it.

        The only write regin ever makes to a tracker, and it writes exactly
        three fields: the parent, a title and a body. No label, assignee or
        priority — those have human writers, and guessing them is the
        mirroring the feature pipeline forbids.

        Raising is the correct default: a tracker that cannot create is a
        perfectly usable tracker for everything else regin does, and the
        caller degrades to attaching an existing key.
        """
        raise IntegrationError(
            f"{self.display_name or self.provider_id} cannot create issues "
            "from regin — attach an existing one with --issue")


def clamp_description(text: Optional[str]) -> Optional[str]:
    """Bound a body before it is cached in a row we control.

    Still a clamp rather than a refusal, because the text here is a tracker's
    answer and refusing it would discard an issue over its own description.
    The bound is what changed: at 8,000 it was clipping real specs mid-word.
    """
    if not text:
        return None
    return text if len(text) <= DESCRIPTION_MAX else text[:DESCRIPTION_MAX]
