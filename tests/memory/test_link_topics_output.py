"""Per-link detail output for `regin memory link-topics` — a real (writing) run
must show *which memory linked to which topic*, not just the summary counts.
Regression for the dry-run-only print bug."""

from __future__ import annotations

import lib.memory as memory
from cli.commands.memory import _apply_assignments, _fmt_link


def _remember(body, **kw):
    kw.setdefault("is_test", False)
    kw.setdefault("title", body[:80])  # lessons now require a (unique) title
    return memory.remember(body, **kw)


def test_fmt_link_includes_status_tag_and_target():
    titles = {"abcdef0123456789": "A short lesson"}
    labels = {"agent-memory": "Agent memory engine"}
    line = _fmt_link("abcdef0123456789", "agent-memory", titles, labels,
                     status="linked")
    assert "linked" in line
    assert "abcdef01" in line           # id8 prefix
    assert "A short lesson" in line      # memory title
    assert "→ agent-memory" in line      # the topic it linked to
    assert "(Agent memory engine)" in line


def test_apply_assignments_prints_each_link_on_real_run(capsys):
    mid = _remember("A lesson to file.", kind="lesson")
    store = memory.get_store()
    titles = {mid: "A lesson to file."}
    labels = {"agent-memory": "Agent memory engine"}

    linked, refreshed, unmatched = _apply_assignments(
        store, {mid: ["agent-memory"]}, dry_run=False,
        titles=titles, labels=labels)

    assert (linked, refreshed, unmatched) == (1, 0, 0)
    out = capsys.readouterr().out
    # the real run printed the per-link detail, not just summary
    assert mid[:8] in out
    assert "→ agent-memory" in out
    assert "linked" in out


def test_apply_assignments_marks_refresh_distinctly(capsys):
    mid = _remember("Already filed once.", kind="lesson")
    store = memory.get_store()
    store.link_authoritative_topic(mid, "agent-memory", source="agent")
    titles = {mid: "Already filed once."}
    labels = {"agent-memory": "Agent memory engine"}

    linked, refreshed, unmatched = _apply_assignments(
        store, {mid: ["agent-memory"]}, dry_run=False,
        titles=titles, labels=labels)

    assert (linked, refreshed, unmatched) == (0, 1, 0)
    out = capsys.readouterr().out
    assert "refresh" in out
    assert mid[:8] in out


def test_apply_assignments_unmatched_prints_no_link_line(capsys):
    mid = _remember("Nowhere to file.", kind="lesson")
    store = memory.get_store()
    titles = {mid: "Nowhere to file."}

    linked, refreshed, unmatched = _apply_assignments(
        store, {mid: []}, dry_run=False, titles=titles, labels={})

    assert (linked, refreshed, unmatched) == (0, 0, 1)
    assert capsys.readouterr().out == ""
