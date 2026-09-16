"""Where a feature currently stands in the pipeline (CAI-215, CAI-236).

Four stages, each read from something somebody declared — the tracker state the
work occupies, and the defects blamed against it. Nothing here infers a stage
from prose, proximity or a clock.

`escaped` is the one that did not previously exist. It replaced `reworking`,
which became unreachable the moment the pipeline forbade reopening a shipped
issue: `reworking` fired only on `rounds > 1`, i.e. a completion walked back,
and that is exactly what no longer happens. The live reading of *shipped and
still leaking* is a count of escapes in regin's own ledger, so `escaped` is the
first stage name not derived from tracker state alone — accepted because
`open_defects` already rides in the same struct from the same source, and an
escape is a human's declared blame, not an inference.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from lib.defects import escapes as escapes_module
from lib.defects import store

SPEC = "spec"
BUILDING = "building"
SHIPPED = "shipped"
ESCAPED = "escaped"

_BLURB = {
    SPEC: "specified, not started",
    BUILDING: "being built for the first time",
    SHIPPED: "completed and never walked back",
    ESCAPED: "shipped, and bugs have been blamed on it since",
}


@dataclass(frozen=True)
class Stage:
    name: str
    rounds: int
    open_defects: int
    escapes: int = 0
    key: str = ""

    @property
    def blurb(self) -> str:
        return _BLURB.get(self.name, "")

    def to_dict(self) -> dict:
        """Flat, and it must stay flat: the three live consumers are the
        `feature.stage` span attributes, the task-link payload and
        `TaskLinkCard.vue`, and span attributes are key/values by nature."""
        return {"feature_key": self.key, "stage": self.name,
                "blurb": self.blurb, "rounds": self.rounds,
                "open_defects": self.open_defects, "escapes": self.escapes}


def name_for(category: str, escapes: int) -> str:
    """Two declared facts decide it: the state the work occupies now, and
    whether anything has been blamed on it since it shipped."""
    if category == "completed":
        return ESCAPED if escapes > 0 else SHIPPED
    return BUILDING if category == "started" else SPEC


def _local_counts(key: str) -> tuple[int, int]:
    """`(open defects, escapes)` from regin's own ledger.

    Different figures and both stay: `escapes` is all-time and historical —
    closing an escape does not un-escape it — while `open_defects` is current.
    """
    if not key:
        return 0, 0
    rows = store.for_feature(key)
    return (sum(1 for row in rows if row.status == "open"),
            escapes_module.counts_for(key).escapes)


def stage_for(issue: Optional[Any]) -> Stage:
    """The stage a fetched tracker issue is in."""
    if issue is None:
        return Stage(name=SPEC, rounds=1, open_defects=0)
    key = getattr(issue, "key", "") or ""
    open_defects, escaped = _local_counts(key)
    return Stage(
        name=name_for(getattr(issue, "state_category", ""), escaped),
        rounds=getattr(issue, "rounds", 1) or 1,
        open_defects=open_defects,
        escapes=escaped,
        key=key,
    )


def stage_from_snapshot(snapshot: Optional[dict]) -> Optional[Stage]:
    """The stage a *cached* issue snapshot implies, or None when there is none.

    None rather than a default: `spec` is a real stage, so letting it stand in
    for "never synced" would report a feature as specified-not-started when in
    fact nobody has looked. For the same reason `unknown` is never a stage
    name — it is a statement about us, not about the feature.
    """
    if not snapshot:
        return None
    key = snapshot.get("key") or ""
    open_defects, escaped = _local_counts(key)
    return Stage(
        name=name_for(snapshot.get("state_category") or "", escaped),
        rounds=snapshot.get("rounds") or 1,
        open_defects=open_defects,
        escapes=escaped,
        key=key,
    )


def stage_for_key(key: str) -> Stage:
    """The stage of the feature the tracker knows by this key.

    The fetch warms the shared issue cache on its way past, so a CLI run
    leaves the web surface something to render. The reverse never happens:
    this function does not fall back to the cache, because the stage it
    returns decides escapes.
    """
    from lib.defects import tracker  # noqa: PLC0415  (circular at import time)
    from lib.tasks import issue_cache  # noqa: PLC0415
    provider, ref = tracker.resolve(key)
    issue = provider.fetch_issue(ref)
    issue_cache.remember(ref.provider, ref.key, issue)
    return stage_for(issue)


def fingerprint(session: Optional[str], result: "Stage") -> None:
    """Leave the span `regin gate pipeline-ran` counts.

    A refusal is reported on stderr rather than swallowed: silence would leave
    the caller believing it had proof and then be convicted of skipping — the
    unfollowable red gate that retired `ui-verified`.
    """
    if not session:
        return
    from lib.hook_plugin import post_span  # noqa: PLC0415
    landed = post_span(trace_id=session, name="feature.stage",
                       attributes=result.to_dict())
    if not landed:
        import sys  # noqa: PLC0415
        print(f"warning: the feature.stage span did not reach regin's ingest, "
              f"so `regin gate pipeline-ran --session {session}` will not see "
              "this. Start `regin serve` and re-run before treating a red gate "
              "as a skip.", file=sys.stderr)
