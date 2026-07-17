"""Batched agentic drift judge: one LLM pass per evolve sweep instead of one
per drifted topic.

The mechanical tiers (wiki anchors, cosine, hash) decide cheaply whether a
change *might* have invalidated a wiki; this judge reads what actually
changed and decides whether each pending refresh is worth drafting. Its
evidence beats the per-stub triage's: each digest row stamps the repo HEAD at
capture (`captured_commit`), so the judge sees `git diff <base> -- <path>` —
the real old→new change — plus the vanished wiki anchors and the current
wiki, and it keeps the triage prompt's agentic contract (it may Read/Grep
further).

Consumed by `lib/topics/agent_spawn.maybe_spawn_refresh_agents`, which falls
back to the per-item triage when this judge is unavailable or its answer is
unparseable for a topic (fail open, same as triage). Best-effort throughout —
never raises into the evolve caller.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

from lib.activity_log import get_activity_logger
from lib.topics.drift import _git
from lib.topics.ref_digest import digests_for_topic, repo_id_for_path
from lib.topics.wiki import topic_wiki_page

log = get_activity_logger("topics")

DIFF_CHARS_PER_PATH = 2500
WIKI_EXCERPT_CHARS = 1200

# Tolerant of the ways LLMs decorate a line — bullets, bold, backticks,
# trailing punctuation, a missing em-dash before the reason — because every
# unparsed line silently costs a fallback triage call per topic.
# Intra-line gaps are [ \t]*, never \s* — \s matches the newline, which lets
# one verdict's reason clause swallow the whole next verdict line.
_VERDICT_LINE = re.compile(
    r"^[ \t]*(?:[-*•][ \t]*)?[`*_]*(?P<topic>[A-Za-z0-9._-]+)[`*_]*[ \t]*"
    r"[:=][ \t]*[`*_]*(?P<verdict>MATERIAL|TRIVIAL)[`*_]*[.,;]?"
    r"(?:[ \t]*[—–:-]*[ \t]*(?P<reason>.*))?$",
    re.IGNORECASE | re.MULTILINE)


def _diff_excerpt(repo_path: str | Path, base: Optional[str],
                  path: str) -> Optional[str]:
    """`git diff <base> -- <path>` (worktree vs the captured baseline),
    truncated; None when there is no baseline or git can't produce it."""
    if not base:
        return None
    lines = _git(repo_path, ["diff", base, "--", path])
    if not lines:
        return None
    text = "\n".join(lines)
    if len(text) > DIFF_CHARS_PER_PATH:
        text = text[:DIFF_CHARS_PER_PATH].rstrip() + "\n…(diff truncated)"
    return text


def _wiki_excerpt(repo_path: str | Path, topic_id: str) -> str:
    page = topic_wiki_page(repo_path, topic_id)
    if not page.is_file():
        return "(no wiki on file)"
    try:
        text = page.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return "(wiki unreadable)"
    if len(text) > WIKI_EXCERPT_CHARS:
        text = text[:WIKI_EXCERPT_CHARS].rstrip() + "\n…(truncated)"
    return text or "(no wiki on file)"


def _path_evidence(repo_path: str | Path, item: dict[str, Any],
                   bases: dict[str, Optional[str]]) -> str:
    missing = item.get("missing_anchors") or {}
    parts: list[str] = []
    for path in item.get("drifted_paths") or []:
        gone = missing.get(path)
        cited = (f" — wiki cites {', '.join(f'`{a}`' for a in gone)}, "
                 f"no longer present" if gone else "")
        parts.append(f"#### {path}{cited}")
        diff = _diff_excerpt(repo_path, bases.get(path), path)
        parts.append(f"```diff\n{diff}\n```" if diff
                     else "(no baseline diff available — read the file as it is now)")
    return "\n".join(parts) or "(no changed paths recorded)"


def _topic_block(repo_path: str | Path, item: dict[str, Any]) -> str:
    topic_id = item["topic_id"]
    repo_id = repo_id_for_path(repo_path)
    bases = ({d["path"]: d.get("captured_commit")
              for d in digests_for_topic(repo_id, topic_id)}
             if repo_id is not None else {})
    return (
        f"### topic `{topic_id}`\n\n"
        f"Changed refs:\n{_path_evidence(repo_path, item, bases)}\n\n"
        f"Current wiki:\n```markdown\n{_wiki_excerpt(repo_path, topic_id)}\n```"
    )


def _parse_verdicts(answer: str,
                    topic_ids: set[str]) -> dict[str, dict[str, str]]:
    by_fold = {t.lower(): t for t in topic_ids}
    out: dict[str, dict[str, str]] = {}
    for m in _VERDICT_LINE.finditer(answer):
        topic = by_fold.get(m.group("topic").lower())
        if topic is None:
            continue
        out[topic] = {"verdict": m.group("verdict").lower(),
                      "reason": (m.group("reason") or "").strip()}
    return out


def judge_drift_batch(repo_path: str | Path, items: list[dict[str, Any]]
                      ) -> Optional[dict[str, dict[str, str]]]:
    """One batched materiality pass over every pending drift item. Returns
    `{topic_id: {"verdict": "material"|"trivial", "reason": …}}`, possibly
    missing topics the answer didn't cover (the caller treats those as
    material — fail open), or None when no judge ran at all (no agent
    configured, empty answer, or any error) so the caller can fall back to
    the per-item triage."""
    if not items:
        return {}
    try:
        answer = _ask_judge(repo_path, items)
        if not answer or not str(answer).strip():
            return None
        topic_ids = {item["topic_id"] for item in items
                     if item.get("topic_id")}
        verdicts = _parse_verdicts(str(answer), topic_ids)
        if not verdicts:
            # An answer with zero parseable verdicts is a judge that didn't
            # play the game (e.g. an old-style triage reviewer) — fall back
            # to per-item triage rather than fail-opening every item.
            log.write("drift_judge_batch_unparsed", repo_path=str(repo_path),
                      judged=len(items))
            return None
        log.write("drift_judge_batch", repo_path=str(repo_path),
                  judged=len(items), parsed=len(verdicts),
                  trivial=sum(1 for v in verdicts.values()
                              if v["verdict"] == "trivial"))
        return verdicts
    except Exception:  # noqa: BLE001 - judging must never break the evolve caller
        log.error("drift_judge_batch_failed", exc_info=True)
        return None


def _ask_judge(repo_path: str | Path,
               items: list[dict[str, Any]]) -> "str | None":
    from lib.memory.adapters import resolve_proposal_reviewer
    from lib.prompts import render_surface
    from lib.prompts.surfaces.triage import JUDGE_BATCH_SURFACE_ID

    blocks = "\n\n".join(_topic_block(repo_path, item) for item in items)
    prompt = render_surface(JUDGE_BATCH_SURFACE_ID, {"topic_blocks": blocks})
    # One verdict line per topic — scale the answer budget with the batch so
    # a verbose judge can't truncate the tail into fail-open spawns.
    return resolve_proposal_reviewer().complete(
        prompt, max_tokens=max(1024, 256 * len(items)), cwd=repo_path,
        surface_id=JUDGE_BATCH_SURFACE_ID)


__all__ = ["judge_drift_batch"]
