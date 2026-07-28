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

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Optional

from lib.topics.branch_refs import (
    ABSENT_ELSEWHERE,
    ABSENT_UNPROVABLE,
    classify_absent_paths,
)
from lib.topics.core import (
    EDGE_TYPES,
    NON_DRIFTING_REF_TIERS,
    REF_ROLES,
    REF_TIERS,
    SCHEMA_VERSION,
    TOPIC_STATUSES,
    _valid_id,
    is_dir_ref,
    normalize,
    ref_covers,
)


Severity = str  # "error" | "warning" | "info"

# A ref absent from this checkout but carried by another branch tip. Split out
# of `graph.dead_ref` so remediation can refuse to delete it; see
# `_recode_branch_owned_refs`.
BRANCH_OWNED_REF_CODE = "graph.ref_on_other_branch"

# A ref absent from this checkout whose branch-tip lookup git could not answer
# (no repo, unborn HEAD, git off PATH, a path git refuses as a pathspec). Not
# deletable either — but distinct, so nothing tells the user a file lives on a
# branch when the question was never answered.
UNPROVABLE_REF_CODE = "graph.ref_unverifiable"

# The codes a ref absent from this checkout can carry instead of
# `graph.dead_ref`. Neither is auto-fixable, and the split/group gates waive
# both: the graph did not become unfileable because git could not answer.
UNDELETABLE_REF_CODES = frozenset({BRANCH_OWNED_REF_CODE, UNPROVABLE_REF_CODE})


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


def _noncanonical_ref_issue(tid: str, path: str) -> Optional[ValidationIssue]:
    """Reject a ref path that isn't the repo-relative canonical spelling of a
    file. `schemas//x`, `./schemas/x` and `/etc/x` all *exist* as far as
    `Path.exists` is concerned, but none of them compares equal to the path git
    reports — so a primary overlap under such a ref would silently escape the
    boundary audit, and `../` would point outside the repo entirely."""
    body = path[:-1] if is_dir_ref(path) else path
    parts = body.split("/")
    if path.startswith("/") or "" in parts or "." in parts or ".." in parts:
        return ValidationIssue(
            severity="error",
            code="topic.ref_path_not_canonical",
            message=f"topic {tid} ref {path!r} is not a canonical repo-relative "
                    f"path (no leading '/', '.', '..' or empty segments)",
            topic_ids=(tid,),
            paths=(path,),
        )
    return None


def _ref_target_issues(
    tid: str, path: str, repo_path: Path,
) -> Iterable[ValidationIssue]:
    """Check a ref against the working tree: it must exist, and its *kind* must
    match its spelling. A trailing `/` promises a directory subtree; without one
    the ref promises a single file. Letting the two blur would make coverage
    ambiguous — `ref_covers` would claim a whole subtree from a path the author
    meant as one file."""
    noncanonical = _noncanonical_ref_issue(tid, path)
    if noncanonical is not None:
        yield noncanonical
        return
    target = repo_path / path
    if not target.exists():
        yield ValidationIssue(
            severity="error",
            code="graph.dead_ref",
            message=f"topic {tid} ref does not exist: {path}",
            topic_ids=(tid,),
            paths=(path,),
        )
        return
    if is_dir_ref(path) and not target.is_dir():
        yield ValidationIssue(
            severity="error",
            code="topic.ref_kind_mismatch",
            message=f"topic {tid} ref {path} ends in '/' but is not a "
                    f"directory; drop the trailing slash",
            topic_ids=(tid,),
            paths=(path,),
        )
    elif not is_dir_ref(path) and target.is_dir():
        yield ValidationIssue(
            severity="error",
            code="topic.ref_kind_mismatch",
            message=f"topic {tid} ref {path} is a directory; add a trailing "
                    f"'/' to cite the whole subtree as one ref",
            topic_ids=(tid,),
            paths=(path,),
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
            yield from _ref_target_issues(tid, path, ctx.repo_path)


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

    One deliberate divergence from `scan.validate()`: a ref absent from the
    working tree is an *error* here even when another branch carries it. This
    gate runs on *authoring* — a proposal citing a path this checkout cannot
    show is unreviewable — whereas `scan.validate` gates a whole pre-existing
    graph, where a not-checked-out branch's anchors are legitimate.
    `diff_against_graph` only reports issues the change *introduces*, so
    pre-existing dead refs never block an apply.

    The cases still get distinct codes (`graph.dead_ref` vs
    `BRANCH_OWNED_REF_CODE` vs `UNPROVABLE_REF_CODE`), because equal severity
    here does not mean equal remediation: only a truly dead ref may be
    deleted. See `_recode_branch_owned_refs`.

    Classification is unconditional. It costs one git pass per branch tip, and
    an earlier revision let set-diffing callers opt out to save it — on the
    theory that a code which cancels out between two audits is never read. It
    is: `diff._classify_issues` feeds its `pre_issues` into
    `GraphDiff.graph_warnings`, which is serialized and rendered *by code* by
    DiffPanel and `mcp_server` (the CLI prints only a count). Opting out there
    printed `graph.dead_ref` next to the `drop_dead_refs` checkbox for an
    anchor that checkbox now refuses to drop. Callers that set-diff must also
    stay mutually consistent — classify one side only and the pair no longer
    cancels — which an opt-in flag makes easy to get wrong (CAI-30).
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
    return _recode_branch_owned_refs(issues, ctx.repo_path)


def _recode_branch_owned_refs(
    issues: list[ValidationIssue], repo_path: Optional[Path],
) -> list[ValidationIssue]:
    """Re-code the `graph.dead_ref` findings whose path is merely not checked
    out as `graph.ref_on_other_branch`, and those git could not answer for as
    `graph.ref_unverifiable`.

    All three stay errors — this gate runs on authoring, and a proposal citing
    a path this checkout cannot show is unreviewable either way. What the split
    buys is that everything *downstream* of the finding can tell them apart:
    only `graph.dead_ref` is auto-fixable, so the audit panel's one-click strip
    can no longer delete an anchor whose file lives on an unmerged branch, and
    the group/split gates can waive both non-dead cases (CAI-30).

    The unverifiable code carries its own message because the branch-owned one
    asserts something nobody checked: in a directory with no git at all, every
    absent ref used to be reported as living on another branch, which also left
    the strip button doing nothing with no way to see why.

    Costs nothing on a clean graph — no `graph.dead_ref` findings, no git.
    """
    if repo_path is None:
        return issues
    absent = {p for i in issues if i.code == "graph.dead_ref" for p in i.paths}
    if not absent:
        return issues
    verdicts = classify_absent_paths(repo_path, absent)
    return [_recoded(issue, verdicts) for issue in issues]


def _recoded(
    issue: ValidationIssue, verdicts: dict[str, str],
) -> ValidationIssue:
    if (issue.code != "graph.dead_ref"
            or not issue.topic_ids or len(issue.paths) != 1):
        return issue
    verdict = verdicts.get(issue.paths[0])
    if verdict == ABSENT_ELSEWHERE:
        return replace(
            issue,
            code=BRANCH_OWNED_REF_CODE,
            message=f"topic {issue.topic_ids[0]} ref does not exist in this "
                    f"checkout (it is present on another branch): "
                    f"{issue.paths[0]}",
        )
    if verdict == ABSENT_UNPROVABLE:
        return replace(
            issue,
            code=UNPROVABLE_REF_CODE,
            message=f"topic {issue.topic_ids[0]} ref does not exist in this "
                    f"checkout and could not be verified against branch "
                    f"tips: {issue.paths[0]}",
        )
    return issue


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
        pair = tuple(sorted(set(tids) | _covering_owners(owners, path)))
        if len(pair) < 2:
            continue
        kind = "directory" if is_dir_ref(path) else "file"
        out.append(ValidationIssue(
            severity="warning",
            code="graph.shared_primary_ref",
            message=(
                f"{kind} {path} is a primary ref of {len(pair)} topics "
                f"({', '.join(pair)}); make it primary in the one topic that "
                f"explains it and tier:\"reference\" in the others so their "
                f"wikis don't cover the same code"
            ),
            topic_ids=pair,
            paths=(path,),
        ))
    return out


def _covering_owners(owners: dict[str, list[str]], path: str) -> set[str]:
    """Topics that claim `path` primary through a *directory* ref rather than by
    naming it. Asymmetric on purpose: a dir ref's own path is never reported
    against the files it covers, so one collision yields exactly one issue —
    keyed on the narrower path, which is where the boundary has to be redrawn."""
    return {
        tid
        for dir_path, dir_tids in owners.items()
        if dir_path != path and is_dir_ref(dir_path) and ref_covers(dir_path, path)
        for tid in dir_tids
    }


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
    "BRANCH_OWNED_REF_CODE",
    "UNDELETABLE_REF_CODES",
    "UNPROVABLE_REF_CODE",
    "ValidationIssue",
    "GraphContext",
    "Severity",
    "validate_topic",
    "audit_graph",
    "split_by_severity",
    "diff_issues",
]
