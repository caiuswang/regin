"""`regin messages list` / `read` over the inbox store."""

import json

from typer.testing import CliRunner

from cli.commands.messages import messages_app
from lib.agent_messages import store


def _send(title: str, **kw) -> int:
    return store.record_message(trace_id="s1", body=f"{title} body",
                                title=title, msg_type="note", **kw)["id"]


def _invoke(*args):
    return CliRunner().invoke(messages_app, list(args))


def test_messages_list_prints_unread_as_json():
    old = _send("old")
    new = _send("new")
    store.mark_read([old])

    result = _invoke("list", "--unread", "--json")

    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert [r["id"] for r in rows] == [new]
    assert rows[0]["title"] == "new" and rows[0]["msg_type"] == "note"


def test_messages_list_json_is_empty_array_when_inbox_clear():
    result = _invoke("list", "--unread", "--json")
    assert result.exit_code == 0
    assert json.loads(result.output) == []


def test_messages_list_text_marks_unread_rows():
    read_id = _send("seen")
    _send("fresh")
    store.mark_read([read_id])

    lines = _invoke("list").output.splitlines()

    assert len(lines) == 2
    assert lines[0].startswith("*") and "fresh" in lines[0]
    assert lines[1].startswith(" ") and "seen" in lines[1]


def test_messages_read_marks_the_given_ids_read():
    a, b, c = _send("a"), _send("b"), _send("c")

    result = _invoke("read", str(a), str(c))

    assert result.exit_code == 0, result.output
    assert "marked 2" in result.output
    unread = [r["id"] for r in store.list_inbox(unread_only=True)]
    assert unread == [b]


def test_messages_read_all_marks_every_unread():
    _send("a"), _send("b")

    result = _invoke("read", "--all")

    assert result.exit_code == 0
    assert "marked 2" in result.output
    assert store.list_inbox(unread_only=True) == []


def test_messages_read_needs_ids_or_all_but_not_both():
    a = _send("a")
    assert _invoke("read").exit_code == 2
    assert _invoke("read", str(a), "--all").exit_code == 2
    assert [r["id"] for r in store.list_inbox(unread_only=True)] == [a]
