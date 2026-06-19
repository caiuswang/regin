"""Criterion S — source quality: the agent relied on authoritative
sources, not convenient proxies.

This is the defense against concreteness bias: a grader that rewards
authoritative-*looking* citations gets gamed; this one classifies what
the grounding span actually was. The taxonomy:

* repo behavior   — AUTHORITATIVE: a Read of the real file (or the Edit
                    diff itself); PROXY: a README/docs file or a Grep
                    pattern match (proves searching, not content).
* runtime result  — AUTHORITATIVE: a Bash span that ran it.
* external fact   — AUTHORITATIVE: an official-domain fetch (project
                    docs, the library's source); PROXY: search snippets,
                    blogs, Q&A mirrors.
* UNVERIFIED      — the claim names a source but no span proves it was
                    consulted (a hard miss on load-bearing claims).
"""

from __future__ import annotations

import re

from lib.grader.models import (
    AUTHORITATIVE, GROUNDED, PROXY, UNGROUNDED, UNVERIFIED,
    Claim, ClaimVerdict, SourceVerdict,
)

_DOC_PATH_RE = re.compile(
    r"(^|/)(readme|changelog|contributing|docs?/)|\.(md|rst|txt)$",
    re.IGNORECASE)
_PROXY_DOMAIN_RE = re.compile(
    r"stackoverflow\.com|stackexchange\.com|medium\.com|reddit\.com|"
    r"news\.ycombinator\.com|gist\.github\.com|/blog/|blogspot|dev\.to",
    re.IGNORECASE)


def _classify_repo_source(verdict: ClaimVerdict) -> tuple[str, str]:
    if verdict.source_kind in ("edit", "bash", "judge"):
        return AUTHORITATIVE, "backed by recorded span output"
    if verdict.source_kind == "grep":
        return PROXY, ("a Grep pattern match proves searching, not the "
                       "cited content — Read the file")
    if _DOC_PATH_RE.search(verdict.evidence_ref or ""):
        return PROXY, ("backed by documentation, not the code at HEAD — "
                       "Read the real path")
    return AUTHORITATIVE, "backed by a Read of the real path"


def _classify_external_source(verdict: ClaimVerdict) -> tuple[str, str]:
    ref = verdict.evidence_ref or ""
    if _PROXY_DOMAIN_RE.search(ref):
        return PROXY, ("a snippet/mirror/Q&A source — fetch the official "
                       "docs or the library source instead")
    if not ref.startswith("http"):
        return PROXY, ("grounded by a search query, not a fetched "
                       "authoritative page")
    return AUTHORITATIVE, "official-domain fetch"


def _source_for_claim(claim: Claim,
                      verdict: ClaimVerdict) -> SourceVerdict | None:
    if claim.type == "aggregate":
        # c0 is grounded by the coverage checklist, not a source — scoring
        # it would inflate A/M with a fabricated entry
        return None
    if verdict.verdict == GROUNDED:
        if claim.type == "external":
            kind, reason = _classify_external_source(verdict)
        elif claim.type == "result":
            kind, reason = AUTHORITATIVE, "a Bash span actually ran it"
        else:
            kind, reason = _classify_repo_source(verdict)
        return SourceVerdict(claim.id, verdict.evidence_ref or
                             verdict.source_kind, kind, reason)
    if verdict.verdict == UNGROUNDED and claim.referents.get("url"):
        return SourceVerdict(
            claim.id, claim.referents["url"], UNVERIFIED,
            "source named but no span proves it was consulted")
    return None


def assess_sources(claims: list[Claim],
                   verdicts: dict[str, ClaimVerdict]) -> list[SourceVerdict]:
    """Classify the source behind every grounded claim (plus UNVERIFIED
    for named-but-never-consulted sources)."""
    out: list[SourceVerdict] = []
    for claim in claims:
        verdict = verdicts.get(claim.id)
        if verdict is None:
            continue
        source = _source_for_claim(claim, verdict)
        if source is not None:
            out.append(source)
    return out


__all__ = ["assess_sources"]
