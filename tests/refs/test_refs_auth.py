"""Who may search, and who is told what a session said (D10, I12)."""

from __future__ import annotations

from tests.refs.conftest import ADMIN, ANON, EDITOR, VIEWER, groups

SID = "3fd8137e-e0fd-4a5b-9c1d-0123456789ab"
MISSING = "ffffffff-not-a-session"
SECRET = "secret alpha plan"
PROMPT = "alpha first prompt words"
RESOLVE = f"/api/refs/resolve?ref=session:{SID}&ref=session:{MISSING}"


def _leaks(client, url: str, headers) -> bool:
    text = client.get(url, headers=headers).get_data(as_text=True)
    return SECRET in text or PROMPT in text


def _session_titles(client, url: str) -> list[str]:
    found = groups(client.get(url, headers=ADMIN).get_json())
    return sorted(item["title"] for item in found["session"]["items"])


def test_search_requires_editor_and_sessions_need_admin(
        flask_client, session_trace):
    session_trace(SID, title=SECRET)
    session_trace("0000aaaa-untitled", prompt=PROMPT)

    assert flask_client.get("/api/refs?q=alpha",
                            headers=ANON).status_code == 401
    assert flask_client.get("/api/refs?q=alpha",
                            headers=VIEWER).status_code == 403
    editor = flask_client.get("/api/refs?q=alpha", headers=EDITOR)
    assert (editor.status_code, "session" in groups(editor.get_json())) \
        == (200, False)
    leaked = [url for url in ("/api/refs?q=alpha", "/api/refs?kind=session",
                              "/api/refs?q=S3fd8", f"/api/refs?q={SID}")
              if _leaks(flask_client, url, EDITOR)]
    assert leaked == []

    # An untitled session reads as its first prompt.
    assert _session_titles(flask_client, "/api/refs?q=alpha") \
        == [PROMPT, SECRET]
    assert _session_titles(flask_client, "/api/refs?q=S3fd8") == [SECRET]


def _restricted(key: str) -> dict:
    return {"ref": f"session:{key}", "kind": "session", "key": key,
            "label": f"S{key[:8]}", "title": "", "status": None,
            "project_id": None, "project": None, "feature_id": None,
            "url": None, "restricted": True}


def test_resolve_restricts_sessions_to_admins(flask_client, session_trace):
    session_trace(SID, title=SECRET)

    assert flask_client.get(RESOLVE, headers=ANON).status_code == 401
    for headers in (EDITOR, VIEWER):
        # Restricted is answered without a lookup: no existence oracle.
        assert flask_client.get(RESOLVE, headers=headers).get_json() == {
            "refs": {f"session:{SID}": _restricted(SID),
                     f"session:{MISSING}": _restricted(MISSING)}}
        assert not _leaks(flask_client, RESOLVE, headers)

    admin = flask_client.get(RESOLVE, headers=ADMIN).get_json()["refs"]
    assert {key: admin[f"session:{SID}"].get(key)
            for key in ("title", "label", "url", "restricted")} == {
        "title": SECRET, "label": f"S3fd8137e {SECRET}",
        "url": f"/trace/sessions/{SID}", "restricted": None}
    assert admin[f"session:{MISSING}"] is None
