"""The chain tail: the three slices every feature ends with.

Integrate, migrate, verify. They are the same three for every feature —
the work that proves the branches came back together, that the install is
not behind, and that the suite still passes — so a human author writing
them by hand writes three rows of boilerplate and gets one of them subtly
wrong. The first pass appends them instead.

Every command in these rows is read from the project's command table
(`.regin/commands.json`), never spelled here. A hardcoded `pytest` would
make the tail true of regin and of nothing else, which is the whole reason
the command table exists — and a null command is rendered as the reason
the table gives for it, so a project with no e2e suite gets a tail row
that says so rather than one that quietly runs nothing.

The tail is the *first* pass's output: it lands in the spec's table, where
a human can see it and edit it before approving. Transcription reads the
table and knows nothing about tails — a tail conjured at transcription
time would be a plan the spec never carried (Invariant 1).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from lib.activity_log import get_activity_logger
from lib.tasks.commands import DEFAULT_SKILL, ProjectCommands
from lib.tasks.slice_table import SliceRow

log = get_activity_logger("tasks")

#: Joins two commands into one verify line. `&&` and not `;` because the
#: tail's verify is a claim that *both* passed.
COMMAND_JOINER = " && "


@dataclass(frozen=True)
class TailSpec:
    """One tail row, with the command-table keys it renders from."""

    key: str
    title: str
    delivers: str          # `{cmd}` is replaced by the rendered command text
    spec_refs: tuple[str, ...]
    tests: tuple[str, ...]
    #: Command-table keys this row runs, in order. Empty means the row runs
    #: no project command of its own and verifies with the default.
    commands: tuple[str, ...] = ()


#: The three, in chain order. Their `tests` are the acceptance names the
#: spec's own tail carries; a feature whose spec names others overrides the
#: cell by hand after the rows are pasted in.
TAIL_SPECS: tuple[TailSpec, ...] = (
    TailSpec(
        key="integrate",
        title="integrate",
        delivers="检查每一片都已合回 `{base}`，无人落零 commit",
        spec_refs=("Invariant 1",),
        tests=("test_every_slice_merged_back",),
    ),
    TailSpec(
        key="migrate",
        title="migrate + doctor",
        delivers="跑 {cmd}，确认这个安装没落后",
        spec_refs=("R1",),
        tests=("test_migrate_leaves_no_schema_finding",),
        commands=("migrate",),
    ),
    TailSpec(
        key="verify",
        title="verify",
        delivers="全量 {cmd}",
        spec_refs=("Acceptance",),
        tests=("test_full_suite_passes",),
        commands=("test", "e2e"),
    ),
)

TAIL_KEYS = tuple(spec.key for spec in TAIL_SPECS)


def _rendered_commands(spec: TailSpec,
                       commands: ProjectCommands) -> tuple[list[str], list[str]]:
    """The row's runnable commands, and the prose for the ones it has none of."""
    runnable: list[str] = []
    absent: list[str] = []
    for key in spec.commands:
        value = commands.get(key)
        if value:
            runnable.append(value)
        else:
            reason = commands.reasons.get(key) or "本项目没有这条命令"
            absent.append(f"本项目没有 `{key}` 命令：{reason}")
    return runnable, absent


def _delivers(spec: TailSpec, commands: ProjectCommands, base: str) -> str:
    runnable, absent = _rendered_commands(spec, commands)
    rendered = "、".join(f"`{cmd}`" for cmd in runnable) or "—"
    text = spec.delivers.format(cmd=rendered, base=base)
    if absent:
        text += "（" + "；".join(absent) + "）"
    return text


def _verify_cmd(spec: TailSpec, commands: ProjectCommands) -> Optional[str]:
    """The row's Verify cell — its own commands, else the table's default.

    A row with no commands of its own leaves the cell empty rather than
    copying `test` into it: an empty Verify already *means* the project's
    test command (the column's documented default), and writing the value
    out would freeze today's spelling into the spec.
    """
    runnable, _ = _rendered_commands(spec, commands)
    return COMMAND_JOINER.join(runnable) or None


def _next_ordinal(rows: Sequence[SliceRow]) -> int:
    return max((int(row.n[1:]) for row in rows), default=-1) + 1


def _leaves(rows: Sequence[SliceRow]) -> list[str]:
    """The slices nothing else depends on — what integrate waits for.

    Every one of them, not just the last: a table whose graph forks ends in
    several leaves, and a tail hung off only one would integrate while the
    other fork was still running.
    """
    depended_on = {dep for row in rows for dep in row.depends_on}
    return [row.n for row in rows if row.n not in depended_on]


def tail_slice_rows(rows: Sequence[SliceRow], commands: ProjectCommands,
                    base: str, skill: str = DEFAULT_SKILL) -> list[SliceRow]:
    """The three tail rows for a table, keyed on after its highest `S<n>`.

    `rows` is the feature's own slices — the tail is appended to them, and
    passing a list that already contains a tail would number a second one.
    """
    ordinal = _next_ordinal(rows)
    previous = _leaves(rows)
    tail: list[SliceRow] = []
    for offset, spec in enumerate(TAIL_SPECS):
        row = SliceRow(
            n=f"S{ordinal + offset}",
            title=spec.title,
            delivers=_delivers(spec, commands, base),
            spec_refs=list(spec.spec_refs),
            tests=list(spec.tests),
            base=base,
            depends_on=list(previous),
            skill=skill,
            verify_cmd=_verify_cmd(spec, commands),
        )
        tail.append(row)
        previous = [row.n]
    log.write("tail_slices_built", slices=",".join(row.n for row in tail))
    return tail


#: The acceptance names only a tail row carries. A spec may fold the three
#: into fewer rows (one "integrate + verify" slice when nothing migrates), so
#: the tail is recognised by what it proves, not by how many rows it took.
TAIL_TESTS = frozenset(test for spec in TAIL_SPECS for test in spec.tests)


def has_tail(rows: Sequence[SliceRow]) -> bool:
    """Whether the table already ends in a chain tail, however it was folded."""
    return any(TAIL_TESTS.intersection(row.tests) for row in rows)
