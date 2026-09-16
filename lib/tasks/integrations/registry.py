"""Which provider owns a pasted reference.

The registry is the only place that knows the set of trackers exists. Adding
the second platform is one `register_provider` line plus its module — no
caller re-opens, because every consumer asks the registry for a provider and
then talks to the `IssueTrackerProvider` contract.

Resolution is by *claim*, not by a configured default: each provider is handed
the text and says whether it recognizes it. An ambiguous paste — two providers
claiming the same bare key, which is what happens the day a Jira adapter lands
next to Linear's `ENG-42` — is refused and names the candidates rather than
guessed at, and the caller re-asks with an explicit `provider`. A silent
first-wins pick would make the second tracker unreachable for every key shape
the first one also matches.
"""

from __future__ import annotations

from typing import Callable, Optional

from lib.tasks.integrations.base import (
    ExternalRef, IntegrationError, IssueTrackerProvider,
)
from lib.tasks.integrations.linear import LinearProvider

ProviderBuilder = Callable[[], IssueTrackerProvider]

# regin itself is deliberately absent. A native feature is a `features` row, so
# it has no remote to be asked about and its stage is derived locally; the
# provider that used to answer for one existed only because a feature was a
# task addressed as `regin:<id>`.
_PROVIDER_BUILDERS: dict[str, ProviderBuilder] = {
    LinearProvider.provider_id: LinearProvider,
}


class UnknownRef(IntegrationError):
    """No configured provider claimed the text."""


class AmbiguousRef(IntegrationError):
    """More than one provider claimed it; the caller must name one."""

    def __init__(self, candidates: list[str]) -> None:
        super().__init__(
            "more than one tracker recognizes that reference ("
            + ", ".join(candidates) + ") — say which one")
        self.candidates = candidates


def register_provider(provider_id: str, builder: ProviderBuilder) -> None:
    _PROVIDER_BUILDERS[provider_id] = builder


def provider_ids() -> list[str]:
    return list(_PROVIDER_BUILDERS)


def build_provider(provider_id: str) -> Optional[IssueTrackerProvider]:
    builder = _PROVIDER_BUILDERS.get((provider_id or "").strip().lower())
    return builder() if builder else None


def display_name(provider_id: str) -> str:
    """What to call a tracker in prose, falling back to its own id.

    A provider that has been removed still has rows pointing at it, and naming
    it by id beats naming it not at all — the operator recognizes the word
    they attached with.
    """
    provider = build_provider(provider_id)
    return provider.display_name if provider else (provider_id or "tracker")


def configured_providers() -> list[IssueTrackerProvider]:
    """Providers holding a credential — the ones a paste may resolve to."""
    return [p for p in (builder() for builder in _PROVIDER_BUILDERS.values())
            if p.is_configured()]


def provider_rows() -> list[dict]:
    """Capability rows for the UI: every known tracker, configured or not, so
    an unconfigured one is visibly off rather than absent."""
    rows = []
    for builder in _PROVIDER_BUILDERS.values():
        provider = builder()
        rows.append({"provider": provider.provider_id,
                     "display_name": provider.display_name,
                     "ref_hint": provider.ref_hint,
                     "configured": provider.is_configured()})
    return rows


def stage_is_live(provider_id: str) -> bool:
    """Whether this tracker's stage can lag the fact.

    A registry lookup, never a comparison against a provider id: the caller
    asks what the provider declares, which is what lets a second local tracker
    be live without every surface re-opening.
    """
    provider = build_provider(provider_id)
    return bool(provider and provider.stage_is_live)


def resolve_ref(text: str,
                provider_id: str = "") -> tuple[IssueTrackerProvider,
                                                ExternalRef]:
    """The provider and reference `text` names, or an `IntegrationError`."""
    if provider_id:
        provider = build_provider(provider_id)
        if provider is None:
            raise UnknownRef(f"no tracker named {provider_id!r}")
        if not provider.is_configured():
            raise UnknownRef(f"{provider.display_name} is not configured")
        ref = provider.parse_ref(text)
        if ref is None:
            raise UnknownRef(
                f"{provider.display_name} does not recognize that reference — "
                f"expected {provider.ref_hint}")
        return provider, ref

    claims = [(p, ref) for p in configured_providers()
              if (ref := p.parse_ref(text)) is not None]
    if not claims:
        raise UnknownRef(_no_claim_message())
    if len(claims) > 1:
        raise AmbiguousRef([p.provider_id for p, _ in claims])
    return claims[0]


def name_ref(text: str) -> ExternalRef:
    """Which tracker a reference *names*, without requiring one to be reachable.

    `resolve_ref` asks only configured providers because it is about to make a
    call. Some callers merely record a pointer — `regin defects fix-item
    --issue ENG-42` is the operator asserting where the fix will land, and
    regin never validates that assertion — so demanding a live credential
    there would refuse a write that needs no network at all.

    Ambiguity is refused identically: a shape two trackers both claim must be
    disambiguated by the caller, not guessed.
    """
    claims = [(p, ref) for p in _PROVIDER_BUILDERS.values()
              if (ref := p().parse_ref(text)) is not None]
    if not claims:
        raise UnknownRef(f"no tracker recognizes {text!r}")
    if len(claims) > 1:
        raise AmbiguousRef([p().provider_id for p, _ in claims])
    return claims[0][1]


def _no_claim_message() -> str:
    """Why nothing claimed the text — always a *shape* complaint.

    There is no "nothing is configured" arm any more: regin's own tracker
    holds no credential and is therefore always configured, so the only way
    to reach here is a reference no registered provider recognizes.
    """
    hints = [f"{p.display_name}: {p.ref_hint}" for p in configured_providers()]
    return "no configured tracker recognizes that reference (" \
        + "; ".join(hints) + ")"
