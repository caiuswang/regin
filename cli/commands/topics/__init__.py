"""`regin topics ...` - maintain repo-local topic graphs."""

from __future__ import annotations

# Import order is `regin topics --help` order: each module registers its
# commands on `topics_app` as it is imported.
from ._common import topics_app
from . import graph, proposals, sync, wiki, audit  # noqa: F401
from .audit import cmd_topics_group
from .graph import cmd_topics_bootstrap, cmd_topics_scan
from .sync import cmd_topics_check, cmd_topics_install_hook
from .wiki import _render_topic_wiki, cmd_topics_route, cmd_topics_wiki
