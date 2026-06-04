"""Phase F: compose corrective topic diffs from a list of audit issues.

The bulk-fix tool is intentionally narrow: it auto-fixes only the
issue codes whose resolution is unambiguous.

  - `graph.dead_ref`           — drop the ref. No information lost
    that wasn't already lost when the file was deleted.
  - `graph.orphan_edge_target` — drop the edge. The target topic
    doesn't exist; the edge points nowhere.

`graph.duplicate_alias` is NOT auto-fixable. The right resolution
depends on which topic the user considers canonical and whether
the right answer is to rename one alias rather than drop it. The
audit list surfaces it (with provenance) but the bulk-fix endpoint
refuses to touch it.

Everything else (per-topic `topic.duplicate_ref`, schema drift,
invalid roles, ...) is left untouched — those are content bugs the
user should resolve via DiffPanel on a re-proposed topic, not a
mass click.
"""

from __future__ import annotations

import copy
from collections import defaultdict
from typing import Any

from lib.topics.validation import ValidationIssue


AUTO_FIXABLE_CODES: frozenset[str] = frozenset({
    "graph.dead_ref",
    "graph.orphan_edge_target",
})


def _group_issues_by_topic(
    issues: list[ValidationIssue],
    selected_codes: set[str],
    topics: dict[str, Any],
) -> dict[str, list[ValidationIssue]]:
    """Bucket each selected issue under the first of its topic_ids that
    exists in the graph (first match wins; one topic owns the fix)."""
    by_topic: dict[str, list[ValidationIssue]] = defaultdict(list)
    for issue in issues:
        if issue.code not in selected_codes:
            continue
        for tid in issue.topic_ids:
            if tid in topics:
                by_topic[tid].append(issue)
                break  # one topic owns the fix; first match wins
    return by_topic


def _strip_dead_refs(cleaned: dict[str, Any], topic_issues: list[ValidationIssue]) -> None:
    """Drop refs whose path is flagged dead by a `graph.dead_ref` issue."""
    dead_paths = {
        path
        for issue in topic_issues
        if issue.code == "graph.dead_ref"
        for path in issue.paths
    }
    if dead_paths:
        cleaned["refs"] = [
            ref for ref in cleaned.get("refs", []) or []
            if not (isinstance(ref, dict) and ref.get("path") in dead_paths)
        ]


def _strip_orphan_edges(
    cleaned: dict[str, Any], topic_issues: list[ValidationIssue], tid: str
) -> None:
    """Drop edges whose target is flagged orphan by `graph.orphan_edge_target`."""
    orphan_targets = {
        t
        for issue in topic_issues
        if issue.code == "graph.orphan_edge_target"
        for t in issue.topic_ids
        if t != tid
    }
    if orphan_targets:
        cleaned["edges"] = [
            edge for edge in cleaned.get("edges", []) or []
            if not (isinstance(edge, dict) and edge.get("target") in orphan_targets)
        ]


def compose_fix(
    graph: dict[str, Any],
    issues: list[ValidationIssue],
    *,
    codes_to_fix: set[str] | frozenset[str],
) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    """Return `[(topic_id, cleaned_topic, before_topic)]` for each
    affected topic.

    `codes_to_fix` is filtered against `AUTO_FIXABLE_CODES` — any
    other code is silently dropped. Issues whose topic doesn't exist
    in the graph (e.g. dangling from a prior bad accept) are also
    dropped.
    """
    selected_codes = set(codes_to_fix) & AUTO_FIXABLE_CODES
    if not selected_codes:
        return []
    topics = graph.get("topics") or {}
    by_topic = _group_issues_by_topic(issues, selected_codes, topics)

    fixes: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for tid, topic_issues in by_topic.items():
        original = topics[tid]
        cleaned = copy.deepcopy(original)
        _strip_dead_refs(cleaned, topic_issues)
        _strip_orphan_edges(cleaned, topic_issues, tid)
        if cleaned != original:
            fixes.append((tid, cleaned, original))
    return fixes


__all__ = ["AUTO_FIXABLE_CODES", "compose_fix"]
