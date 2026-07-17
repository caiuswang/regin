"""Unified validator for topic graphs and proposed topics.

Replaces the two pre-refactor validators (`lib/topics/scan.py::validate`
and `lib/topics/proposal_drafting.py::validate_proposal`) with a single
checker keyed by `mode`:

- `mode="proposal"` — per-topic checks only. Skips graph-wide rules
  (alias collisions across the approved set, ref-existence on disk,
  edge-target existence) because a draft topic hasn't been merged yet.
  Used by the diff/apply layer to validate a proposed topic against
  *itself*: field types, required strings, enum membership, intra-topic
  dup-alias.
- `mode="approved"` — runs everything `proposal` does, plus the
  graph-wide rules. Used after a diff is applied to verify the new
  graph is still consistent.

`audit_graph(graph)` walks the whole approved graph and returns issues;
the result powers the `/audit` endpoint added in Phase B.

`ValidationIssue` carries a stable identity key built from
`(code, sorted(topic_ids), sorted(paths), sorted(aliases))` so the
diff layer can subtract pre-apply issues from post-apply issues — only
the *new* issues (those a diff would introduce) block apply.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from lib.topics.core import (
    EDGE_TYPES,
    NON_DRIFTING_REF_TIERS,
    REF_ROLES,
    REF_TIERS,
    SCHEMA_VERSION,
    TOPIC_STATUSES,
    _valid_id,
    normalize,
)


Severity = str  # "error" | "warning" | "info"


@dataclass(frozen=True)
class ValidationIssue:
    """One validation finding.

    `severity` drives policy (error blocks apply; warning is advisory).
    `code` is a stable string so the bulk-fix tool and UI can match
    findings without parsing `message`. The three list fields name the
    artefacts the finding references; they participate in the identity
    key so two issues that share the same code but point at different
    topics/aliases/paths are distinct.
    """

    severity: Severity
    code: str
    message: str
    topic_ids: tuple[str, ...] = field(default_factory=tuple)
    paths: tuple[str, ...] = field(default_factory=tuple)
    aliases: tuple[str, ...] = field(default_factory=tuple)

    @property
    def identity(self) -> tuple:
        """Stable key for set-diffing — order-insensitive on list fields."""
        return (
            self.code,
            tuple(sorted(self.topic_ids)),
            tuple(sorted(self.paths)),
            tuple(sorted(self.aliases)),
        )


@dataclass(frozen=True)
class GraphContext:
    """Snapshot of the surrounding graph passed into `validate_topic`.

    Held outside the topic dict so the caller can fabricate a "what-if"
    context (e.g. a hypothetical graph with the proposed topic inserted)
    without mutating the real graph. `repo_path=None` skips
    ref-existence checks — useful in unit tests and in proposal mode.
    """

    topic_ids: frozenset[str] = frozenset()
    alias_owners: dict[str, str] = field(default_factory=dict)  # normalized alias -> owning topic_id
    repo_path: Optional[Path] = None
    current_topic_id: Optional[str] = None


_REQUIRED_STR_FIELDS = ("label", "intent", "status")
_LIST_FIELDS = ("aliases", "refs", "edges", "commands", "include_globs", "exclude_globs")


def validate_topic(
    topic: dict[str, Any],
    *,
    mode: str,
    topic_id: Optional[str] = None,
    graph_context: Optional[GraphContext] = None,
) -> list[ValidationIssue]:
    """Validate one topic dict.

    `mode` is `"proposal"` or `"approved"`. `topic_id` defaults to
    `topic["id"]` so the caller can omit it for proposal topics that
    carry their id inline; pass it explicitly when validating an
    approved topic dict that lives keyed-by-id in the graph and so
    doesn't repeat the id in its body.
    """
    if mode not in {"proposal", "approved"}:
        raise ValueError(f"mode must be 'proposal' or 'approved', got {mode!r}")

    tid = topic_id or topic.get("id") or "<unknown>"
    ctx = graph_context or GraphContext()
    issues: list[ValidationIssue] = []

    if not _valid_id(tid):
        issues.append(ValidationIssue(
            severity="error",
            code="topic.invalid_id",
            message=f"topic id {tid!r} must use lowercase letters, digits, dots, underscores, or hyphens",
            topic_ids=(tid,),
        ))

    for fname in _REQUIRED_STR_FIELDS:
        v = topic.get(fname)
        if not isinstance(v, str) or not v:
            issues.append(ValidationIssue(
                severity="error",
                code="topic.missing_field",
                message=f"topic {tid} field {fname} must be a non-empty string",
                topic_ids=(tid,),
            ))

    if topic.get("status") not in TOPIC_STATUSES:
        issues.append(ValidationIssue(
            severity="error",
            code="topic.invalid_status",
            message=f"topic {tid} has invalid status {topic.get('status')!r}",
            topic_ids=(tid,),
        ))

    for fname in _LIST_FIELDS:
        if not isinstance(topic.get(fname, []), list):
            issues.append(ValidationIssue(
                severity="error",
                code="topic.invalid_field",
                message=f"topic {tid} field {fname} must be a list",
                topic_ids=(tid,),
            ))

    issues.extend(_validate_aliases(tid, topic, ctx, mode))
    issues.extend(_validate_refs(tid, topic, ctx, mode))
    issues.extend(_validate_edges(tid, topic, ctx, mode))
    return issues


def _validate_aliases(
    tid: str, topic: dict[str, Any], ctx: GraphContext, mode: str,
) -> Iterable[ValidationIssue]:
    aliases = topic.get("aliases", []) or []
    if not isinstance(aliases, list):
        return  # already flagged by the list-field check
    seen_local: set[str] = set()
    for alias in aliases:
        if not isinstance(alias, str) or not alias:
            yield ValidationIssue(
                severity="error",
                code="topic.invalid_alias",
                message=f"topic {tid} has empty or non-string alias",
                topic_ids=(tid,),
            )
            continue
        key = normalize(alias)
        if key in seen_local:
            yield ValidationIssue(
                severity="error",
                code="topic.duplicate_alias_local",
                message=f"topic {tid} repeats alias {alias!r}",
                topic_ids=(tid,),
                aliases=(alias,),
            )
            continue
        seen_local.add(key)
        if mode == "approved":
            owner = ctx.alias_owners.get(key)
            if owner and owner != tid:
                # Sort the pair so the identity key is symmetric — the
                # "same collision viewed from the other topic" produces
                # the same identity and set-diffs to itself.
                pair = tuple(sorted((tid, owner)))
                yield ValidationIssue(
                    severity="error",
                    code="graph.duplicate_alias",
                    message=f"duplicate alias {alias!r} on topics {owner} and {tid}",
                    topic_ids=pair,
                    aliases=(alias,),
                )


def _ref_vocab_issues(
    tid: str, ref: dict[str, Any], path: Any,
) -> Iterable[ValidationIssue]:
    """Validate a ref's optional controlled-vocabulary fields — `role` (what
    kind of file) and `tier` (how central to the wiki). Both are optional; an
    unset value is fine, a set-but-unknown value is an error. Kept separate so
    `_validate_refs` doesn't grow a branch per vocabulary field."""
    paths = (path,) if isinstance(path, str) else ()
    role = ref.get("role")
    if role is not None and role not in REF_ROLES:
        yield ValidationIssue(
            severity="error", code="topic.invalid_role",
            message=f"topic {tid} ref {path!r} has invalid role {role!r}",
            topic_ids=(tid,), paths=paths,
        )
    tier = ref.get("tier")
    if tier is not None and tier not in REF_TIERS:
        yield ValidationIssue(
            severity="error", code="topic.invalid_tier",
            message=f"topic {tid} ref {path!r} has invalid tier {tier!r}",
            topic_ids=(tid,), paths=paths,
        )


def _validate_refs(
    tid: str, topic: dict[str, Any], ctx: GraphContext, mode: str,
) -> Iterable[ValidationIssue]:
    refs = topic.get("refs", []) or []
    if not isinstance(refs, list):
        return
    seen_paths: set[str] = set()
    for ref in refs:
        if not isinstance(ref, dict):
            yield ValidationIssue(
                severity="error",
                code="topic.bad_ref_object",
                message=f"topic {tid} refs must be objects",
                topic_ids=(tid,),
            )
            continue
        path = ref.get("path")
        yield from _ref_vocab_issues(tid, ref, path)
        if not isinstance(path, str) or not path:
            yield ValidationIssue(
                severity="error",
                code="topic.missing_ref_path",
                message=f"topic {tid} has ref with missing path",
                topic_ids=(tid,),
            )
            continue
        if path in seen_paths:
            yield ValidationIssue(
                severity="warning",
                code="topic.duplicate_ref",
                message=f"topic {tid} has duplicate ref {path}",
                topic_ids=(tid,),
                paths=(path,),
            )
        seen_paths.add(path)
        if mode == "approved" and ctx.repo_path is not None:
            if not (ctx.repo_path / path).exists():
                yield ValidationIssue(
                    severity="error",
                    code="graph.dead_ref",
                    message=f"topic {tid} ref does not exist: {path}",
                    topic_ids=(tid,),
                    paths=(path,),
                )


def _validate_edges(
    tid: str, topic: dict[str, Any], ctx: GraphContext, mode: str,
) -> Iterable[ValidationIssue]:
    edges = topic.get("edges", []) or []
    if not isinstance(edges, list):
        return
    for edge in edges:
        if not isinstance(edge, dict):
            yield ValidationIssue(
                severity="error",
                code="topic.bad_edge_object",
                message=f"topic {tid} edges must be objects",
                topic_ids=(tid,),
            )
            continue
        target = edge.get("target")
        etype = edge.get("type", "related")
        if etype not in EDGE_TYPES:
            yield ValidationIssue(
                severity="error",
                code="topic.invalid_edge_type",
                message=f"topic {tid} edge has invalid type {edge.get('type')!r}",
                topic_ids=(tid,),
            )
        if mode == "approved":
            if not isinstance(target, str) or target not in ctx.topic_ids:
                yield ValidationIssue(
                    severity="error",
                    code="graph.orphan_edge_target",
                    message=f"topic {tid} edge target does not exist: {target}",
                    topic_ids=(tid, target) if isinstance(target, str) else (tid,),
                )


def audit_graph(
    graph: dict[str, Any],
    *,
    repo_path: Optional[Path | str] = None,
) -> list[ValidationIssue]:
    """Walk the approved graph and return all validation issues.

    Mirrors what `scan.validate()` did before the refactor but emits
    `ValidationIssue` objects with stable identity keys so the diff
    layer can set-diff them. `repo_path=None` skips on-disk ref checks
    (used by unit tests that don't materialize files).
    """
    issues: list[ValidationIssue] = []

    if graph.get("version") != SCHEMA_VERSION:
        issues.append(ValidationIssue(
            severity="error",
            code="graph.schema_drift",
            message="topic graph version must be 1",
        ))
    if not isinstance(graph.get("repo"), str) or not graph.get("repo"):
        issues.append(ValidationIssue(
            severity="error",
            code="graph.invalid_repo",
            message="topic graph repo must be a non-empty string",
        ))

    topics = graph.get("topics")
    if not isinstance(topics, dict):
        issues.append(ValidationIssue(
            severity="error",
            code="graph.invalid_topics",
            message="topic graph topics must be an object",
        ))
        return issues

    # Build the cross-topic context once so per-topic checks share it.
    alias_owners: dict[str, str] = {}
    for tid, topic in topics.items():
        if not isinstance(topic, dict):
            continue
        for alias in topic.get("aliases", []) or []:
            if isinstance(alias, str) and alias:
                alias_owners.setdefault(normalize(alias), tid)

    ctx = GraphContext(
        topic_ids=frozenset(topics.keys()),
        alias_owners=alias_owners,
        repo_path=Path(repo_path) if repo_path else None,
    )

    for tid, topic in topics.items():
        if not isinstance(topic, dict):
            issues.append(ValidationIssue(
                severity="error",
                code="graph.invalid_topic_value",
                message=f"topic {tid} must be an object",
                topic_ids=(tid,),
            ))
            continue
        issues.extend(validate_topic(
            topic,
            mode="approved",
            topic_id=tid,
            graph_context=ctx,
        ))
    issues.extend(_audit_taxonomy_placement(topics))
    issues.extend(_audit_shared_primary_refs(topics))
    return issues


def _audit_taxonomy_placement(
    topics: dict[str, Any],
) -> list[ValidationIssue]:
    """Advisory placement checks for the navigation taxonomy. A non-bucket
    topic must reach a `kind:"bucket"` node through its `parent_id` chain; one
    whose chain has no real root — a null, dangling, or cyclic `parent_id` at
    its head — is *unclassified*. It still works (recall routes it to the
    reserved `unclassified` bucket) but it's a curation backlog item, so it
    warns rather than errors. A sub-topic nested under another topic that *is*
    placed is classified transitively and is NOT re-flagged — only the unplaced
    root of a subtree warns. This reuses `tree.effective_parent`, so the audit
    and the navigation tree can never disagree about what is classified."""
    from lib.topics.tree import UNCLASSIFIED, effective_parent
    buckets = {tid for tid, n in topics.items()
               if isinstance(n, dict) and n.get("kind") == "bucket"}
    out: list[ValidationIssue] = []
    for tid, topic in topics.items():
        if not isinstance(topic, dict) or tid in buckets:
            continue
        if effective_parent(topics, buckets, tid) == UNCLASSIFIED:
            out.append(ValidationIssue(
                severity="warning",
                code="topic.unclassified",
                message=f"topic {tid} has no bucket ancestor "
                        f"(parent_id={topic.get('parent_id')!r}); pick a "
                        f"bucket so it leaves the unclassified backlog",
                topic_ids=(tid,),
            ))
    return out


def _collect_primary_ref_owners(
    tid: str, topic: dict[str, Any], owners: dict[str, list[str]],
) -> None:
    """Record `tid` as an owner of each of its *primary* refs (tier absent or
    `"primary"`), de-duped within the topic. Pointer-only (`reference`) refs are
    skipped — a file may be cited as context by many topics without either
    wiki claiming to explain it."""
    seen: set[str] = set()
    for ref in topic.get("refs", []) or []:
        if not isinstance(ref, dict):
            continue
        path = ref.get("path")
        if not isinstance(path, str) or not path or path in seen:
            continue
        seen.add(path)
        if ref.get("tier") not in NON_DRIFTING_REF_TIERS:
            owners.setdefault(path, []).append(tid)


def _audit_shared_primary_refs(topics: dict[str, Any]) -> list[ValidationIssue]:
    """Boundary check: a file should be the *primary* ref of exactly one topic —
    the one whose wiki actually explains it. When two topics both list a file as
    primary, their wikis tend to describe the same code and a content-drift edit
    to that file nags both: the "two overlapping wikis" smell. Advisory
    (warning), never a hard block — a transient overlap while a topic is
    downgraded/replaced is legitimate, and the diff layer already surfaces only
    the collisions a given apply *newly* introduces (pre-existing overlap stays
    informational)."""
    owners: dict[str, list[str]] = {}
    for tid, topic in topics.items():
        if isinstance(topic, dict):
            _collect_primary_ref_owners(tid, topic, owners)
    out: list[ValidationIssue] = []
    for path, tids in sorted(owners.items()):
        if len(tids) < 2:
            continue
        pair = tuple(sorted(tids))
        out.append(ValidationIssue(
            severity="warning",
            code="graph.shared_primary_ref",
            message=(
                f"file {path} is a primary ref of {len(pair)} topics "
                f"({', '.join(pair)}); make it primary in the one topic that "
                f"explains it and tier:\"reference\" in the others so their "
                f"wikis don't cover the same code"
            ),
            topic_ids=pair,
            paths=(path,),
        ))
    return out


def split_by_severity(issues: list[ValidationIssue]) -> tuple[list[ValidationIssue], list[ValidationIssue]]:
    """Return `(errors, warnings_and_info)` split."""
    errors = [i for i in issues if i.severity == "error"]
    rest = [i for i in issues if i.severity != "error"]
    return errors, rest


def diff_issues(
    before: list[ValidationIssue],
    after: list[ValidationIssue],
) -> tuple[list[ValidationIssue], list[ValidationIssue]]:
    """Return `(introduced, resolved)` between two issue sets.

    Uses the stable `identity` key so two issues that name the same
    `(code, topics, paths, aliases)` triple are considered equal even
    if their message strings differ slightly across runs.
    """
    before_keys = {i.identity for i in before}
    after_keys = {i.identity for i in after}
    introduced = [i for i in after if i.identity not in before_keys]
    resolved = [i for i in before if i.identity not in after_keys]
    return introduced, resolved


__all__ = [
    "ValidationIssue",
    "GraphContext",
    "Severity",
    "validate_topic",
    "audit_graph",
    "split_by_severity",
    "diff_issues",
]
