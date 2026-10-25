## Reported

Declaring a feature currently ends with a spec written twice: thin prose in the
feature row, and a long `docs/<name>-spec.md` the row then points at. The file
is the one that rots — nothing re-reads it when the design changes, it is not
what a run's prompt carries, and `docs/` grows a file per feature forever.
Stop writing it. The feature row is the spec.

## Spec

**Invariant.** A feature's spec is `features.body`. The repo holds a spec file
only when the user asked for one; the pipeline never writes one on its own.

**Gap that forces the file today.** Nothing can write the spec onto an existing
feature from a session. `regin feature new --spec` sets it at birth, and the
web PATCH sets it from a browser; a CLI session that has just argued the design
out has no way to store it, so it writes a file instead. Close that:

| Command | Does |
|---|---|
| `regin feature spec <ID>` | prints the current spec |
| `regin feature spec <ID> --body "<text>"` | replaces it |
| `regin feature spec <ID> --file <path>` | replaces it from a file (`-` = stdin) |
| `regin feature spec <ID> --clear` | empties it |

`--file` matters more than `--body`: a real spec is thousands of characters
with newlines, and argv is the wrong pipe for it.

**Refusals** (exit 2, row unchanged): unknown id; a spec over 64,000 chars;
`--body` with `--file`; `--clear` with either.

## Acceptance

- `feature spec <id>` with no flags prints the stored body verbatim.
- `--body` replaces it and the row reads back changed.
- `--file` reads the spec off disk; `--file -` reads stdin.
- `--clear` leaves the body NULL.
- over-long body → exit 2, message names the limit, row unchanged.
- unknown id → exit 2.
- two write sources at once → exit 2, row unchanged.

## Also in scope

- The skill and `docs/feature-pipeline.md` say the spec lives on the feature
  row and that no spec file is written unless the user asks; CLAUDE.md +
  AGENTS.md carry the one-line rule.
- `docs/bash-edit-diff-spec.md` (feature #71's spec) is folded into #71's body
  and deleted — the old file this rule retires.

## Non-goals

- Injecting the feature's spec into every run prompt. A run already carries the
  task's body; copying the whole feature spec into each attempt is the
  duplication this feature is removing, pointed the other way.
- Sweeping the other `docs/*-design.md` files. They are design records the user
  asked for, not pipeline output.