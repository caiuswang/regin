#!/usr/bin/env python3
"""Snapshot every features.body and agent_tasks.body into the editor corpus.

The rich editor's round-trip test (`frontend/tests/unit/markdownDoc.test.mjs`)
asserts `serialize(parse(s)) === s` over these files, so they are written as
the exact stored bytes — no newline translation, no trailing-newline fixup.
A body matching a secret pattern has the match replaced by `REDACTED`: the
corpus is committed, and a redacted body still exercises the parser.

Usage:
    .venv/bin/python scripts/export-bodies.py              # → frontend/tests/fixtures/bodies/
    .venv/bin/python scripts/export-bodies.py --out DIR
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import select  # noqa: E402

from lib.orm.engine import SessionLocal  # noqa: E402
from lib.orm.models.features import Feature  # noqa: E402
from lib.orm.models.tasks import AgentTask  # noqa: E402
from lib.patterns.secret_scan import PATTERNS  # noqa: E402

DEFAULT_OUT = Path(__file__).resolve().parent.parent / "frontend" / "tests" / "fixtures" / "bodies"


def redact(body: str) -> str:
    for pattern, _label in PATTERNS:
        body = pattern.sub("REDACTED", body)
    return body


def collect() -> dict[str, str]:
    with SessionLocal() as session:
        features = session.exec(select(Feature.id, Feature.body)).all()
        tasks = session.exec(select(AgentTask.id, AgentTask.body)).all()
    bodies = {f"feature-{fid}.md": body for fid, body in features if body}
    bodies.update({f"task-{tid}.md": body for tid, body in tasks if body})
    return bodies


def write(out: Path, bodies: dict[str, str]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for stale in out.glob("*.md"):
        if stale.name not in bodies:
            stale.unlink()
    for name, body in bodies.items():
        (out / name).write_bytes(redact(body).encode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    bodies = collect()
    write(args.out, bodies)
    sys.stdout.write(f"wrote {len(bodies)} bodies to {args.out}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
