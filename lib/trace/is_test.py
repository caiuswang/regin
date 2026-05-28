"""SQL conventions for the `is_test` span marker.

Producers (hooks, test harnesses, external tools) historically wrote
`attributes.is_test` as Python bool, int, or one of the strings
{"true","True","1","yes"}. `_normalize_is_test` is the write-time
normaliser that turns any of those into a real boolean before the row
hits sqlite; after that, `_IS_TEST_WHERE` / `_IS_TEST_CASE` are the
single canonical SQL fragments every read site uses to filter or count
test sessions.

This lives in `lib/` so that both the lib-side trace queries and the
web blueprints can sit on top of it. `web/helpers.py` re-exports the
four names so the `app_module._IS_TEST_CASE` test surface stays intact.
"""

from __future__ import annotations


_IS_TEST_TRUTHY = {'1', 'true', 'yes', 'y'}


def _normalize_is_test(value) -> bool:
    """Return True iff `value` represents a test-session marker."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in _IS_TEST_TRUTHY
    return False


# Reusable WHERE-clause fragments. After ingest normalisation, `is_test`
# is always either absent (treated as falsey) or JSON boolean true, which
# sqlite's json_extract returns as integer 1.
_IS_TEST_WHERE = "json_extract(attributes, '$.is_test') = 1"
_IS_TEST_CASE = f"MAX(CASE WHEN {_IS_TEST_WHERE} THEN 1 ELSE 0 END)"
