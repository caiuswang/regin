"""Conversations about things — threads and their comments.

One subject kind today (`task`), and the tables are polymorphic anyway: the
alternative was widening `proposal_feedback_*`, which is this shape welded to
one subject by three foreign keys, and every reader of it would then have had
to carry a kind column with no second value.
"""

from lib.comments import store  # noqa: F401

__all__ = ["store"]
