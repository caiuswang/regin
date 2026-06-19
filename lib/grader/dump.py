"""Read-only serialization of a session's gradeable evidence.

This is the *method* the agentic deep-tier judge uses to investigate a
session itself, rather than grading pre-chewed text: `regin trace dump
<id>` emits the merged projection (prompts, final deliverable, ordered
tool spans with capped output slices), and `regin trace span <id>
<span_id>` returns one span's full untruncated content for a close read.

Both go through `build_evidence`, so the span_ids the judge cites line up
with the ones the grader validates its quotes against.
"""

from __future__ import annotations

from lib.grader.evidence import EvidenceIndex, ToolEvent, build_evidence

_PREVIEW = 2000


_INDEX_PREVIEW = 160


def _span_preview(event: ToolEvent) -> dict:
    return {
        "index": event.index,
        "span_id": event.span_id,
        "tool": event.tool,
        "status": event.status,
        "file_path": event.file_path,
        "command": event.command[:300],
        "content_preview": event.content[:_PREVIEW],
        "stdout_preview": event.stdout[:_PREVIEW],
        "stderr": event.stderr[:1000],
        "diff_preview": event.diff[:_PREVIEW],
    }


def _span_index_row(event: ToolEvent) -> dict:
    """A compact catalog row — enough to pick a span by, scaling with span
    count not content. Fetch the full span with `trace span` to ground."""
    body = event.content or event.stdout or event.diff or event.stderr
    return {
        "span_id": event.span_id, "tool": event.tool, "status": event.status,
        "file_path": event.file_path, "command": event.command[:120],
        "preview": body[:_INDEX_PREVIEW],
        **event.read_range,
    }


def dump_session(trace_id: str, evidence: EvidenceIndex | None = None,
                 index_only: bool = False) -> dict:
    """The judge's view of one session: prompts, final deliverable, and the
    tool spans. `index_only` emits a compact catalog (no large content) the
    judge pages from before fetching specific spans full."""
    ev = evidence if evidence is not None else build_evidence(trace_id)
    span_fn = _span_index_row if index_only else _span_preview
    return {
        "trace_id": trace_id,
        "prompts": ev.prompt_texts,
        "final_deliverable": ev.final_text,
        "commit_messages": ev.commit_messages,
        "spans": [span_fn(e) for e in ev.events],
    }


def dump_span(trace_id: str, span_id: str,
              evidence: EvidenceIndex | None = None) -> dict | None:
    """One span's full, untruncated recorded content."""
    ev = evidence if evidence is not None else build_evidence(trace_id)
    for event in ev.events:
        if event.span_id == span_id:
            return {
                "span_id": span_id, "index": event.index, "tool": event.tool,
                "status": event.status, "file_path": event.file_path,
                "command": event.command, "content": event.content,
                "stdout": event.stdout, "stderr": event.stderr,
                "diff": event.diff, **event.read_range,
            }
    return None


def span_recorded_text(evidence: EvidenceIndex, span_id: str) -> str:
    """The concatenated recorded output of one span — what a judge's quote
    must appear in verbatim for the claim to count as grounded."""
    for event in evidence.events:
        if event.span_id == span_id:
            return " ".join((event.content, event.stdout, event.stderr,
                             event.diff, event.command, event.file_path))
    return ""


__all__ = ["dump_session", "dump_span", "span_recorded_text"]
