"""Linear adapter — the first `IssueTrackerProvider`.

One GraphQL round trip per fetch, against a personal API key held in
`settings.task_integrations.linear_api_key`. Linear's `issue(id:)` resolves
the human identifier (`ENG-42`) as well as the uuid, so the identifier is both
the dedup key and the query argument and nothing has to be resolved twice.

Everything vendor-shaped stops in this file: the priority integers, the
`state.type` vocabulary, the `labels.nodes` envelope. What leaves is an
`ExternalIssue`.

`stateHistory` rides the same query rather than a second round trip. It is a
native Linear field (`IssueStateSpanConnection`, confirmed by introspecting the
schema, not assumed), and it is the only reliable source for rounds-to-done:
reopening an issue *clears* `completedAt`, so the current row cannot tell a
feature that was right first time from one reopened and re-completed.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Optional

from lib.defects import rounds
from lib.settings import settings
from lib.tasks.integrations.base import (
    ExternalIssue, ExternalRef, IntegrationError, IssueTrackerProvider,
    NotConfigured, STATE_CATEGORIES, STATE_UNKNOWN, clamp_description,
)

API_URL = "https://api.linear.app/graphql"

# `ENG-42`, anywhere in a pasted URL or on its own.
_KEY_RE = re.compile(r"\b([A-Za-z][A-Za-z0-9_]*)-(\d+)\b")
# The identifier is read from the path segment after `/issue/`, never from the
# URL at large: a workspace slug is user-chosen and routinely ends in digits
# (`linear.app/my-team-1/issue/ENG-42`), so a loose search over the whole URL
# claims `TEAM-1` and silently links a different, existing issue.
_ISSUE_URL_RE = re.compile(
    r"^https?://(?:www\.)?linear\.app/[^/]+/issue/([^/?#]+)", re.IGNORECASE)

# Linear ships priority as an int; `priorityLabel` is localized, so the number
# is what we map — onto regin's own priority words, so one vocabulary sorts
# both a task and the issue behind it.
_PRIORITY = {0: None, 1: "urgent", 2: "high", 3: "medium", 4: "low"}

_QUERY = """
query ReginIssue($id: String!) {
  issue(id: $id) {
    identifier
    title
    description
    url
    updatedAt
    priority
    state { name type }
    assignee { displayName }
    labels { nodes { name } }
    team { key name }
    project { name }
    stateHistory { nodes { startedAt endedAt state { name type } } }
  }
}
"""


# `issueCreate` needs the parent's uuid and its team's uuid; the read query
# returns neither, so filing costs one extra round trip.
_PARENT_QUERY = """
query ReginParent($id: String!) { issue(id: $id) { id team { id } } }
"""

_CREATE_MUTATION = """
mutation ReginCreate($input: IssueCreateInput!) {
  issueCreate(input: $input) { issue { identifier url } }
}
"""

# Linear accepts long titles; a defect symptom is free prose and can run to
# paragraphs, which makes an unreadable board row.
_TITLE_MAX = 240


class LinearProvider(IssueTrackerProvider):
    provider_id = "linear"
    display_name = "Linear"
    ref_hint = "a linear.app issue URL or an identifier like ENG-42"

    def is_configured(self) -> bool:
        cfg = settings.task_integrations
        return bool(cfg.linear_enabled and (cfg.linear_api_key or "").strip())

    def parse_ref(self, text: str) -> Optional[ExternalRef]:
        """Claim a linear.app issue URL, or a bare `ENG-42` identifier.

        A bare identifier is only claimed when the text is *nothing but* the
        identifier: `ABC-1` also matches a Jira key and half the ticket
        vocabularies that will follow, so an ambiguous claim here would make
        the second provider unreachable. A full Linear URL is unambiguous and
        wins outright.
        """
        raw = (text or "").strip()
        if not raw:
            return None
        in_url = _ISSUE_URL_RE.match(raw)
        if in_url:
            match = _KEY_RE.fullmatch(in_url.group(1))
            # The pasted URL is kept verbatim rather than rebuilt: it is
            # workspace-qualified and therefore actually resolves, which the
            # card still needs on the path where the fetch failed.
            return self._ref(match, raw) if match else None
        match = _KEY_RE.fullmatch(raw)
        return self._ref(match) if match else None

    def _ref(self, match: re.Match, url: str = "") -> ExternalRef:
        """A bare identifier carries no workspace, so its URL is synthesized
        and then replaced by the canonical one the first fetch returns."""
        key = f"{match.group(1).upper()}-{match.group(2)}"
        return ExternalRef(provider=self.provider_id, key=key,
                           url=url or f"https://linear.app/issue/{key}")

    def fetch_issue(self, ref: ExternalRef) -> ExternalIssue:
        if not self.is_configured():
            raise NotConfigured(
                "Linear is not configured — set task_integrations."
                "linear_api_key and enable it in Settings.")
        node = self._issue_node(ref.key)
        return self._to_issue(ref, node)

    def _issue_node(self, key: str) -> dict:
        payload = self._post({"query": _QUERY, "variables": {"id": key}})
        self._raise_errors(payload, "Linear refused the query")
        node = (payload.get("data") or {}).get("issue")
        if not node:
            raise IntegrationError(f"Linear has no issue {key}.")
        return node

    def create_issue(self, *, parent: ExternalRef, title: str,
                     body: str) -> ExternalRef:
        """File a sub-issue of `parent`, carrying the symptom verbatim."""
        if not self.is_configured():
            raise NotConfigured(
                "Linear is not configured — set task_integrations."
                "linear_api_key and enable it in Settings.")
        parent_id, team_id = self._parent_ids(parent)
        payload = self._post({
            "query": _CREATE_MUTATION,
            "variables": {"input": {
                "teamId": team_id, "parentId": parent_id,
                "title": title[:_TITLE_MAX], "description": body}}})
        self._raise_errors(payload, "Linear refused the issue")
        created = ((payload.get("data") or {}).get("issueCreate") or {}
                   ).get("issue")
        if not created:
            raise IntegrationError("Linear created no issue.")
        return ExternalRef(provider=self.provider_id,
                           key=str(created["identifier"]),
                           url=str(created.get("url") or ""))

    def _parent_ids(self, parent: ExternalRef) -> tuple[str, str]:
        """`issueCreate` takes uuids, not the human identifier `issue(id:)`
        accepts on the read side — hence one extra round trip to file."""
        payload = self._post({"query": _PARENT_QUERY,
                              "variables": {"id": parent.key}})
        node = (payload.get("data") or {}).get("issue") or {}
        team = (node.get("team") or {}).get("id")
        if not node.get("id") or not team:
            raise IntegrationError(
                f"Linear could not resolve {parent.key} to file under.")
        return str(node["id"]), str(team)

    @staticmethod
    def _raise_errors(payload: dict, prefix: str) -> None:
        errors = payload.get("errors")
        if errors:
            raise IntegrationError(
                prefix + ": "
                + "; ".join(str(e.get("message", e)) for e in errors[:3]))

    def _post(self, body: dict) -> dict:
        cfg = settings.task_integrations
        request = urllib.request.Request(
            API_URL, data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": (cfg.linear_api_key or "").strip()},
            method="POST")
        try:
            with urllib.request.urlopen(
                    request, timeout=cfg.linear_timeout_seconds) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise IntegrationError(
                    "Linear rejected the API key (HTTP "
                    f"{exc.code}).") from None
            raise IntegrationError(
                f"Linear returned HTTP {exc.code}.",
                retryable=exc.code >= 500) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise IntegrationError(f"Linear is unreachable: {exc}",
                                   retryable=True) from None
        except json.JSONDecodeError:
            raise IntegrationError(
                "Linear returned a body that is not JSON.") from None

    def _to_issue(self, ref: ExternalRef, node: dict) -> ExternalIssue:
        state = node.get("state") or {}
        return ExternalIssue(
            provider=self.provider_id,
            key=_text(node.get("identifier"), ref.key),
            url=_text(node.get("url"), ref.url),
            title=_text(node.get("title"), ref.key),
            state=_text(state.get("name"), ""),
            state_category=_category(state.get("type")),
            priority=_PRIORITY.get(node.get("priority")),
            assignee=(node.get("assignee") or {}).get("displayName"),
            labels=_labels(node),
            description=clamp_description(node.get("description")),
            updated_at=node.get("updatedAt"),
            rounds=rounds.from_state_history(
                (node.get("stateHistory") or {}).get("nodes")),
            extra=_extra(node),
        )


def _text(value, fallback: str) -> str:
    return str(value) if value else fallback


def _category(raw) -> str:
    """Linear's `state.type`, or `unknown` for a vocabulary we do not carry —
    a tracker adding a seventh category must not poison the stored snapshot."""
    value = str(raw or "").lower()
    return value if value in STATE_CATEGORIES else STATE_UNKNOWN


def _labels(node: dict) -> tuple[str, ...]:
    nodes = (node.get("labels") or {}).get("nodes") or []
    return tuple(str(n.get("name")) for n in nodes if n.get("name"))


def _extra(node: dict) -> dict:
    """The facts only Linear has, as opaque display rows."""
    pairs = (("Team", (node.get("team") or {}).get("name")),
             ("Project", (node.get("project") or {}).get("name")))
    return {label: value for label, value in pairs if value}
