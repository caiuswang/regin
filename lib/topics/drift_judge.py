"""Batched agentic drift judge: one LLM pass per evolve sweep instead of one
per drifted topic.

The mechanical tiers (wiki anchors, cosine, hash) decide cheaply whether a
change *might* have invalidated a wiki; this judge reads what actually
changed and decides whether each pending refresh is worth drafting. The
prompt hands it evidence *pointers*, not pre-extracted content: the wiki's
path, each ref's baseline commit (`captured_commit`, the repo HEAD stamped at
digest capture) with a one-line change summary, and the vanished wiki
anchors. The judge pulls the rest itself — Read the wiki, `git diff <base>`,
`git log <base>..HEAD` — so nothing is truncated into the prompt and the
commit messages behind a change are in reach.

Consumed by `lib/topics/agent_spawn.maybe_spawn_refresh_agents`, which falls
back to the per-item triage when this judge is unavailable or its answer is
unparseable for a topic (fail open, same as triage). Best-effort throughout —
never raises into the evolve caller.
"""

from __future__ import annotations

import re
import shlex
import sys
from pathlib import Path
from typing import Any, Optional

from lib.activity_log import get_activity_logger
from lib.settings import settings
from lib.topics.drift import _git
from lib.topics.ref_digest import digests_for_topic, repo_id_for_path
from lib.topics.wiki import topic_wiki_page

log = get_activity_logger("topics")


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


def _stat_line(repo_path: str | Path, base: str,
               path: str) -> Optional[str]:
    """One-line `git diff --shortstat <base> -- <path>` magnitude summary;
    None when git can't produce it (unreachable baseline, or no change)."""
    lines = _git(repo_path, ["diff", "--shortstat", base, "--", path])
    return lines[0].strip() if lines else None


def _wiki_pointer(repo_path: str | Path, topic_id: str) -> str:
    page = topic_wiki_page(repo_path, topic_id)
    try:
        if not page.is_file() or not page.read_text(
                encoding="utf-8", errors="replace").strip():
            return "(no wiki on file)"
    except OSError:
        return "(wiki unreadable)"
    try:
        return page.relative_to(Path(repo_path)).as_posix()
    except ValueError:
        return str(page)


def _path_evidence(repo_path: str | Path, item: dict[str, Any],
                   bases: dict[str, Optional[str]]) -> str:
    missing = item.get("missing_anchors") or {}
    parts: list[str] = []
    for path in item.get("drifted_paths") or []:
        gone = missing.get(path)
        cited = (f" — wiki cites {', '.join(f'`{a}`' for a in gone)}, "
                 f"no longer present" if gone else "")
        base = bases.get(path)
        if base:
            stat = _stat_line(repo_path, base, path)
            change = f" — baseline {base} — " + (
                stat if stat else
                "no diff available (read the file as it is now)")
        else:
            change = " — no baseline recorded (read the file as it is now)"
        parts.append(f"- {path}{cited}{change}")
    return "\n".join(parts) or "- (no changed paths recorded)"


def feedback_cmd(subcommand: str) -> str:
    """The quoted prefix of the drift-feedback CLI call the judge appends a
    topic id and reason to. Built like the drafting agent's finish command —
    server interpreter + regin CLI path — so it works regardless of whether
    `regin` is on the agent's PATH."""
    cli = settings.project_root / "cli" / "regin.py"
    return " ".join(shlex.quote(p) for p in
                    (sys.executable, str(cli), "topics", subcommand))


def evidence_context(repo_path: str | Path, topic_id: str,
                     item: dict[str, Any],
                     repo_id: Optional[int]) -> dict[str, str]:
    """The evidence pointers for one topic's drift, shared by the batched
    judge's topic blocks and the per-item triage fallback so both judge the
    same facts: per-path baseline commits + change summaries, and the wiki
    path. `item` carries `drifted_paths` / `missing_anchors`."""
    bases = ({d["path"]: d.get("captured_commit")
              for d in digests_for_topic(repo_id, topic_id)}
             if repo_id is not None else {})
    return {
        "changed_refs": _path_evidence(repo_path, item, bases),
        "wiki_pointer": _wiki_pointer(repo_path, topic_id),
        "repo_root": str(Path(repo_path).resolve()),
    }


def _topic_block(repo_path: str | Path, item: dict[str, Any],
                 repo_id: Optional[int]) -> str:
    topic_id = item["topic_id"]
    evidence = evidence_context(repo_path, topic_id, item, repo_id)
    return (
        f"### topic `{topic_id}`\n\n"
        f"Wiki (the narrative under judgment — Read it): "
        f"{evidence['wiki_pointer']}\n"
        f"Changed refs:\n{evidence['changed_refs']}"
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
    from lib.memory.adapters import resolve_drift_judge
    from lib.prompts import render_surface
    from lib.prompts.surfaces.triage import JUDGE_BATCH_SURFACE_ID

    repo_id = repo_id_for_path(repo_path)
    blocks = "\n\n".join(_topic_block(repo_path, item, repo_id)
                         for item in items)
    prompt = render_surface(JUDGE_BATCH_SURFACE_ID, {
        "topic_blocks": blocks,
        "repo_root": str(Path(repo_path).resolve()),
        "dismiss_cmd": feedback_cmd("drift-dismiss"),
        "note_cmd": feedback_cmd("drift-note"),
    })
    # One verdict line per topic — scale the answer budget with the batch so
    # a verbose judge can't truncate the tail into fail-open spawns.
    return resolve_drift_judge().complete(
        prompt, max_tokens=max(1024, 256 * len(items)), cwd=repo_path,
        surface_id=JUDGE_BATCH_SURFACE_ID)


__all__ = ["judge_drift_batch", "evidence_context", "feedback_cmd"]
