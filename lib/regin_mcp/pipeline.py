"""The pipeline-wide read: what is running, what is next, what needs a call."""

from __future__ import annotations

from lib.regin_mcp import _common


def pipeline_overview(project: str = "") -> dict:
    """The pipeline at a glance: what is running, what to run next, what needs
    a decision, features in flight, and (unscoped) every project's rollup.

    Args:
        project: A registered project (repo) name to scope to; empty for all.
    """
    from lib.pipeline.overview import overview
    from lib.pipeline.counts import SCOPE_ALL
    project_id = _common.project_id(project)
    return overview(SCOPE_ALL if project_id is None else project_id)


TOOLS = (pipeline_overview,)
