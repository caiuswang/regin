"""Build the `<skill_experience>` block for an invoked skill.

The shared core behind both delivery paths:

  * slash-command invocation (`/playwright-screenshots …`), detected by the
    UserPromptSubmit recall handler (`hook_manager/handlers/memory_recall.py`);
  * the assistant calling the `Skill` tool directly (an *auto-invoked* skill
    like topic-router), caught by a PreToolUse handler
    (`hook_manager/handlers/skill_experience.py`).

Both resolve a skill id to its `skill-<id>` meta-leaf (lib/topics/meta_roots),
recall the memories filed there, and render one block — so the two paths can
never drift. Provider-neutral: no HookPayload here, just the skill id + the
session id used to record the injection for engagement feedback.
"""

from __future__ import annotations

from typing import Optional

# Body cap for one injected-memory line; shared with `<recalled_experience>`.
ENTRY_MAX_CHARS = 400


def age_suffix(m: dict) -> str:
    """Compact relative age (', 3d old') from updated_at/created_at, or '' when
    the stamp is absent or unparseable so a block never breaks on it."""
    stamp = m.get("updated_at") or m.get("created_at")
    if not stamp:
        return ""
    try:
        from datetime import datetime
        then = datetime.fromisoformat(stamp)
        age_hours = max(0.0, (datetime.now() - then).total_seconds()) / 3600.0
    except Exception:
        return ""
    if age_hours < 1:
        return ", fresh"
    if age_hours < 24:
        return f", {int(age_hours)}h old"
    if age_hours < 24 * 60:
        return f", {int(age_hours / 24)}d old"
    return f", {int(age_hours / (24 * 30))}mo old"


def format_memory_line(m: dict) -> str:
    """One injected-memory line — the shared renderer for both
    `<recalled_experience>` and `<skill_experience>`."""
    title = f"{m['title']}: " if m.get("title") else ""
    body = m["body"]
    if len(body) > ENTRY_MAX_CHARS:
        body = body[:ENTRY_MAX_CHARS] + "…"
    return f"- [{m['kind']}] {title}{body} (memory {m['id'][:8]}{age_suffix(m)})"


def leaf_id_for_skill(skill_id: str) -> Optional[str]:
    """The `skill-<id>` meta-leaf id for a skill, or None for a blank id. The
    leading slash of a slash command is stripped first."""
    sid = (skill_id or "").strip().lstrip("/")
    return f"skill-{sid}" if sid else None


def _skill_memories(leaf_id: str, cfg) -> list[dict]:
    """Active memories filed under a skill meta-leaf, importance-ranked and
    top-k capped. [] when `leaf_id` is not a known skill node, so a skill with
    no meta-leaf injects nothing."""
    import lib.memory as memory
    from lib.topics.meta_roots import load_global_meta_topics
    if leaf_id not in load_global_meta_topics():
        return []
    store = memory.get_store()
    out = []
    for mid in store.memories_for_topic_subtree([leaf_id], scope=None):
        m = store.get_dict(mid)
        if m:
            out.append(m)
        if len(out) >= cfg.inject_top_k:
            break
    return out


def _build_block(skill_name: str, mems: list[dict], max_chars: int) -> str:
    lines = [
        "<skill_experience>",
        f"Past-session lessons filed under the `{skill_name}` skill. May be",
        "stale — verify against the current code before relying on it.",
    ]
    budget = (max_chars - sum(len(l) + 1 for l in lines)
              - len("</skill_experience>"))
    for m in mems:
        entry = format_memory_line(m)
        if len(entry) + 1 > budget:
            break
        lines.append(entry)
        budget -= len(entry) + 1
    lines.append("</skill_experience>")
    return "\n".join(lines)


def _record_injection(session_id: str, mems: list[dict], query: str) -> None:
    """Record skill-memory injections so the engagement-feedback loop can score
    their usefulness (the signal `consolidate-skills` reads). Best-effort."""
    if not session_id:
        return
    try:
        import lib.memory as memory
        memory.get_store().record_injections(
            session_id, [m["id"] for m in mems], query=(query or "")[:2000])
    except Exception:
        pass


def skill_experience_block(skill_id: str, session_id: Optional[str], *,
                           query: Optional[str] = None) -> str:
    """The `<skill_experience>` block for `skill_id`, or '' when the feature is
    disabled, the skill has no meta-leaf, or nothing is filed under it. Records
    the injection as a side effect when a block is produced."""
    from lib.settings import settings
    cfg = settings.agent_memory
    if not (cfg.enabled and cfg.auto_inject and cfg.skill_experience_inject):
        return ""
    leaf_id = leaf_id_for_skill(skill_id)
    if not leaf_id:
        return ""
    try:
        mems = _skill_memories(leaf_id, cfg)
    except Exception:
        return ""
    if not mems:
        return ""
    _record_injection(session_id or "", mems, query or skill_id)
    return _build_block(leaf_id[len("skill-"):], mems,
                        cfg.skill_experience_max_chars)


__all__ = ["skill_experience_block", "format_memory_line", "age_suffix",
           "leaf_id_for_skill", "ENTRY_MAX_CHARS"]
