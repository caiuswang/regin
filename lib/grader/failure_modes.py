"""Failure-mode taxonomy — the bridge from one session's grade `detail`
to stable, countable *mode keys* the aggregator buckets across sessions.

A mode key is a coarse, recurrence-friendly label for a kind of mistake
(`claim:state:UNGROUNDED`, `coverage:MISSING`, `process:WASTED`) — the
unit Slice 3 counts. Two sessions that both assert file state without a
backing Read collapse to the same `claim:state:UNGROUNDED` key, which is
what lets a recurring weakness rise above per-session noise.

`MODE_LABELS` / `REMEDIATION` turn a key back into human prose for the
consolidated lesson; unknown keys degrade to the key itself.
"""

from __future__ import annotations

MODE_LABELS: dict[str, str] = {
    "coverage:MISSING": "required checklist items left unaddressed",
    "coverage:PARTIAL": "checklist items touched but never grounded",
    "source:PROXY": "load-bearing claims backed only by a proxy source",
    "source:UNVERIFIED": "claims whose cited source was never consulted",
    "process:WASTED": "tool calls whose output fed nothing downstream",
    "process:SUBOPTIMAL": "a cheaper tool existed for the job",
    "process:ignored_error_feeds_claim":
        "an ignored error fed a final claim",
}

# Concrete, behaviour-changing advice per mode — the "what to do instead"
# that makes the consolidated lesson actionable rather than a tally.
REMEDIATION: dict[str, str] = {
    "claim:state": "Read or Grep the file in-session and quote it before "
                   "asserting what the code does; an unbacked state claim "
                   "grades UNGROUNDED.",
    "claim:result": "Re-run the verifying command after the last edit and "
                    "cite its exit code/output; a result claim staled by a "
                    "later mutation grades STALE.",
    "claim:external": "Actually WebFetch/WebSearch the API or library before "
                      "claiming how it behaves; an unconsulted external "
                      "claim grades UNGROUNDED.",
    "claim:diagnostic": "Show both cause and effect from the trace (the "
                        "Read that found it and the run that confirms it) "
                        "before stating a root cause.",
    "coverage:MISSING": "Address every sub-task the user asked for, "
                        "including implied ones (root cause, applied fix, "
                        "green verification), not just the easy ones.",
    "coverage:PARTIAL": "Don't just touch a required item — ground it: make "
                        "a claim a span backs, or the item stays PARTIAL.",
    "source:PROXY": "Prefer the authoritative source (the file/run itself) "
                    "over a blog/memory/second-hand mention.",
    "source:UNVERIFIED": "Consult the source you cite; don't assert it from "
                         "memory.",
    "process:WASTED": "Don't run tools whose output you won't use; each "
                      "wasted call inflates cost without informing a "
                      "decision.",
    "process:SUBOPTIMAL": "Reach for the dedicated tool (Grep over "
                          "`cat | grep`, Read over `cat`) — it is cheaper "
                          "and cleaner.",
    "process:ignored_error_feeds_claim":
        "Never build a conclusion on a command that errored; handle or "
        "re-run it first.",
}


def label_for(mode: str) -> str:
    """Human description for a mode key, falling back gracefully."""
    if mode in MODE_LABELS:
        return MODE_LABELS[mode]
    if mode.startswith("claim:"):
        _, ctype, verdict = mode.split(":", 2)
        return f"{ctype} claims that graded {verdict}"
    if mode.startswith("process:redundancy:"):
        return f"redundant {mode.rsplit(':', 1)[-1]} episodes"
    return mode


def remediation_for(mode: str) -> str:
    """Behaviour-changing advice for a mode key, or '' when none is mapped.
    Claim modes key off the `claim:<type>` prefix (verdict-independent)."""
    if mode in REMEDIATION:
        return REMEDIATION[mode]
    if mode.startswith("claim:"):
        return REMEDIATION.get(":".join(mode.split(":")[:2]), "")
    if mode.startswith("process:redundancy:"):
        return ("Cache what you already read/derived; re-reading the same "
                "file or re-deriving the same fact wastes turns.")
    return ""


def _weak_claim(claim: dict, verdict: dict, cid: str) -> bool:
    return (cid != "c0" and verdict.get("verdict") != "GROUNDED"
            and claim.get("load_bearing", True))


def _claim_modes(detail: dict, out: dict[str, str]) -> None:
    by_id = {c.get("id"): c for c in (detail.get("claims") or [])}
    for cid, v in (detail.get("verdicts") or {}).items():
        claim = by_id.get(cid) or {}
        if _weak_claim(claim, v, cid):
            mode = f"claim:{claim.get('type', 'state')}:{v.get('verdict')}"
            out.setdefault(mode, (claim.get("normalized_text")
                                  or claim.get("raw_text") or "")[:120])


def _coverage_modes(detail: dict, out: dict[str, str]) -> None:
    for item in detail.get("checklist") or []:
        if item.get("verdict") in ("MISSING", "PARTIAL"):
            out.setdefault(f"coverage:{item.get('verdict')}",
                           str(item.get("item"))[:120])


def _source_modes(detail: dict, out: dict[str, str]) -> None:
    for src in detail.get("sources") or []:
        if src.get("verdict") in ("PROXY", "UNVERIFIED"):
            out.setdefault(f"source:{src.get('verdict')}",
                           str(src.get("source"))[:120])


def _correctness_modes(detail: dict, out: dict[str, str]) -> None:
    _claim_modes(detail, out)
    _coverage_modes(detail, out)
    _source_modes(detail, out)


def _process_modes(detail: dict, out: dict[str, str]) -> None:
    for f in (detail.get("tool_use") or {}).get("findings", []):
        if f.get("verdict") in ("SUBOPTIMAL", "WASTED"):
            out.setdefault(f"process:{f.get('verdict')}",
                           str(f.get("reason"))[:120])
    for kind, eps in (detail.get("redundancy") or {}).items():
        if eps:
            out.setdefault(f"process:redundancy:{kind}",
                           f"{len(eps)} episode(s)")
    if (detail.get("reliability") or {}).get("ignored_feeding_claim"):
        out.setdefault("process:ignored_error_feeds_claim",
                       "an errored command fed a final claim")


def session_modes(grades: dict) -> dict[str, str]:
    """Map one session's `{axis: grade_dict}` (each with `detail`) to
    `{mode_key: representative_example}`. Only problem modes appear; a
    clean axis contributes nothing. One example per mode (first seen)."""
    out: dict[str, str] = {}
    for axis, grade in (grades or {}).items():
        detail = (grade or {}).get("detail") or {}
        if axis == "correctness":
            _correctness_modes(detail, out)
        elif axis == "process":
            _process_modes(detail, out)
    return out


__all__ = ["MODE_LABELS", "REMEDIATION", "label_for", "remediation_for",
           "session_modes"]
