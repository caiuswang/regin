# F123 — Bring production Python under the complexity bar

## Intent
Every production Python function passes the radon gate regin already enforces on edit
(grade ≤ B, CC ≤ 10), and no production module exceeds 1000 lines — so an agent editing
any file is not tripped by debt it did not write, and no module is too large to read whole.
Pure refactor: behaviour, public import paths, HTTP routes and CLI surface do not change.

## Non-goals
- Test code (tests/, test_*.py, conftest.py): 44 C-grade blocks, left for a later feature.
- alembic/versions/: frozen history.
- Frontend / Vue complexity (separate vue-complexity engine).
- Changing the radon engine threshold or adding a LOC lint engine.

## Shape
    Scope    = git ls-files '*.py'  minus  (^|/)tests?/ , test_*.py , conftest.py , ^alembic/versions/
    Target   = CCBlock{file, function, cc ≥ 11}  |  BigModule{file, lines > 1000}
    Baseline (2026-09-29) = 99 CCBlock in 56 files (all C, CC 11–19) + 22 BigModule

Slices (one task each, worst-first; each slice = one commit per target):
| # | Slice | Targets |
|---|---|---|
| S1 | web/blueprints CC | 30 blocks (rules/triggers.py, trace/sessions.py, skills.py, …) |
| S2 | lib/ CC | lib/trace, lib/topics, lib/patterns, lib/skills, lib/sync, lib/rule_engines, lib/features, … |
| S3 | hook_manager + cli + scripts CC | incl. the scripts/ ↔ example/rule/…/scripts mirror pair |
| S4 | split modules > 1500 lines | 11 files, starting web/blueprints/trace/sessions.py (3955) |
| S5 | split modules 1001–1500 lines | remaining 11 files |

## Rules
| Condition | Action |
|---|---|
| function CC ≥ 11 | extract helpers / dispatch tables / early returns until CC ≤ 10 |
| module > 1000 lines | split into a package or sibling modules by responsibility; old import path re-exports the public names |
| module is a Flask blueprint | routes, URL rules and blueprint name unchanged; split handlers into sibling modules registered on the same blueprint |
| target has no test covering the touched function | write a characterization test pinning today's output **before** refactoring |
| file has a byte-identical mirror (scripts/ ↔ example/rule/…/scripts) | apply the same change to both; they stay identical |
| edit made by a script (sed/heredoc) instead of Edit/Write | hand-run the radon + grit engines on the file — PostToolUse did not |
| a split creates a new module | it must itself be CC-clean and ≤ 1000 lines |

## Invariants
- `from lib.settings import settings` and every other existing public import path keeps working.
- No behavioural diff: the pre-change test baseline's pass/fail set is unchanged per slice.
- No new time-based logic, compat shims, or comments narrating the refactor.

## Acceptance
Feature: production Python complexity

  Scenario: no production function at radon grade C or worse
    Given the Scope file list
    When I run `xargs .venv/bin/radon cc -s -n C < scope.txt`
    Then it prints no block lines

  Scenario: no production module over 1000 lines
    Given the Scope file list
    When I run `xargs wc -l < scope.txt`
    Then no file other than the "total" row exceeds 1000

  Scenario: the known worst offenders are gone
    Then web/blueprints/trace/sessions.py is ≤ 1000 lines (or a package whose modules each are)
    And web/blueprints/trace/sessions.py:api_sessions has CC ≤ 10

  Scenario: behaviour preserved
    Given the pytest pass/fail set recorded before the slice
    When I run `.venv/bin/python -m pytest -q -n auto --dist=loadfile`
    Then no test that passed before fails after

  Scenario: routes and CLI unchanged
    When I diff `app.url_map` rules and `regin --help` subcommand trees before/after
    Then they are identical

  Scenario: mirrors stay identical
    When I run `diff scripts/filter_grit_output.py example/rule/regin-python-conventions/scripts/filter_grit_output.py`
    Then there is no output (same for find_applicable_files.py)

  Scenario: grit conventions still clean on touched files
    When the grit engine runs on every file the feature touched
    Then it reports no violation

## Build vehicle
`.claude/workflows/complexity-refactor-tier.js` covers S1–S3 (python, grade C, per-dir,
coverage-gated, one commit per target). S4–S5 need a split-per-module run with the rules above.
