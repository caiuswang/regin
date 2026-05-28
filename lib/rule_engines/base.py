"""Rule engine protocol — the seam by which regin plugs in lint/check tooling.

A rule engine is the thing that (a) parses rule sources from disk,
(b) decides whether a given rule applies to a given file, (c) runs the
rule against that file and reports violations. The existing GritQL
integration is the first concrete adapter; this module defines the
contract it implements.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol


@dataclass(frozen=True)
class Rule:
    """One rule, engine-agnostic.

    `metadata` is the engine-specific bag (e.g. grit layer/guide/language).
    The index writer and web/api surface read engine-agnostic top-level
    fields plus whatever keys the engine stamps into metadata.
    """

    id: str
    engine: str          # e.g. "grit"
    summary: str
    severity: str
    triggers: tuple[str, ...]
    source_file: str     # relative to the engine's source root
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Violation:
    rule_id: str
    file_path: str
    match_count: int
    detail: str | None = None


@dataclass(frozen=True)
class ApplicableRules:
    """Engine's answer to 'what rules apply to this file?'.

    `items` is a list of (engine_for_run, rule, guide) triples — each
    engine is the instance to call `.run()` against for that rule. For
    most engines this is `self`; engines that do per-file discovery
    (grit walking up to a repo-local `.grit/` dir) may return a
    different instance pointed at the discovered location.

    `total_in_pool` is the size of the engine's rule pool before
    applies_to + disabled filtering — used by the handler to report
    "checked N applicable of M total".

    `repo_root` is the engine's notion of the project root for this
    file, used downstream for relpath rendering. None when the engine
    has no opinion.
    """
    items: list[tuple[Any, Any, str | None]]
    total_in_pool: int
    repo_root: str | None


class RuleEngine(Protocol):
    """Adapter surface every rule engine must expose."""

    id: str      # instance id, e.g. "grit"
    kind: str    # class key used by the registry factory, e.g. "grit"

    def parse_rules(self) -> list[Rule]:
        """Load every rule this engine owns."""
        ...

    def applies_to(self, rule: Rule, file_path: str, content: str) -> bool:
        """Return True if `rule` should be evaluated against `file_path`."""
        ...

    def applicable_rules(self, file_path: str, content: str) -> ApplicableRules:
        """Rules to evaluate against this file (post-disabled, post-applies_to).

        Each engine owns its own discovery, indexing, and filtering
        decisions — handlers iterate the returned items uniformly.
        """
        ...

    def run(self, rule: Rule, file_path: str, repo_root: str) -> Violation | None:
        """Evaluate `rule` against `file_path`. Returns None if no match."""
        ...

    def contributed_skills(self) -> list[dict]:
        """Engine-contributed auto-generated skills (e.g. grit-rules)."""
        ...

    @classmethod
    def reserved_auto_skill_ids(cls) -> frozenset[str]:
        """Skill ids this engine kind exclusively owns as auto-generated.

        Used by `lib.skills.skill_registry` to keep these ids out of the
        pattern-walk fallback when no instance of the engine is currently
        configured — without this, a stale directory left behind under
        `patterns/<reserved-id>/` would resurrect the skill as if it were
        a user pattern. Static (class-level) so it can be queried without
        instantiating the engine.
        """
        ...

    def write_index(self) -> dict:
        """Regenerate on-disk rule index artefacts (rules.json, RULES.md, ...)."""
        ...


def default_applicable_rules(engine, file_path: str, content: str) -> ApplicableRules:
    """Shared `applicable_rules` impl for engines whose rules come from
    `parse_rules()` + `applies_to()` + the configured `project_root`.

    Engines with per-file discovery (grit walking up for a repo-local
    `.grit/` dir) implement their own; everyone else delegates here so
    the protocol pipeline is consistent.
    """
    import os
    from lib.rules import engine_rule_disable
    off = engine_rule_disable.disabled_ids(engine.id)
    all_rules = [r for r in engine.parse_rules() if r.id not in off]
    if not all_rules:
        return ApplicableRules(items=[], total_in_pool=0,
                               repo_root=getattr(engine, 'project_root', None))
    project_root = getattr(engine, 'project_root', None)
    match_path = file_path
    if project_root:
        try:
            match_path = os.path.relpath(file_path, project_root)
        except ValueError:
            match_path = file_path
    items: list[tuple] = []
    for rule in all_rules:
        if not engine.applies_to(rule, match_path, content):
            continue
        guide = getattr(rule, 'metadata', {}).get('guide')
        items.append((engine, rule, guide))
    return ApplicableRules(items=items, total_in_pool=len(all_rules),
                           repo_root=project_root)
