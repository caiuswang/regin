# regin Python conventions

Repo-local Python rules for **regin** itself, enforced by GritQL on every
`.py` Edit/Write via the PostToolUse hook. Each rule below maps 1:1 to a
pattern in `.grit/patterns/python/regin_python_checks.grit`.

> A PostToolUse hook runs the applicable rules against every `.py` file you
> Edit/Write and surfaces violations automatically. You do **not** need to
> re-run `grit apply` on a file you just edited. Use `scripts/check_grit.sh`
> for bulk sweeps / CI / auditing code you didn't touch this session.

## Rules

### 1. No bare `except:` — `py_bare_except` (warn)

A bare `except:` swallows **everything**, including `KeyboardInterrupt` and
`SystemExit`, and hides the real error. Name the exception type.

```python
# ✗ bad
try:
    risky()
except:
    pass

# ✓ good
try:
    risky()
except (OSError, ValueError) as exc:
    log.error("risky_failed", exc_info=exc)
```

### 2. No `print()` in library code — `py_print_call` (warn)

Library code routes output through the structured-log pipeline, not stdout.
`print()` can't be filtered, rotated, or correlated by `request_id`.

```python
# ✗ bad
print(f"imported {slug}")

# ✓ good
from lib.activity_log import get_activity_logger
log = get_activity_logger("patterns")
log.write("pattern_imported", slug=slug)
```

CLI command modules (`cli/`) are the intended place for user-facing `print()`;
this rule targets `lib/`. (Trigger is content-based; suppress per-line where a
print is genuinely a CLI surface.)

### 3. Activity logger forbids `.info()` / `.debug()` — `py_activity_logger_forbidden_level` (error)

The `lib/activity_log.py` wrapper intentionally omits `.info()` / `.debug()`
(they raise `AttributeError`) so the read=DEBUG / write=INFO discipline can't
be silently violated.

```python
log = get_activity_logger("patterns")
log.info("loaded")          # ✗ AttributeError at runtime
log.read("pattern_loaded", pattern_id=pid)   # ✓ DEBUG (read paths)
log.write("pattern_saved", pattern_id=pid)    # ✓ INFO  (write paths)
```

### 4. No raw `sqlite3.connect()` — `py_raw_sqlite_connect` (warn)

Direct connects miss the canonical `DB_PATH`, the row factory, and the WAL
pragmas. Use the ORM for normal access, or the raw-sqlite helper when you
genuinely need a cursor.

```python
# ✗ bad
import sqlite3
conn = sqlite3.connect("regin.db")

# ✓ good — ORM
from lib.orm import SessionLocal
with SessionLocal() as session:
    ...

# ✓ good — raw path (sets row_factory + WAL pragmas + DB_PATH)
from lib.orm.engine import get_connection
conn = get_connection()
```

See `references/python-conventions.md` for copy-paste compliant snippets.
