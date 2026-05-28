# Compliant snippets — regin Python conventions

Copy-paste templates that satisfy all four `regin-python-conventions` rules.

## Structured logging (rules 2 + 3)

```python
from lib.activity_log import get_activity_logger

log = get_activity_logger("patterns")   # a registered feature name
log.read("pattern_loaded", pattern_id=pid)     # read paths  → DEBUG
log.write("pattern_imported", slug=slug)        # write paths → INFO
log.error("import_failed", exc_info=True)       # errors

# Never: log.info(...) / log.debug(...) / print(...) in lib/
```

## Narrow exception handling (rule 1)

```python
try:
    data = json.load(f)
except (OSError, json.JSONDecodeError) as exc:
    log.error("config_read_failed", exc_info=exc)
    raise
```

## Database access (rule 4)

```python
# Normal access — ORM
from lib.orm import SessionLocal, AuthSessionLocal

with SessionLocal() as session:
    doc = session.exec(select(PatternDoc).where(PatternDoc.slug == slug)).first()

# Raw cursor when you really need one — keeps DB_PATH + row_factory + WAL
from lib.orm.engine import get_connection

conn = get_connection()
try:
    rows = conn.execute("select id from pattern_docs").fetchall()
finally:
    conn.close()
```
