# example/rule

Importable example pattern bundles that carry rules — used to exercise regin's
"import a bundle, activate its rules" path end to end. Two engine flavors:

- `regin-python-conventions/` — **GritQL** rules, activated through the existing
  `grit` engine on import.
- `frontend-style-convention/` — a **`regin-bundle/v1`** rule pack (Node/AST
  checkers) run by the generic `BundleEngine`, activated by registering it as a
  custom `kind: bundle` rule engine.

## `regin-python-conventions/`

A pattern bundle (regin-skillhub `v1` layout) with **4 Python GritQL rules**
targeting regin's own conventions:

| Rule | Severity | Catches |
|------|----------|---------|
| `py_bare_except` | warn | bare `except:` (swallows `KeyboardInterrupt`/`SystemExit`) |
| `py_print_call` | warn | `print()` in library code (use `lib/activity_log.py`) |
| `py_activity_logger_forbidden_level` | error | `log.info()`/`log.debug()` on the activity-logger wrapper |
| `py_raw_sqlite_connect` | warn | `sqlite3.connect()` (use `SessionLocal()`/`get_connection()`) |

Layout: `SKILL.md` (shim) + `content.md` (guide) + `manifest.json` +
`references/` + `scripts/` (the grit runner) + `.grit/` (`rules.json` and
`patterns/python/regin_python_checks.grit`).

### Import it

```bash
# Zip the folder (bundle files must sit at the archive root), then import:
( cd example/rule/regin-python-conventions && zip -qr -X /tmp/regin-python-conventions.zip . )
.venv/bin/python cli/regin.py pattern import /tmp/regin-python-conventions.zip

# Activate enforcement: push the pattern (globally, then to a project so the
# rules land in that repo's .grit/ and the PostToolUse hook runs them).
.venv/bin/python cli/regin.py skills push --id regin-python-conventions
```

Import installs the `.grit` sources into the active grit dir, regenerates the
rule index, and redeploys the `grit-rules` skill. The rules stay **disabled**
until the pattern guide is deployed (guide-gating), then enforce on `.py`
edits. A project deploy also syncs the rules into the target repo's `.grit/`.

## `frontend-style-convention/`

A `regin-bundle/v1` rule pack of **8 frontend rules** for Vue/CSS/JS UI, run by
the generic `BundleEngine` via a Node runner + checker modules:

| Rule | Severity | Catches |
|------|----------|---------|
| `icon_button_requires_label` | error | icon-only buttons with no accessible name |
| `clickable_card_needs_affordance` | warn | clickable surfaces missing pointer + hover/focus affordance |
| `avoid_raw_hex_in_templates` | warn | raw hex colors where design tokens exist |
| `heading_hierarchy_skips` | warn | skipped heading levels |
| `focus_visible_styling_coverage` | warn | interactive elements without focus-visible styling |
| `avoid_native_alert_dialogs` | warn | native `alert()` instead of unified feedback |
| `avoid_native_confirm_dialogs` | warn | native `confirm()` instead of `ConfirmDialog` |
| `select_input_in_flex_wrap_row` | warn | inline `.input` in a flex-wrap row without a width override |

Layout: `SKILL.md` (shim) + `content.md` (guide) + `manifest.json` +
`regin-bundle.yaml` (the bundle manifest) + `accessibility.yaml` / `layout.yaml`
(rule definitions) + `checkers/` (Node checker modules) + `bin/` (the runner) +
`lib/` (shared AST utils) + `references/` + `package.json` (declares the `vue`
dep the checkers parse with). `node_modules/` is **not** shipped — install it
after import.

### Import and register it

```bash
# Zip the folder (bundle files must sit at the archive root), then import:
( cd example/rule/frontend-style-convention && zip -qr -X /tmp/frontend-style-convention.zip . )
.venv/bin/python cli/regin.py pattern import /tmp/frontend-style-convention.zip

# The checkers need their npm deps once, in the imported bundle dir:
( cd ~/.local/share/regin/patterns/frontend-style-convention && npm install )
```

Unlike the GritQL example, this bundle is **not** auto-discovered (regin runs
with `bundle_autoload: false`). Register it as a custom rule engine by adding an
entry to `rule_engines` in `settings.json`, then restart `regin serve`:

```json
{
  "id": "frontend-style-convention",
  "kind": "bundle",
  "bundle_root": "~/.local/share/regin/patterns/frontend-style-convention"
}
```

The `BundleEngine` then runs the checkers on every Vue / CSS / JS / TS edit via
the PostToolUse hook. The rules surface in `/api/rules` (and the pattern detail
page) grouped under the `frontend-style-convention` guide.
