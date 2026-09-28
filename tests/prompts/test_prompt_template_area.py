"""The prompt-template list API carries each row's registry category."""

from __future__ import annotations

from lib.prompt_templates import create_template, seed_builtin_skeletons
from lib.prompts.registry import get_surface


def _rows(flask_client, kind: str | None = None) -> list[dict]:
    url = "/api/prompt-templates" + (f"?kind={kind}" if kind else "")
    answer = flask_client.get(url)
    assert answer.status_code == 200, answer.get_json()
    return answer.get_json()["templates"]


def test_every_row_carries_the_registry_area(flask_client):
    seed_builtin_skeletons()
    create_template({"label": "My own fragment", "body": "be terse"})

    rows = _rows(flask_client)
    by_slug = {r["slug"]: r for r in rows}

    assert rows and all("area" in r for r in rows)
    for slug, row in by_slug.items():
        surface = get_surface(slug)
        assert row["area"] == (surface.area if surface else "other"), slug
    assert by_slug["topic-authoring-standards"]["area"] == "topic-proposal"
    assert by_slug["my-own-fragment"]["area"] == "other"


def test_skeleton_tab_spans_several_areas(flask_client):
    seed_builtin_skeletons()

    areas = {r["area"] for r in _rows(flask_client, "skeleton")}

    assert {"memory", "grader", "task-run", "topics"} <= areas
    assert "other" not in areas
