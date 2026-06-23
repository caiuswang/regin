# Rule-trigger audit — 2026-06-23

One-time audit of the live `rule_triggers` table against the **current** code,
to separate true principle-violations from false positives. Method: take the
latest check per `(rule_id, file_path)` (triggers only fire at edit-time, so a
stale row may describe code that has since been refactored), then re-run the
actual checkers / `radon` / grep against the working tree.

**Conclusion: no current code truly violates the rules.** Every recent trigger
is already-fixed, accepted-by-policy, or a false positive. One checker bug was
the systemic cause of the largest false-positive class and was fixed (see
below).

## Per-rule findings

| Rule(s) | Verdict | Evidence |
|---|---|---|
| `py_bare_except`, `py_print_call`, `py_raw_sqlite_connect`, `py_activity_logger_forbidden_level` (GritQL) | **Clean** | No true matches in `lib/ web/ hook_manager/ cli/`. FPs only: `lib/doctor.py:81` `sqlite3.connect(':memory:')` is a throwaway FTS5 capability probe (not the canonical DB); the test-file sqlite hit is test code; the `log.info/.debug` hits in `lib/topics/graph_io.py` & `apply.py` are stdlib `logging.getLogger(__name__)`, not the activity-logger wrapper (the rule keys on the variable name `log`). |
| `python.cyclomatic-complexity.c` (radon) | **Accepted** | `radon cc -n D` over `lib/ web/ hook_manager/ cli/` is empty — zero grade-D+ functions. Everything is ≤ grade C, the project's stated stopping point. The engine flags `>= C` (CC≥11) at `warn`; these are advisory, not gate failures. |
| `icon_button_requires_label` (the only **error** rule) | **Clean** | 0 current violations across all 148 `.vue` files. Its one historical trigger (`frontend/src/RuleProbe.vue`) is a deleted probe fixture. |
| `avoid_native_alert_dialogs`, `avoid_native_confirm_dialogs` | **Clean (regin)** | Only ever triggered in *other* repos (`claude-term`, `oh-my-pi`); zero in regin. |
| `clickable_card_needs_affordance` | **False positive** (4 files) | All are modal scrims (`ConfirmDialog.vue`, `ReposView.vue`, `CommandPalette.vue` overlay — `@click.self`/`@mousedown.self` click-outside-to-close) or a lightbox image with an intentional `cursor-zoom-in` (the rule hardcodes `cursor-pointer`). None are interactive cards. |
| `prefer_button_primitive` (and `prefer_select`/`checkbox`) | **Accepted** | Explicitly an *incremental, non-blocking* migration nudge — raw `<button>` → `<Button>` as each file is touched. Not a violation to fix in bulk. |
| `focus_visible_styling_coverage` | **False positive → fixed at source** | Was the #1 trigger class (~70 files). **100% were false positives**: `isInteractiveElement` lowercased the tag, so the PascalCase `<Button>` primitive matched as a native `<button>` and was flagged for lacking an inline `focus-visible:` utility — even though `<Button>` owns its focus ring via the global `.btn:focus-visible` token (`style.css:1540`) and its own cva base. After the fix, the checker reports **0 matches across all 148 `.vue` files**. |

## Fix applied

`example/rule/frontend-style-convention/lib/ast-utils.mjs` (and the deployed
mirror under `~/.local/share/regin/patterns/frontend-style-convention/`):
`isInteractiveElement` now matches native tags **case-sensitively** (`node.tag`,
no `.toLowerCase()`), so PascalCase components are excluded. This mirrors the
sibling `prefer_ds_primitive` checker, which was already case-sensitive for the
same reason.

Verified (independent adversarial review): native `<button>`,
`<div role="button">`, `<span data-clickable="true">`, and `<a href>` without
focus styling are **still flagged** (no false negative); `<Button>` and
focus-styled native controls are **not** flagged. The only behavioral delta is
uppercase/PascalCase `button`/`a`, which Vue treats as components, not native
controls — so there is no WCAG 2.4.7 regression.

## Known orthogonal gap (not fixed here)

`<a @click="…">` with no `href`/`:href` (a JS-driven link) is not flagged by
`focus_visible_styling_coverage` — the `a` branch only matches an `href` prop or
a `bind` directive. This predates and is independent of the fix above; worth a
separate ticket if JS-driven anchors are in use.
