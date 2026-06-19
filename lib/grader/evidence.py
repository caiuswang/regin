"""Evidence index — the bridge from a regin trace to gradeable evidence.

Every `tool_use` span and its recorded output is a citable source: Read
spans carry file content, Bash spans carry command/stdout/stderr, Edit
spans carry diffs, WebFetch spans carry the URL consulted. The grader's
unit of work is *"for every assertion, find the span that backs it"* —
this module builds the indexes that make that lookup cheap, and records
span order so the staleness rule (evidence read before a later mutation)
can be checked from the timeline alone.

Failure shape: hook-captured sessions record a failed call as a
`tool.failure` span whose real tool lives in `attrs.tool_name` (status
ERROR, error text in `attrs.error`); workflow-ingested sessions record
`tool.<Name>` with status ERROR directly. Both shapes resolve to the same
ToolEvent here, so failure polarity is visible regardless of capture path.

Tool I/O here is evidence, not claims; the artifact the claims are
extracted from is the final deliverable: the assistant messages after the
last prompt, plus agent-authored commit messages found in git-commit
Bash spans.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from lib.activity_log import get_activity_logger

log = get_activity_logger("grader")

# Tools whose spans mutate files — used by the STALE rule.
MUTATING_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit",
                            "apply_patch"})
READ_TOOLS = frozenset({"Read"})
SEARCH_TOOLS = frozenset({"Grep", "Glob"})
FETCH_TOOLS = frozenset({"WebFetch", "WebSearch"})

# Doc-ish paths: their mutation must not mark a verification run stale —
# editing a README after the test run doesn't invalidate the run.
_DOC_PATH_RE = re.compile(
    r"(^|/)(readme|changelog|memory|notes?)[^/]*$|\.(md|rst|txt)$",
    re.IGNORECASE)

# Test paths: a *newly added* test file doesn't change the product
# behavior a verification run checked, so — like a doc edit — it must not
# mark that run stale. (Editing pre-existing code, including a test the
# run actually exercised, still counts.)
_TEST_PATH_RE = re.compile(
    r"(^|/)tests?/|(^|/)test_[^/]*$|_test\.[a-z0-9]+$|\.(spec|test)\.[a-z0-9]+$",
    re.IGNORECASE)

# Caps for tokenized slices: recorded outputs reach 64-256KB; matching
# works fine on a prefix and tokenizing whole bodies per claim is the
# difference between O(n) and gigabytes of regex scanning.
_TOKEN_SLICE = 4000


@dataclass
class ToolEvent:
    """One resolved tool span, in timeline order."""

    index: int               # position in the ordered span list
    span_id: str
    tool: str                # 'Read', 'Bash', ... ('' for non-tool spans)
    start_time: str
    status: str              # 'OK' | 'ERROR' | 'UNSET'
    attrs: dict
    agent_id: str | None = None   # set when a subagent emitted the span

    @property
    def is_error(self) -> bool:
        return self.status == "ERROR"

    @property
    def file_path(self) -> str:
        return str(self.attrs.get("file_path") or "")

    @property
    def command(self) -> str:
        return str(self.attrs.get("command")
                   or self.attrs.get("command_preview") or "")

    @property
    def stdout(self) -> str:
        return str(self.attrs.get("stdout") or "")

    @property
    def stderr(self) -> str:
        # tool.failure spans carry the failure text under `error`
        return str(self.attrs.get("stderr") or self.attrs.get("error") or "")

    @property
    def content(self) -> str:
        return str(self.attrs.get("content") or "")

    @property
    def diff(self) -> str:
        return str(self.attrs.get("diff") or "")

    @property
    def read_range(self) -> dict:
        """Line-range metadata for a Read span (`start_line`/`num_lines`/
        `total_lines`), omitting keys the span didn't record; empty for
        non-Read spans. Surfaced in the trace dump so a reviewer can tell a
        chunked walk through a large file from a true re-read — without it
        repeated reads of one path look identical and read as redundant."""
        keys = ("start_line", "num_lines", "total_lines")
        return {k: self.attrs[k] for k in keys
                if isinstance(self.attrs.get(k), int)}

    def match_tokens(self) -> set[str]:
        """Memoized token set over this event's capped output slices."""
        cached = self.attrs.get("_match_tokens")
        if cached is None:
            cached = content_tokens(" ".join((
                self.command[:_TOKEN_SLICE], self.stdout[:_TOKEN_SLICE],
                self.stderr[:1000], self.content[:_TOKEN_SLICE],
                self.diff[:_TOKEN_SLICE], self.file_path,
                str(self.attrs.get("pattern") or ""),
                str(self.attrs.get("url") or ""))))
            self.attrs["_match_tokens"] = cached
        return cached


def _is_added_file(events: list[ToolEvent]) -> bool:
    """True when a path was first *created* this session — a Write span, or
    a diff that starts at line 0 (`@@ -0,0`). Distinguishes a brand-new
    file from an edit to a pre-existing one."""
    return any(e.tool == "Write" or e.diff.lstrip().startswith("@@ -0,0")
               for e in events)


@dataclass
class EvidenceIndex:
    """All evidence for one session, indexed for the graders."""

    trace_id: str
    task_text: str = ""
    prompt_texts: list[str] = field(default_factory=list)
    final_text: str = ""
    assistant_texts: list[str] = field(default_factory=list)
    commit_messages: list[str] = field(default_factory=list)
    events: list[ToolEvent] = field(default_factory=list)
    reads: dict[str, list[ToolEvent]] = field(default_factory=dict)
    mutations: dict[str, list[ToolEvent]] = field(default_factory=dict)
    bash: list[ToolEvent] = field(default_factory=list)
    searches: list[ToolEvent] = field(default_factory=list)
    fetches: list[ToolEvent] = field(default_factory=list)
    session: dict = field(default_factory=dict)   # sessions-table row

    def artifact_text(self) -> str:
        """The graded artifact: final deliverable text + agent-authored
        commit messages (prose in the diff, §13.1 primary tier)."""
        parts = [self.final_text] + self.commit_messages
        return "\n".join(p for p in parts if p)

    def full_task_text(self) -> str:
        """Every user prompt, for checklist derivation — the user's words
        are fixed input, so this stays anti-gaming-safe."""
        return "\n".join(self.prompt_texts)[:8000] or self.task_text

    def mutations_after(self, path: str, index: int) -> list[ToolEvent]:
        """Mutating spans touching `path` after timeline position `index`."""
        return [e for e in self.mutations.get(path, []) if e.index > index]

    def mutations_of(self, path: str) -> list[ToolEvent]:
        """Mutating spans whose file matches `path` on component boundaries
        (claims cite suffixes: `projection.py` matches
        `lib/trace/projection.py`). Mirror of `reads_of` for the edit side."""
        if not path:
            return []
        suffix = "/" + path.lstrip("/")
        hits: list[ToolEvent] = []
        for full, events in self.mutations.items():
            if full == path or full.endswith(suffix):
                hits.extend(events)
        return sorted(hits, key=lambda e: e.index)

    def last_mutation_index(self) -> int:
        """Timeline position of the last file mutation, or -1."""
        all_mut = [e.index for evs in self.mutations.values() for e in evs]
        return max(all_mut) if all_mut else -1

    def last_code_mutation_index(self) -> int:
        """Like last_mutation_index, but excludes mutations that can't
        invalidate a prior verification run: doc-ish paths, and test files
        first created this session (a new regression test doesn't change
        the product behavior the run checked)."""
        idxs: list[int] = []
        for path, evs in self.mutations.items():
            if _DOC_PATH_RE.search(path):
                continue
            if _TEST_PATH_RE.search(path) and _is_added_file(evs):
                continue
            idxs.extend(e.index for e in evs)
        return max(idxs) if idxs else -1

    def reads_of(self, path: str) -> list[ToolEvent]:
        """Read events matching `path` on component boundaries (claims
        cite suffixes: `login.ts` matches `src/auth/login.ts`, not
        `latest.ts`)."""
        suffix = "/" + path.lstrip("/")
        hits: list[ToolEvent] = []
        for full, events in self.reads.items():
            if full == path or full.endswith(suffix):
                hits.extend(events)
        return sorted(hits, key=lambda e: e.index)


def _span_status(span: dict) -> str:
    code = span.get("status_code") or "UNSET"
    return code if code in ("OK", "ERROR") else "UNSET"


def _tool_name(span: dict) -> str:
    name = span.get("name") or ""
    if not name.startswith("tool."):
        return ""
    tool = name[len("tool."):]
    if tool == "failure":
        # hook-captured failures: the real tool is in the attributes
        attrs = span.get("attributes") or {}
        return str(attrs.get("tool_name") or "failure")
    return tool


def _index_tool_event(idx: ToolEvent, out: "EvidenceIndex") -> None:
    tool = idx.tool
    if tool in READ_TOOLS and idx.file_path:
        out.reads.setdefault(idx.file_path, []).append(idx)
    elif tool in MUTATING_TOOLS and idx.file_path:
        out.mutations.setdefault(idx.file_path, []).append(idx)
    elif tool == "Bash":
        out.bash.append(idx)
    elif tool in SEARCH_TOOLS:
        out.searches.append(idx)
    elif tool in FETCH_TOOLS:
        out.fetches.append(idx)


def _collect_texts(span: dict, out: "EvidenceIndex",
                   sub_texts: list[str]) -> None:
    name = span.get("name") or ""
    attrs = span.get("attributes") or {}
    text = str(attrs.get("text") or "")
    if not text:
        return
    if name == "prompt":
        out.prompt_texts.append(text)
        if not out.task_text:
            out.task_text = text
        out.assistant_texts.append("")   # marks a turn boundary
    elif name == "assistant_response":
        if attrs.get("agent_id"):
            sub_texts.append(text)
        else:
            out.assistant_texts.append(text)


_COMMIT_MSG_RE = re.compile(r"""git\s+commit[^\n]*?-m\s+(['"])(.+?)\1""",
                            re.DOTALL)


def _collect_commit_messages(out: "EvidenceIndex") -> None:
    for event in out.bash:
        for _, msg in _COMMIT_MSG_RE.findall(event.command):
            out.commit_messages.append(msg.strip()[:2000])


def _final_deliverable(out: "EvidenceIndex", sub_texts: list[str]) -> str:
    """The assistant messages after the last prompt, joined. Workflow
    sessions have no main-conversation responses — fall back to the last
    subagent response rather than grading a phantom empty artifact."""
    tail: list[str] = []
    for text in reversed(out.assistant_texts):
        if text == "":   # turn boundary marker
            if tail:
                break
            continue
        tail.append(text)
    out.assistant_texts = [t for t in out.assistant_texts if t]
    if tail:
        return "\n\n".join(reversed(tail))
    return sub_texts[-1] if sub_texts else ""


def _load_session_row(trace_id: str) -> dict:
    from lib.orm import SessionLocal
    from lib.orm.models.trace import Session as SessionRow

    with SessionLocal() as db:
        row = db.get(SessionRow, trace_id)
        return row.model_dump() if row is not None else {}


def build_evidence(trace_id: str, spans: list[dict] | None = None) -> EvidenceIndex:
    """Build the evidence index for one session.

    `spans` may be injected (tests); otherwise the merged projection is
    fetched, so placeholders are already dropped and order is canonical.
    """
    if spans is None:
        from lib.trace.trace_service.queries import fetch_session_projection
        spans, _tree = fetch_session_projection(trace_id)

    out = EvidenceIndex(trace_id=trace_id)
    sub_texts: list[str] = []
    index = 0
    for span in spans:
        if (span.get("status_code") or "") == "PENDING":
            continue
        _collect_texts(span, out, sub_texts)
        tool = _tool_name(span)
        if not tool:
            continue
        attrs = span.get("attributes") or {}
        event = ToolEvent(
            index=index, span_id=str(span.get("span_id") or ""),
            tool=tool, start_time=str(span.get("start_time") or ""),
            status=_span_status(span), attrs=attrs,
            agent_id=attrs.get("agent_id"),
        )
        out.events.append(event)
        _index_tool_event(event, out)
        index += 1

    out.final_text = _final_deliverable(out, sub_texts)
    _collect_commit_messages(out)
    out.session = _load_session_row(trace_id)
    log.read("evidence_built", trace_id=trace_id, events=len(out.events),
             reads=len(out.reads), bash=len(out.bash))
    return out


# The hash alternative requires at least one digit so ordinary words made
# of a-f letters ("decade", "deadbeef" aside) don't bind claims to spans.
_TOKEN_RE = re.compile(
    r"[A-Za-z_][A-Za-z0-9_.-]{2,}|\b(?=[0-9a-f]*[0-9])[0-9a-f]{7,40}\b")

_STOPWORDS = frozenset({
    "the", "and", "with", "that", "this", "for", "was", "were", "are",
    "from", "into", "not", "have", "has", "had", "but", "all", "its",
    "after", "before", "when", "then", "than", "they", "them", "will",
})


def content_tokens(text: str) -> set[str]:
    """Identifier-ish tokens used for lexical evidence matching.

    Paths split on `/` so a claim citing `login.ts` matches evidence
    recorded under `src/auth/login.ts`; the second alternative keeps
    digit-bearing git hashes (`930389e`) matchable against Bash stdout.
    Stopwords are dropped so a 2-token overlap means shared content, not
    shared grammar.
    """
    return {t.lower() for t in _TOKEN_RE.findall(text or "")
            if t.lower() not in _STOPWORDS}


__all__ = [
    "ToolEvent", "EvidenceIndex", "build_evidence", "content_tokens",
    "MUTATING_TOOLS", "READ_TOOLS", "SEARCH_TOOLS", "FETCH_TOOLS",
]
