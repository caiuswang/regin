"""One envelope can name SEVERAL tasks.

When a session ends with background shells still running, the harness tells
the NEXT session about all of them in one envelope — several `<task-id>` tags
plus an `__orphan_summary__` marker — and marks them stopped. Reading only the
first tag left every shell but one listed as running forever.
"""

from __future__ import annotations

from lib.trace.task_notification import parse_task_notification

ORPHANS = """<task-notification>
<task-id>bmbknevn9</task-id>
<task-id>bsk9mt0d6</task-id>
<task-id>__orphan_summary__:shell</task-id>
<status>stopped</status>
<summary>2 background shell command tasks didn\'t finish.</summary>
</task-notification>"""

ONE = """<task-notification>
<task-id>bo58vwcaw</task-id>
<status>completed</status>
</task-notification>"""


def test_every_named_task_is_reported():
    fields = parse_task_notification(ORPHANS)

    assert fields['task_ids'] == ['bmbknevn9', 'bsk9mt0d6']


def test_the_internal_scan_marker_is_not_a_task():
    """The envelope says so itself: ids beginning `__orphan_summary` are scan
    markers, not tasks."""
    assert '__orphan_summary__:shell' not in parse_task_notification(ORPHANS)['task_ids']


def test_the_single_id_case_is_unchanged():
    fields = parse_task_notification(ONE)

    assert fields['task_id'] == 'bo58vwcaw'
    assert fields['task_ids'] == ['bo58vwcaw']


def test_the_first_id_stays_the_span_key():
    """`task_id` keys the notification span; it must keep naming one task."""
    assert parse_task_notification(ORPHANS)['task_id'] == 'bmbknevn9'
