"""`regin memory index-root|index-expand|index-fetch|read|recall` — the MCP-free walk.

The memory tree walk used to exist only as `mcp__memory__index_*`, which made
`goal-verified-treenav`'s recall arm unrunnable on any harness that doesn't
speak MCP. These commands render through the same `lib/memory/tree_nav.py`
functions the MCP tools delegate to, and emit the `memory.index.nav` span so a
walk done this way still satisfies `regin gate recall-ran`.

Every parity test compares the CLI to `lib.memory.mcp_server.*`, never to the
renderer the CLI itself calls — the latter is a tautology that cannot catch
drift. And the fixtures seed real memories, topic links and a wiki file so that
`--scope` / `--top-k` / `--reinforce` / `--part` actually change the output;
against an empty DB every one of those flags is a silent no-op.
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

import lib.memory as memory
from cli.app import app
from lib import hook_plugin
from lib.memory import mcp_server, tree_nav
from lib.orm import SessionLocal
from lib.orm.models import SessionSpan
from lib.settings import settings
from lib.topics.graph_io import load_authoritative_graph
from lib.topics.wiki import wiki_dir
from lib.trace.span_gates import RECALL_ARM, span_count


runner = CliRunner()


def _leaf_node() -> str:
    """A real, non-bucket topic node from the repo's approved graph."""
    graph = load_authoritative_graph(str(settings.project_root))
    return next(t for t, n in graph["topics"].items()
                if not n.get("meta") and n.get("kind") != "bucket")


@pytest.fixture
def filled_node() -> str:
    """A topic node carrying several linked memories in two scopes — the state
    every flag on these commands is defined against.

    Seeded as non-test rows on purpose: `memory.recall` filters `is_test` out,
    so a `is_test=True` fixture makes every recall assertion vacuously pass.
    The autouse `tmp_memory_db` fixture keeps them out of the real store."""
    node = _leaf_node()
    store = memory.get_store()
    for i in range(4):
        mid = memory.remember(
            f"Walk fixture memory {i} about kimi provider transcripts.\n\n"
            "**Why:** so top_k has something to cut.\n\n"
            "**How to apply:** read the part index.",
            kind="lesson", title=f"Walk fixture {i}",
            scope="repo:regin")
        store.link_authoritative_topic(mid, node, source="manual")
    other = memory.remember("Out-of-scope fixture memory.", kind="lesson",
                            title="Other scope", scope="repo:elsewhere")
    store.link_authoritative_topic(other, node, source="manual")
    return node


@pytest.fixture
def node_with_wiki() -> str:
    """A topic that really has a wiki file, so `--reinforce` has something to
    bump. Repointing `project_root` at a tmp dir would take the topic graph
    with it, leaving `index-fetch` with no node to fetch at all."""
    graph = load_authoritative_graph(str(settings.project_root))
    wiki = wiki_dir(settings.project_root)
    node = next((t for t in graph["topics"] if (wiki / f"{t}.md").exists()), None)
    if node is None:
        pytest.skip("no topic in this checkout has a wiki file")
    return node


def _captured_spans(monkeypatch) -> list[dict]:
    """Intercept the span seam — the suite severs ingest, so a real post
    would be swallowed and prove nothing."""
    spans: list[dict] = []
    monkeypatch.setattr(hook_plugin, "post_span",
                        lambda **kw: spans.append(kw) or True)
    return spans


def _out(*argv: str) -> str:
    result = runner.invoke(app, list(argv))
    assert result.exit_code in (0, 1), result.output
    return result.stdout.strip()


def _exit(*argv: str) -> int:
    return runner.invoke(app, list(argv)).exit_code


# ── parity with the MCP tools (the only non-tautological comparison) ──

def test_index_root_matches_the_mcp_tool(filled_node):
    assert _out("memory", "index-root", "--scope", "repo:regin") == \
        mcp_server.index_root("repo:regin").strip()


def test_index_expand_matches_the_mcp_tool(filled_node):
    assert _out("memory", "index-expand", filled_node) == \
        mcp_server.index_expand(filled_node).strip()


def test_index_fetch_matches_the_mcp_tool(filled_node):
    assert _out("memory", "index-fetch", filled_node, "--top-k", "3",
                "--no-reinforce") == \
        mcp_server.index_fetch(filled_node, top_k=3, reinforce=False).strip()


def test_read_matches_the_mcp_tool(filled_node):
    mid = memory.get_store().memories_for_topic_subtree([filled_node])[0]
    assert _out("memory", "read", mid) == mcp_server.memory_read(mid).strip()


def test_read_part_matches_the_mcp_tool(filled_node):
    mid = memory.get_store().memories_for_topic_subtree([filled_node])[0]
    assert _out("memory", "read", mid, "--part", "Why") == \
        mcp_server.memory_read(mid, part="Why").strip()
    assert "so top_k has something to cut" in _out(
        "memory", "read", mid, "--part", "Why")
    assert "How to apply" not in _out("memory", "read", mid, "--part", "Why")


def test_recall_matches_the_mcp_tool(filled_node):
    out = _out("memory", "recall", "kimi provider transcripts", "--top-k", "2")
    # Guard against a vacuous pass: two "nothing matched" strings also match.
    assert "Walk fixture" in out
    assert out == mcp_server.recall("kimi provider transcripts",
                                    top_k=2).strip()


# ── the flags actually do something (mutation bait) ───────────────

def test_scope_filters_the_counts(filled_node):
    wide = _out("memory", "index-root")
    narrow = _out("memory", "index-root", "--scope", "repo:regin")
    assert wide != narrow


def test_top_k_caps_the_memory_listing(filled_node):
    assert _out("memory", "index-fetch", filled_node, "--top-k", "2",
                "--no-reinforce").count("(id: ") == 2
    assert _out("memory", "index-fetch", filled_node, "--top-k", "10",
                "--no-reinforce").count("(id: ") == 5


def test_no_reinforce_leaves_the_wiki_counter_alone(node_with_wiki):
    store = memory.get_store()
    exposure = lambda: store.wiki_recall_for_topic(node_with_wiki)["exposure"]
    before = exposure()
    _out("memory", "index-fetch", node_with_wiki, "--no-reinforce")
    assert exposure() == before
    _out("memory", "index-fetch", node_with_wiki)
    assert exposure() > before


def test_compact_recall_is_the_inspection_listing(filled_node):
    query = "kimi provider transcripts"
    agent_form = _out("memory", "recall", query, "--top-k", "1")
    compact = _out("memory", "recall", query, "--top-k", "1", "--compact")
    assert "Walk fixture" in agent_form and "Walk fixture" in compact
    assert compact != agent_form
    assert "so top_k has something to cut" not in compact


# ── the render itself (parity alone cannot see renderer drift) ────

def test_expand_on_a_leaf_points_at_the_next_step(filled_node):
    # The hint IS the routing instruction — a leaf that doesn't name the fetch
    # step strands the walk, and CLI/MCP parity can't see that (both drift).
    # One renderer serves both front-ends, so it must name BOTH call forms:
    # "call index_fetch" is not actionable on a harness with no MCP.
    out = _out("memory", "index-expand", filled_node)
    assert "leaf" in out
    assert "index_fetch" in out
    assert "regin memory index-fetch" in out


def test_orphan_bucket_fetches_as_addresses_with_no_wiki_or_refs():
    out = _out("memory", "index-fetch", memory.ORPHAN_NODE_ID)
    assert "## wiki\n(none" in out
    assert "## source refs\n(none)" in out
    assert "## memories" in out


def test_fetch_returns_addresses_not_bodies(filled_node):
    out = _out("memory", "index-fetch", filled_node, "--no-reinforce")
    assert out.startswith(filled_node)
    for section in ("## wiki", "## source refs", "## memories"):
        assert section in out
    assert "so top_k has something to cut" not in out


def test_root_lists_cards_under_a_next_step_header(filled_node):
    lines = _out("memory", "index-root", "--scope", "repo:regin").splitlines()
    assert lines[0].startswith("top-level topics")
    assert any(line.startswith("- ") for line in lines[1:])


# ── miss paths ────────────────────────────────────────────────────

def test_index_expand_reports_an_unknown_node_instead_of_crashing():
    out = _out("memory", "index-expand", "no-such-node")
    assert "no topic node" in out
    # Both front-ends read this string, so it must not name only one of them.
    assert "regin memory index-root" in out


def test_read_reports_a_missing_memory():
    assert "no memory" in _out("memory", "read", "deadbeef")


# ── the gate fingerprint ──────────────────────────────────────────

def test_walk_commands_emit_a_nav_span_when_given_a_session(monkeypatch,
                                                            filled_node):
    spans = _captured_spans(monkeypatch)
    runner.invoke(app, ["memory", "index-root", "--session", "sid-walk"])
    runner.invoke(app, ["memory", "index-expand", filled_node,
                        "--session", "sid-walk"])
    assert [s["name"] for s in spans] == ["memory.index.nav"] * 2
    assert {s["trace_id"] for s in spans} == {"sid-walk"}
    assert [s["attributes"]["tool"] for s in spans] == [
        "index-root", "index-expand"]


def test_no_session_means_no_span(monkeypatch, filled_node):
    # The walk still works unattributed; you only forgo the gate's proof.
    spans = _captured_spans(monkeypatch)
    assert _out("memory", "index-root")
    assert spans == []


def test_recall_command_can_also_fingerprint_the_arm(monkeypatch, filled_node):
    spans = _captured_spans(monkeypatch)
    runner.invoke(app, ["memory", "recall", "kimi provider transcripts",
                        "--session", "sid-r"])
    assert [s["attributes"]["tool"] for s in spans] == ["recall"]


@pytest.mark.parametrize("argv", [
    ["memory", "read", "zzzzzzzz"],            # no such memory
    ["memory", "index-expand", "no-such"],     # no such node
    ["memory", "recall", "qqqzzzxyz-nomatch"],  # nothing matched
])
def test_a_miss_never_fingerprints_the_arm(monkeypatch, argv):
    # Otherwise `regin memory read <typo> --session $SID` is a one-command
    # bypass of the anti-skip gate that step 2 and step 4 treat as a wall.
    spans = _captured_spans(monkeypatch)
    runner.invoke(app, [*argv, "--session", "sid-miss"])
    assert spans == []


def test_disabled_memory_never_fingerprints_the_arm(monkeypatch):
    monkeypatch.setattr(memory, "enabled", lambda: False)
    spans = _captured_spans(monkeypatch)
    runner.invoke(app, ["memory", "index-root", "--session", "sid-off"])
    assert spans == []


def test_an_ingest_failure_is_reported_not_swallowed(monkeypatch, filled_node):
    # A silently-dropped span leaves the agent believing it has proof, then
    # convicts it of skipping — the unfollowable gate `ui-verified` died of.
    monkeypatch.setattr(hook_plugin, "post_span", lambda **kw: False)
    result = runner.invoke(app, ["memory", "index-root", "--session", "sid-dead"])
    assert result.exit_code == 0
    assert "did not reach regin's ingest" in result.stderr


def test_nav_span_satisfies_the_recall_ran_gate():
    with SessionLocal() as s:
        s.add(SessionSpan(trace_id="sid-cli", span_id="sp-1", parent_id=None,
                          name="memory.index.nav", kind="internal",
                          start_time="2026-07-28 06:00:00"))
        s.commit()
    assert span_count("sid-cli", RECALL_ARM) == 1
    result = runner.invoke(app, ["gate", "recall-ran", "--session", "sid-cli"])
    assert result.exit_code == 0
    assert "GATE PASS" in result.stdout


def test_render_miss_detection_covers_every_dead_end():
    assert not tree_nav.is_result(tree_nav.DISABLED)
    assert not tree_nav.is_result("no topic node 'x' — list the roots first")
    assert not tree_nav.is_result("no memory 'x' — check the id")
    assert not tree_nav.is_result("no stored experience matched this query")
    assert tree_nav.is_result("top-level topics (then expand / fetch a node")


def test_a_hit_exits_zero_and_a_miss_exits_one(filled_node):
    # `regin memory index-expand <node> || fall_back` has to work in a shell.
    assert _exit("memory", "index-expand", filled_node) == 0
    assert _exit("memory", "index-expand", "no-such-node") == 1
    assert _exit("memory", "read", "zzzzzzzz") == 1


def test_disabled_memory_exits_one_on_every_walk_command(monkeypatch):
    monkeypatch.setattr(memory, "enabled", lambda: False)
    for argv in (["memory", "index-root"], ["memory", "index-expand", "x"],
                 ["memory", "index-fetch", "x"], ["memory", "read", "x"]):
        result = runner.invoke(app, argv)
        assert result.exit_code == 1, argv
        assert tree_nav.DISABLED in result.stdout


def test_json_recall_keeps_its_contract_when_memory_is_disabled(monkeypatch,
                                                                filled_node):
    # Seeded on purpose: against an empty store this passes no matter what, and
    # `memory.recall` does NOT consult `enabled()` — so without the guard here
    # a disabled store still returns bodies, bumps recall_count, and (with
    # --session) credits the anti-skip gate. A prose line would also break any
    # consumer doing `json.loads`.
    spans = _captured_spans(monkeypatch)
    monkeypatch.setattr(memory, "enabled", lambda: False)
    result = runner.invoke(app, ["memory", "recall", "kimi provider transcripts",
                                 "--json", "--session", "sid-off"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == []
    assert spans == []


def test_disabled_memory_never_recalls_on_any_output_mode(monkeypatch,
                                                          filled_node):
    monkeypatch.setattr(memory, "enabled", lambda: False)
    for extra in ([], ["--compact"]):
        result = runner.invoke(app, ["memory", "recall",
                                     "kimi provider transcripts", *extra])
        assert result.exit_code == 1, extra
        assert "Walk fixture" not in result.stdout, extra


def test_top_k_is_clamped_identically_on_every_output_mode(filled_node):
    # `render_recall` caps at 20; --json reaching `memory.recall` directly used
    # to bypass that, so the two modes disagreed on how much they returned.
    wide = json.loads(_out("memory", "recall", "kimi provider transcripts",
                           "--json", "--top-k", "50"))
    assert len(wide) <= 20
    assert len(json.loads(_out("memory", "recall", "kimi provider transcripts",
                               "--json", "--top-k", "0"))) >= 1


def test_recall_prints_the_part_index_that_makes_read_discoverable(filled_node):
    # Without this line a CLI-only harness has no way to learn that `--part Why`
    # is even available — the follow-up the skill docs instruct.
    long_body = ("Lead sentence for the part-index probe.\n\n"
                 "**Why:** " + "w" * 400 + "\n\n"
                 "**How to apply:** " + "h" * 400)
    memory.remember(long_body, kind="lesson", title="Part index probe",
                    scope="repo:regin")
    out = _out("memory", "recall", "part-index probe", "--top-k", "1")
    assert "⋯ +" in out
    assert "regin memory read" in out
    assert "--part" in out


def test_index_fetch_span_carries_reinforce_for_the_wiki_read_signal(
        monkeypatch, filled_node):
    # lib/memory/wiki_reads.py reads `reinforce` off this span; without it an
    # audit sweep would count as a genuine consultation.
    spans = _captured_spans(monkeypatch)
    runner.invoke(app, ["memory", "index-fetch", filled_node,
                        "--no-reinforce", "--session", "sid-rf"])
    assert spans[0]["attributes"]["reinforce"] is False


def test_every_next_step_hint_names_both_call_forms(filled_node):
    # One renderer serves the MCP tools and the CLI, so a hint naming only the
    # tool strands a harness that has no MCP. Assert it, don't just observe it.
    root = _out("memory", "index-root")
    assert "index_expand" in root and "regin memory index-expand" in root

    over_cap = _out("memory", "index-fetch", filled_node, "--top-k", "1",
                    "--no-reinforce")
    assert "raise top_k / --top-k" in over_cap

    flat = memory.remember("A memory with no authored sections at all.",
                           kind="lesson", title="Flat", scope="repo:regin")
    assert "`part` / `--part`" in _out("memory", "read", flat, "--part", "nope")


def test_recall_fingerprints_the_arm_on_every_output_mode(monkeypatch,
                                                          filled_node):
    # Honouring --session only on the default render would make
    # `recall --json --session $SID` a silent no-op, and the agent that
    # scripted it would then be told it skipped the recall arm.
    for extra in ([], ["--json"], ["--compact"]):
        spans = _captured_spans(monkeypatch)
        runner.invoke(app, ["memory", "recall", "kimi provider transcripts",
                            "--session", "sid-mode", *extra])
        assert [s["attributes"]["tool"] for s in spans] == ["recall"], extra
