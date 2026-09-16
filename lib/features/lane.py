"""The join from a feature key to the sessions that built it — once.

This expression was copy-pasted across five sites: `index._LANE`,
`cost.coverage`, and `attribution`'s `_task_link_traces`, `_lane_features_for`
and `traceless_runs`. Three of those five are the ledger page, so a shape added
to one and not the others showed a feature sessions and cost in the index and
**zero** on its own page.

**There are three shapes, unioned, and they are three because the assertion
is different in each.** A *link* is an operator pointing a task at an issue in
another tracker. A *parent* is an operator saying this task is a run of that
feature. A *Run on a top-level task* is an operator saying the task is the
feature and this attempt builds it. All are declared acts; none is inferred;
and a key reaches the population only through one of them.

**One collapse rule, everywhere: `LIMIT 1`.** A run's own trace and the
conversation it drove are one session, joined by `agent_run_sessions` — but
that table holds two rows for at least one trace, so a `LEFT JOIN` fans the
run out into two sessions. Sites 1-2 did exactly that while site 3 used
`canonical_trace()`, and the two disagreed by one session on the live
database. A run driving two conversations is an ingestion artefact rather than
a fact worth preserving, so the collapse takes the first alias and the figure
moves once, here, under a test that names it.

The ambiguity guard rides along for the same reason. A task holding two
feature keys belongs to neither, and `index._LANE` had no guard at all — so a
two-link task made the index double-count sessions the ledger refuses to count
at all.
"""

from __future__ import annotations

from lib.tasks.integrations.regin import key_sql

#: One `agent_run_sessions` row per trace, chosen by id. A correlated subquery
#: rather than a `LEFT JOIN`, because the join is what fans a run out into two
#: sessions when the alias table holds two rows for it.
CANONICAL_TRACE = """
COALESCE((SELECT a.cli_session_id FROM agent_run_sessions a
          WHERE a.trace_id = r.trace_id ORDER BY a.id LIMIT 1),
         r.trace_id)"""

#: A top-level task that holds runs and links no other tracker — the feature
#: Run made of it. Its attempts are its own sessions, under its own key. The
#: link exclusion is what keeps this arm from handing a linked task a second
#: key: a linked task's runs belong to the issue, and two keys on one task is
#: the ambiguity every reader below refuses.
_SELF = f"""
    SELECT t.id AS task_id, {key_sql("t.id")} AS key
    FROM agent_tasks t
    WHERE t.parent_id IS NULL
      AND EXISTS (SELECT 1 FROM agent_task_runs r WHERE r.task_id = t.id)
      AND NOT EXISTS (SELECT 1 FROM agent_task_links l WHERE l.task_id = t.id)"""

#: Every task that is a run of a feature, with the key of the feature it runs.
#: Three arms because the three assertions differ — a link points at another
#: tracker's issue, a parent points at a regin task, and a Run on a top-level
#: task points at the task itself.
_RUN_OF = f"""
run_of AS (
    SELECT l.task_id AS task_id, l.external_key AS key
    FROM agent_task_links l
    UNION ALL
    SELECT t.id AS task_id, {key_sql("t.parent_id")} AS key
    FROM agent_tasks t
    WHERE t.parent_id IS NOT NULL
    UNION ALL{_SELF}
)"""

#: Every feature key a task holds, in either role. A task is a *run of* a
#: feature through a link or through its parentage, and it *is* a feature when
#: something names it as parent — and a task in both roles at once holds two
#: feature keys, which is the thing `feature_key` being identity forbids.
_OWNS = f"""
owns AS (
    SELECT task_id, key FROM run_of
    UNION ALL
    SELECT t.id AS task_id, {key_sql("t.id")} AS key
    FROM agent_tasks t
    WHERE t.id IN (SELECT parent_id FROM agent_tasks
                   WHERE parent_id IS NOT NULL)
)"""

#: A task naming two feature keys. Its runs belong to neither, so every reader
#: below excludes it rather than charging one session's whole cost to one of
#: two features with nobody seeing the choice. The keys go too: the ledger
#: refuses such a feature outright, and an index that still counted its
#: sessions would disagree with the page it links to.
_AMBIGUOUS = """
ambiguous AS (
    SELECT task_id FROM owns GROUP BY task_id HAVING COUNT(DISTINCT key) > 1
),
ambiguous_keys AS (
    SELECT DISTINCT key FROM owns
    WHERE task_id IN (SELECT task_id FROM ambiguous)
)"""

#: `(key, trace_id, task_id)` for every run that produced a trace.
_TRACES = f"""
lane AS (
    SELECT run_of.key AS key,
           {CANONICAL_TRACE} AS trace_id,
           r.task_id AS task_id
    FROM agent_task_runs r
    JOIN run_of ON run_of.task_id = r.task_id
    WHERE r.trace_id IS NOT NULL
      AND r.task_id NOT IN (SELECT task_id FROM ambiguous)
      AND run_of.key NOT IN (SELECT key FROM ambiguous_keys)
)"""

#: The whole lane, as a CTE chain a caller prefixes with `WITH`.
LANE = f"{_RUN_OF},{_OWNS},{_AMBIGUOUS},{_TRACES}"

#: Every feature key regin knows. Five arms, each a row that exists only
#: because somebody declared something: a session attribution, a defect's
#: blame, a task link, a task named as parent, and a top-level task somebody
#: pressed Run on. None sweeps in every root task — an idea nobody has run is
#: not a feature yet, and admitting it by filter would be an inference.
KEYS = f"""
keys AS (
    SELECT feature_key AS key FROM session_features
    UNION SELECT feature_key FROM defects WHERE feature_key IS NOT NULL
    UNION SELECT external_key FROM agent_task_links
    UNION SELECT DISTINCT {key_sql("parent_id")} FROM agent_tasks
        WHERE parent_id IS NOT NULL
    UNION SELECT key FROM ({_SELF.strip()})
)"""

#: Runs of a feature that produced no trace at all — reported beside the
#: session list rather than dropped, so a feature that burned three failed
#: launches does not read as untouched.
TRACELESS = """
SELECT COUNT(*) FROM agent_task_runs r
JOIN run_of ON run_of.task_id = r.task_id
WHERE run_of.key = :k AND r.trace_id IS NULL
"""


def with_lane(body: str) -> str:
    """`body` prefixed by the lane's CTE chain."""
    return f"WITH {LANE}{body}"
