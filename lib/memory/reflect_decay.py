"""Lifecycle-decay stage of `reflect()`: chronically-ignored decay, the
pending engagement sweep, and stale file-reference flagging."""

from __future__ import annotations

import re

from lib.activity_log import get_activity_logger
from lib.settings import settings
from lib.memory.reflect_ops import ReflectResult, _doc_text

log = get_activity_logger("memory")


# Decay magnitude per reflect run, and the importance floor it stops at
# (the two triggers and their counts are tunable via `settings.agent_memory`:
# `decay_ignored_threshold` / `decay_injected_threshold`, 0 to disable each).
_IGNORED_DECAY_STEP = 0.1
_IGNORED_DECAY_FLOOR = 0.1
# Validation actions that hard-spare a row from decay — explicit, high-trust
# signals (human approval, distill auto-approval, resurface reinforcement).
# 'engaged' is deliberately NOT here: once the pending sweep densifies the
# signal a single engagement is weak evidence, so engagement spares by *rate*
# (`_engagement_spare`) instead, letting a heavily-ignored memory still decay.
_POSITIVE_ACTIONS = ("approved", "auto_approved", "reinforced")


def _validation_action_counts(store, memory_id: str) -> dict[str, int]:
    """{action: count} from a memory's (trimmed) validation log. Reads the
    model directly — the store keeps no aggregate, and the log is capped at
    a handful of rows, so this is cheap."""
    from sqlmodel import select

    from lib.memory.engine import MemorySessionLocal
    from lib.memory.models import MemoryValidation

    with MemorySessionLocal() as session:
        rows = session.exec(
            select(MemoryValidation.action)
            .where(MemoryValidation.memory_id == memory_id)).all()
    counts: dict[str, int] = {}
    for action in rows:
        counts[action] = counts.get(action, 0) + 1
    return counts


def _decay_spared(mem: dict, counts: dict[str, int]) -> bool:
    """A memory is spared from decay by any earned-usefulness signal — a
    deliberate recall or any `_POSITIVE_ACTIONS` validation — or once it has
    already sunk to the floor (nothing left to take)."""
    if (mem.get("recall_count") or 0) > 0:
        return True
    if any(counts.get(a) for a in _POSITIVE_ACTIONS):
        return True
    return (mem.get("importance") or 0.0) <= _IGNORED_DECAY_FLOOR


def _engagement_spare(engagement: tuple[int, int, int]) -> "bool | None":
    """Rate-based verdict on a memory's recorded engagement, or None when
    there is too little signal to decide (defer to the other triggers).

    Decay is driven by the *decisive* signal — `engaged` vs `hard_ignored`.
    `soft_ignored` (referents matched downstream but only generically, no idf
    credit) is generic contact: it neither credits nor condemns, so it is
    excluded from the rate and treated as benefit-of-the-doubt. This stops the
    idf gate from retiring a memory whose real value leaves no specific
    referent (matched but never idf-engaged) — absence of *specific* evidence
    is not evidence of uselessness.

      True  — engaged/(engaged+hard) ≥ `engage_spare_rate` at ≥
              `engage_min_volume` decisive injects (proven useful); or any
              positive contact (engaged or soft) below the decisive floor.
      False — a low rate *at* decisive volume (proven low-value → decay even
              if the memory once reinforced).
      None  — no scored injects, or only sub-floor hard ignores to go on.
    `engage_spare_rate == 0` disables the rate path entirely (None)."""
    spare_rate = settings.agent_memory.engage_spare_rate
    if not spare_rate:
        return None
    engaged, soft, hard = engagement
    if engaged + soft + hard == 0:
        return None
    decisive = engaged + hard
    if decisive < settings.agent_memory.engage_min_volume:
        return True if (engaged + soft) > 0 else None
    return engaged / decisive >= spare_rate


def _decay_reason(mem: dict, counts: dict[str, int],
                  injection: tuple[int, int],
                  engagement: tuple[int, int, int]) -> "str | None":
    """Why this memory should lose importance this run, or None to spare it.

    Checked in order:
      - hard spares (`_decay_spared`): deliberate recall, an explicit
        high-trust validation, or already at the floor.
      - engaged-*rate* (`_engagement_spare`): the dense always-on signal.
        A high rate spares outright; a low rate at volume *forces*
        `low_engagement` decay (gated once per memory by a prior
        `decayed_low_engagement` validation, so it can't decay every run).
      - `ignored` — `decay_ignored_threshold`+ feedback 'ignored' verdicts.
        Self-limiting: writing `decayed_ignored` evicts an older row from the
        capped log, so the count falls back under threshold until fresh
        ignores arrive. Largely subsumed by the rate path now; kept for the
        grade-time-only signal.
      - `injected` — `decay_injected_threshold`+ auto-injects with zero
        reinforcement, for rows the sweep has no engagement verdict on yet.
        Gated once per memory (a prior `decayed_injected` blocks it).
    Returns the trigger name (used in the validation note) so the triggers
    stay distinguishable in the log."""
    if _decay_spared(mem, counts):
        return None
    spare = _engagement_spare(engagement)
    if spare is not None:
        # The dense engagement signal is strictly better evidence than the
        # raw count thresholds, so when it reaches a verdict it *owns* the
        # decision: a high rate spares; a low rate decays once (then spares,
        # gated by `decayed_low_engagement`) rather than also tripping the
        # injected trigger on the same memory.
        if spare:
            return None
        return None if counts.get("decayed_low_engagement") else "low_engagement"
    return _threshold_decay_reason(counts, injection)


def _threshold_decay_reason(counts: dict[str, int],
                            injection: tuple[int, int]) -> "str | None":
    """The pre-rate count thresholds, for rows the engagement signal can't
    yet decide: enough grade-time 'ignored' verdicts, or enough zero-
    reinforcement injects (each gated/self-limiting; see `_decay_reason`)."""
    ignored_t = settings.agent_memory.decay_ignored_threshold
    if ignored_t and counts.get("ignored", 0) >= ignored_t:
        return "ignored"
    injected, reinforced = injection
    injected_t = settings.agent_memory.decay_injected_threshold
    if (injected_t and reinforced == 0 and injected >= injected_t
            and not counts.get("decayed_injected")):
        return "injected"
    return None


def _score_pending(store, *, dry_run: bool) -> None:
    """Densify the positive signal before decay reads it: stamp engagement
    verdicts on finished, unscored injects so high-engaged memories are
    spared and chronic low-rate ones are caught this same pass. A write, so
    skipped on dry-run; gated by `score_pending_on_reflect`."""
    if dry_run or not settings.agent_memory.score_pending_on_reflect:
        return
    from lib.memory.feedback import (rebuild_session_referent_df,
                                     score_pending_sessions)
    # Refresh the session-span df cache first so the sweep's idf-weighted
    # verdicts read current ubiquity, not last pass's.
    rebuild_session_referent_df(store)
    score_pending_sessions(store)
    _refresh_query_df()


def _refresh_query_df() -> None:
    """Rebuild the topic router's query-log term-frequency cache off the same
    routed-prompt corpus the recall hook logs. Best-effort and isolated: a
    topics-side failure must never break the memory reflect sweep."""
    try:
        from lib.topics.term_weights import rebuild_query_df
        rebuild_query_df(settings.project_root)
    except Exception:  # noqa: BLE001 - cache refresh is non-critical
        log.error("query_df_refresh_failed", exc_info=True)


def _decay_note(reason: str, injection: tuple[int, int],
                engagement: tuple[int, int, int]) -> str:
    """Human-readable provenance for a decay validation, per trigger."""
    if reason == "ignored":
        return "ignored on injection, never recalled"
    if reason == "low_engagement":
        engaged, _soft, hard = engagement
        return f"engaged {engaged}/{engaged + hard} decisive injects, low rate"
    return f"injected {injection[0]}x, never reinforced or recalled"


def _decay_chronically_ignored(store, episodic: list[dict], *, dry_run: bool,
                               result: ReflectResult) -> None:
    """The negative half of the inject→usefulness loop: a memory that earned
    no positive signal and either drew `decay_ignored_threshold`+ 'ignored'
    verdicts or was injected `decay_injected_threshold`+ times without ever
    being reinforced loses `_IGNORED_DECAY_STEP` importance (floored, not
    retired). See `_decay_reason` for the per-trigger cadence guarantees.
    Complements `lib.memory.feedback`'s positive 'engaged' reward."""
    injection = store.injection_counts()
    engagement = store.engagement_match_counts()
    for mem in episodic:
        counts = _validation_action_counts(store, mem["id"])
        reason = _decay_reason(mem, counts, injection.get(mem["id"], (0, 0)),
                               engagement.get(mem["id"], (0, 0, 0)))
        if reason is None:
            continue
        decayed = max(_IGNORED_DECAY_FLOOR,
                      (mem.get("importance") or 0.0) - _IGNORED_DECAY_STEP)
        result.decayed += 1
        result.actions.append(f"decay {reason} {mem['id'][:8]} -> {decayed:.2f}")
        if dry_run:
            continue
        note = _decay_note(reason, injection.get(mem["id"], (0, 0)),
                           engagement.get(mem["id"], (0, 0, 0)))
        store.update(mem["id"], importance=decayed)
        store.record_validation(mem["id"], validator="reflect",
                                action=f"decayed_{reason}", note=note)


# Re-verification: a memory that names a concrete repo file path goes stale
# when the code moves and the path stops resolving — the structural complement
# to `valid_until` time-expiry. We flag it (a 'stale_ref' validation + a
# veracity demote true→unknown), never retire or falsify: a regex + filesystem
# check is a heuristic, and the named code may have merely moved while the
# lesson holds. Bare filenames (no slash) are skipped — too ambiguous to verify
# without false hits; precision over recall, as elsewhere in this store.
_REF_PATH_RE = re.compile(
    r"\b([\w.-]+(?:/[\w.-]+)+\."
    r"(?:py|vue|js|ts|tsx|jsx|md|sql|sh|toml|ya?ml|css|html|json))\b")


def _referenced_paths(mem: dict) -> set[str]:
    """Concrete repo file paths named in a memory (slash + known extension)."""
    return set(_REF_PATH_RE.findall(_doc_text(mem)))


def _repo_root_for_scope(scope: "str | None") -> "str | None":
    """Filesystem root of a `repo:<name>` scope (basename match against the
    registered repos), or None for global / unregistered scopes — whose
    references can't be verified, so the memory is left untouched."""
    import os
    if not scope or not scope.startswith("repo:"):
        return None
    name = scope.split(":", 1)[1]
    for repo_path in settings.repo_paths:
        root = os.path.realpath(str(repo_path))
        if os.path.basename(root) == name:
            return root
    return None


def _missing_refs(root: str, paths: set[str]) -> list[str]:
    import os
    return sorted(p for p in paths
                  if not os.path.exists(os.path.join(root, p)))


def _rename_follow(store, root: str, mem: dict, missing: list[str], *,
                   dry_run: bool, result: ReflectResult) -> list[str]:
    """High-confidence half of stale-ref handling: for missing paths that git
    history shows were *renamed* (not deleted), rewrite the memory body to the
    new path and leave veracity untouched — a rename is relocation, not
    staleness. Returns the residual genuinely-missing paths for the caller to
    flag. A no-op (returns `missing` unchanged) unless `mechanical_autoapply`
    is on; best-effort, so a git failure can't break the reflect pass."""
    if not settings.topic_evolution.mechanical_autoapply:
        return missing
    try:
        from lib.topics.drift import renames_from_history, rewrite_memory_body
        renames = renames_from_history(root, set(missing))
        if not renames:
            return missing
        if rewrite_memory_body(store, mem, renames, dry_run=dry_run):
            result.ref_renames += 1
            result.actions.append(
                f"rename-follow {mem['id'][:8]}: {len(renames)} path(s)")
        return [p for p in missing if p not in renames]
    except Exception:  # noqa: BLE001 - rename-follow must not break reflect
        log.error("reflect_rename_follow_failed", exc_info=True)
        return missing


def _check_one_stale(store, mem: dict, *, dry_run: bool,
                     result: ReflectResult) -> None:
    """Flag one memory if a concrete path it names no longer resolves. Skips
    rows already flagged (idempotency), unverifiable scopes, and rows with no
    path references or no missing paths. A renamed (not deleted) path is
    rewritten in place first — only the genuinely-deleted residual is flagged."""
    paths = _referenced_paths(mem)
    if not paths:
        return
    root = _repo_root_for_scope(mem.get("scope"))
    if root is None or _validation_action_counts(
            store, mem["id"]).get("stale_ref"):
        return
    missing = _missing_refs(root, paths)
    if not missing:
        return
    missing = _rename_follow(store, root, mem, missing,
                             dry_run=dry_run, result=result)
    if not missing:
        return
    result.flagged_stale += 1
    result.actions.append(
        f"stale ref {mem['id'][:8]}: {', '.join(missing[:3])}")
    if dry_run:
        return
    if mem.get("veracity") == "true":
        store.update(mem["id"], veracity="unknown")
    store.record_validation(mem["id"], validator="reflect", action="stale_ref",
                            note=f"unresolved path(s): {', '.join(missing[:5])}")


def _flag_stale_references(store, rows: list[dict], *, dry_run: bool,
                           result: ReflectResult) -> None:
    """The structural half of memory hygiene: flag active memories whose named
    repo file paths no longer exist, for human review at /memory. A no-op
    unless `verify_stale_refs` is on (filesystem-touching, off by default)."""
    if not settings.agent_memory.verify_stale_refs:
        return
    for mem in rows:
        _check_one_stale(store, mem, dry_run=dry_run, result=result)
