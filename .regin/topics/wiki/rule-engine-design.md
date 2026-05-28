# Rule engine design

regin's rule-engine layer is the seam between the harness and any external linter / structural-rewrite tool. The layer's job is narrow but load-bearing: it has to (1) decide which engines are configured for the current process, (2) ask each engine which of its rules apply to a given file, and (3) execute those rules and surface violations back through the PostToolUse hook as a `rule.check` span plus a blocking `additional_context` message to the agent.

> Note on grounding: gitnexus MCP tools were not available in this session, so the design summary below is reconstructed strictly from repository reads. No process names or call-graph edges from gitnexus are cited.

## The adapter contract

Everything funnels through one Protocol in `lib/rule_engines/base.py`:

```
class RuleEngine(Protocol):
    id: str            # instance id, e.g. "grit"
    kind: str          # class key used by the registry factory
    def parse_rules(self) -> list[Rule]: ...
    def applies_to(self, rule, file_path, content) -> bool: ...
    def applicable_rules(self, file_path, content) -> ApplicableRules: ...
    def run(self, rule, file_path, repo_root) -> Violation | None: ...
    def contributed_skills(self) -> list[dict]: ...
    @classmethod
    def reserved_auto_skill_ids(cls) -> frozenset[str]: ...
    def write_index(self) -> dict: ...
```

`Rule` is a frozen dataclass carrying engine-agnostic top-level fields (`id`, `engine`, `summary`, `severity`, `triggers`, `source_file`) plus an open `metadata` mapping where each engine stamps its own keys (`layer`, `guide`, `language`, `checker`, `min_grade`, …). `Violation` is the smallest possible report — `rule_id`, `file_path`, `match_count`, optional `detail`. `ApplicableRules` is the per-file answer: `items` is a list of `(engine_for_run, rule, guide)` triples; `total_in_pool` is the size of the engine's pre-filter rule pool; `repo_root` is the engine's notion of project root for relpath rendering.

The protocol ships a default `applicable_rules` impl: `default_applicable_rules(engine, file_path, content)` in `base.py` reads `engine.parse_rules()`, subtracts operator-disabled ids via `lib.rules.engine_rule_disable.disabled_ids(engine.id)`, applies `engine.applies_to()` per rule, and packages the result. Engines whose rule source is static call this directly (Radon, Bundle); engines that do per-file discovery (Grit walking up for a repo-local `.grit/` dir) implement their own.

## The registry and its three sources

`lib/rule_engines/__init__.py` resolves engines lazily on every `all_engines()` / `get(id)` call (no module-level cache — `reload_settings()` and test monkeypatching both need a fresh read). The module's docstring states the precedence explicitly and `_load_engines` implements it:

1. **Explicit `settings.rule_engines` entries** (with `enabled=True`) — built via `_build_engine(cfg)`. The factory map `_ENGINE_KINDS = {'grit': GritEngine, 'bundle': BundleEngine, 'radon': RadonEngine}` is the closed set of kinds at the moment. Each engine reads its own subset of `RuleEngineConfig` fields (`grit_dir`, `bundle_root`, `language_ids`, `min_grade`, `severity`). `grit_dir` and `bundle_root` are tilde-expanded before being passed in.
2. **Legacy grit fallback** — when `rule_engines` is empty but `settings.grit_dir` is a directory containing `*.grit` files, `_legacy_fallback_engine()` synthesises a single grit engine for backwards compatibility. It logs `rule_engines.legacy_fallback` once per process so the operator knows to migrate.
3. **Auto-discovered bundles** — when `settings.bundle_autoload` is true, `_discovered_bundle_engines()` walks `settings.patterns_dir/*/regin-bundle.{yaml,json}` via `discover_bundles()`. Bundles whose manifest id collides with anything already loaded by (1) or (2) are skipped (`rule_engines.bundle.skipped_collision`); explicit always wins.

If all three sources are empty, regin runs as a generic harness with zero rule enforcement — no PostToolUse chrome, no `/api/rules` entries, no auto-skills. This is an intentional ground state, not an error.

## The three concrete engines

### GritEngine (`lib/rule_engines/grit.py`)

Wraps the external `grit` CLI. Owns the on-disk layout `<grit_dir>/patterns/<language_id>/*.grit` (see `patterns_dir()`). Rule parsing delegates to `lib.utils.grit_parser`; applicability is a manual matcher that partitions a rule's `triggers` into filename globs (anything containing `*` or ending in a language extension) versus content triggers (`@Ann` substrings or bareword regex), then ANDs across kinds and ORs within each kind. Its `applicable_rules` impl is custom: it walks up from `file_path` looking for a repo-local `.grit/rules.json` index (built by `lib/rules/grit_rule_index.py`), and returns an `ApplicableRules` whose `engine_for_run` points at a GritEngine instance configured for the discovered `.grit/` dir. Execution shells out to `grit apply <rule> --dry-run --grit-dir <dir> <file>` with a 20-second per-rule timeout (`_PER_RULE_TIMEOUT_S`) and parses match counts out of combined stdout/stderr. The binary path can be pinned via the `HOOK_MANAGER_GRIT_BIN` env var for tests. The engine contributes one auto-skill (`grit-rules`) and reserves that id so a stale `patterns/grit-rules/` directory left behind cannot resurrect it when grit is no longer configured.

### BundleEngine (`lib/rule_engines/bundle.py`)

Generic adapter for self-describing rule packs. A *bundle* is a pattern directory containing a `regin-bundle.yaml` manifest validated by `BundleManifest` in `lib/rule_engines/manifest.py` (schema id `rule-bundle/v1`, id `^[a-z0-9][a-z0-9-]*$`, language list, `rules_dir`, `checkers_dir`, and a `RunnerSpec` whose `kind` is one of `node` / `python` / `shell`). Rule parsing reads YAML/JSON files under `rules_dir`, skipping `node_modules`, `.git`, `__pycache__`, hidden directories, and a reserved-filenames set (`package.json`, `bundle.json`, `package-lock.json`, plus all `MANIFEST_NAMES`) so a root-level `rules_dir: '.'` doesn't slurp `package.json` or the manifest itself. Applicability is glob-based with a small fanout (`src/`-prefix candidate, `/**/` expansion) and an optional `content_triggers` AND-gate. `applicable_rules` delegates to the protocol's `default_applicable_rules` helper. Execution dispatches to the bundle's runner over a JSON-over-stdin contract:

```
stdin:  {"repo_root": str, "file_path": str, "rule": {id, checker, options, metadata}}
stdout: {"matches": int, "details": [str, ...]}
```

`_argv_for()` resolves `kind` to `node` / `sys.executable` / `bash`. `external_runner_path` is a seam that points a BundleEngine at a runner outside the bundle root (used by the `FrontendUxEngine` compat shim). Bundles never contribute auto-skills — the bundle directory IS the pattern directory, so the skill_registry pattern walk already deploys its `SKILL.md`. The manifest module also ships `scaffold_bundle()` (writes a minimal example) and `validate_bundle()` / `_dry_run_runner()` for the `regin pattern rules-doctor` flow: the dry run finds the first rule's `checker`, builds a representative JSON payload with an empty temp file, invokes the interpreter, and reports any non-zero exit or missing-interpreter error as an actionable diagnostic. `resolve_runner_entry()` enforces path-traversal protection by re-resolving the entry and asserting it lives inside `bundle_root.resolve()`.

### RadonEngine (`lib/rule_engines/radon_engine.py`)

The simplest concrete engine and the canonical example of "no on-disk rule files needed." Its config IS its rule source: `parse_rules()` synthesises one `Rule` whose id is `python.cyclomatic-complexity.<grade>` from `min_grade` (default `C`) and `severity` (default `warn`). Applicability is a pure `.py` extension check. `applicable_rules` calls `default_applicable_rules`. Execution calls `radon.complexity.cc_visit` / `cc_rank` in-process — no subprocess — and reports one `Violation` whose `match_count` is the number of offending blocks, with a `detail` listing up to three named functions and their CC scores. Two thresholds = two engine instances (e.g. `radon-warn` + `radon-strict`). Contributes a `python-complexity` auto-skill and reserves that slug via `reserved_auto_skill_ids()`.

## In-tree example bundles (`example/rule/`)

The repository ships two end-to-end **importable** example bundles under `example/rule/`. They exist precisely to exercise the "import a bundle, activate its rules" path — one per engine flavour. `example/rule/README.md` is the operator-facing walkthrough; both bundles deploy via `cli/regin.py pattern import <zip>` after being zipped with bundle files at the archive root.

### `example/rule/regin-python-conventions/` — GritEngine bundle

A pattern bundle (regin-skillhub `v1` layout) with **4 Python GritQL rules** targeting regin's own conventions: `py_bare_except` (warn), `py_print_call` (warn), `py_activity_logger_forbidden_level` (error — forbids `.info()` / `.debug()` on the activity-logger wrapper), and `py_raw_sqlite_connect` (warn — enforces `SessionLocal()` / `get_connection()`). Layout: `SKILL.md` shim + `content.md` guide + `manifest.json` + `references/` + `scripts/` (the grit runner: `check_grit.sh`, `filter_grit_output.py`, `find_applicable_files.py`) + a `.grit/` directory carrying `rules.json` and `patterns/python/regin_python_checks.grit`. Importing the zip installs the `.grit` sources into the active grit dir, regenerates the rule index via `lib/rules/grit_rule_index.py`, and redeploys the `grit-rules` skill. Rules stay **disabled** until the pattern's guide is deployed (the skill-scope gate enforced by `lib.patterns.pattern_scope.pattern_allowed_for_file`); push the skill with `regin skills push --id regin-python-conventions` to activate enforcement on `.py` edits, then a project-targeted deploy also syncs the rules into the target repo's `.grit/`.

### `example/rule/frontend-style-convention/` — BundleEngine bundle

A self-describing `regin-bundle/v1` rule pack of **8 Vue/CSS/JS UI rules** run by the generic `BundleEngine` through a Node runner + per-rule AST checker modules. Rules cover icon-button accessible names, clickable-card affordance, raw hex colors in templates, heading-level skips, focus-visible styling coverage, native `alert()` / `confirm()` usage, and inline `.input` in flex-wrap rows. The bundle is the canonical proof that BundleEngine handles non-Python toolchains.

Key files (citable on disk today):

* `example/rule/frontend-style-convention/regin-bundle.yaml` — the manifest. `schema: rule-bundle/v1`, `id: frontend-style-convention`, `language_ids: [vue, css, javascript, typescript]`, `rules_dir: '.'` (rule YAMLs sit at the bundle root), `checkers_dir: checkers`, `runner: {kind: node, entry: bin/frontend-ux-runner.mjs, timeout_seconds: 10}`, `severity_default: warn`. The root-level `rules_dir: '.'` is exactly the case BundleEngine's reserved-filename skip-list (`package.json`, `package-lock.json`, manifest names) was written to handle.
* `accessibility.yaml` / `layout.yaml` — the rule definitions (each is one YAML file containing the rule objects: `id`, `summary`, `severity`, `triggers`, `checker`, `options`, `metadata.guide`).
* `checkers/*.mjs` — one ESM checker per rule (`icon_button_accessible_name.mjs`, `clickable_requires_affordance.mjs`, `disallow_raw_hex.mjs`, `heading_order.mjs`, `interactive_requires_focus_visible.mjs`, `disallow_native_alert.mjs`, `disallow_native_confirm.mjs`, `select_input_in_flex_wrap.mjs`).
* `bin/frontend-ux-runner.mjs` — the Node runner that implements the BundleEngine JSON-over-stdin contract: reads `{repo_root, file_path, rule}` from stdin, dispatches by `rule.checker` to the matching module under `checkers/`, writes `{matches, details}` to stdout.
* `bin/check_frontend_ux.sh` — a thin shell wrapper used by some compat paths.
* `lib/ast-utils.mjs` — shared template / AST parsing helpers used by the checkers.
* `package.json` — declares the `vue` dep the checkers parse with; `node_modules/` is **not** shipped, so the operator runs `npm install` in the imported bundle directory once.
* `references/style-convention.md` — the long-form style guide.

Unlike the GritQL example, regin runs with `bundle_autoload: false` in this repo, so this bundle is **not** auto-discovered. Register it as a custom engine in `settings.json`:

```json
{
  "id": "frontend-style-convention",
  "kind": "bundle",
  "bundle_root": "~/.local/share/regin/patterns/frontend-style-convention"
}
```

After `npm install` in the imported bundle dir and a `regin serve` restart, BundleEngine runs the checkers on every Vue / CSS / JS / TS edit through the PostToolUse hook; the rules show up in `/api/rules` (and the pattern detail page) grouped under the `frontend-style-convention` guide. The whole flow — manifest, runner contract, content-trigger gating, dispatch through `_argv_for(kind='node')` — is exactly what `lib/rule_engines/bundle.py` documents; this directory is the working reference.

## Bundle manifest lifecycle

`lib/rule_engines/manifest.py` is the schema authority. `BundleManifest` is a strict pydantic model with field validators for the schema id and id regex. `discover_bundles()` walks `patterns_dir`, skips hidden / `_`-prefixed directories, and logs (rather than raises) on malformed manifests — discovery never crashes the engine boot. `manifest_path()` checks all three `MANIFEST_NAMES` (`regin-bundle.yaml` / `.yml` / `.json`). `RunnerSpec.timeout_seconds` defaults to 10. The scaffold templates ship a working example (`python` runner, one `example_rule`, an `example_checker.py` that line-scans for a `forbidden_token`), so `regin pattern enable-rules` produces a bundle that passes the rules-doctor dry run from the start.

## The PostToolUse driver

`hook_manager/handlers/rule_check.py::handle` is the single place where the layer is exercised on every agent edit:

1. Filter to `Edit` / `Write` / `MultiEdit` tools; extract `file_path` from `tool_response.filePath` or `tool_input.file_path`. Bail unless the file exists on disk.
2. `_engines_for_file(file_path)` calls `rule_engines.all_engines()` and pairs each engine with the first `language_id` whose extension list matches the file — this preserves a clean trace tag (`grit·vue`, not `grit·{vue,css}`) when a single engine registers multiple languages. Extensions come from `lib.languages.get(id).file_extensions` first, with the `_FALLBACK_EXTENSIONS` map (vue, css, js/ts, py, rb, go, rs, sh, json, yaml, md, html) covering languages not yet registered in `lib/languages/`.
3. Read the file and reset the per-pattern deployment cache (`pattern_scope.reset_cache()`) so a deployment toggle between hook invocations takes effect on the very next check. Resolve the canonical `Repo.name` via `repo_for_path(file_path)` for trigger tagging.
4. For each matched engine, call `engine.applicable_rules(file_path, content)`. The handler treats every engine uniformly through this contract — grit's per-file `.grit/rules.json` discovery is hidden inside its `applicable_rules` override, and other engines hit the protocol's default helper. Each returned triple is gated by **skill scope** via `lib.patterns.pattern_scope.pattern_allowed_for_file(rule.metadata.guide, file_path)`. Rules attached to a guide deployed only to certain repos fire only on edits inside those repos; globally-deployed guides fire everywhere; rules with no attached guide are treated as global.
5. Surviving rules are run via `engine.run(rule, file_path, effective_root)`. Each result becomes a `rule_triggers` row (severity, guide, summary, `match_count`, `source: post-edit-hook`) and a line in the violations block. `repo_for_path()` (`lib/rule_engines/repo_scope.py`) resolves the canonical `Repo.name` via longest-prefix matching against registered repos so triggers don't get tagged with `os.path.basename(effective_root)` heuristics.
6. `_emit_rule_check_span()` writes a `rule.check` span carrying `applicable_rules`, `engine_tags`, `total_rules`, `skipped_rules`, and — when the edit happened inside a subagent — the `agent_id` / `agent_type` so the trace projection's third-pass graft can re-parent the span under its `subagent.start`. The span id is stamped onto every `rule_triggers` row from the same check so the `/trace/triggers` drawer can deep-link an event back to its span.
7. The handler returns a `HookResponse` with `additional_context` that either reports `OK` or lists violations with `- <id> (<severity>): <summary> — guide: patterns/<guide>.md` and the ultimatum *"Fix these before claiming the edit is complete."*

## Engine-contributed skills

`contributed_skills()` is the path by which engines inject auto-generated `SKILL.md` files (`grit-rules`, `python-complexity`) into the deployer. `lib/skills/skill_registry.py` walks `rule_engines.all_engines()` and merges what they return; `reserved_auto_skill_ids()` (a *classmethod*, callable without instantiation) is the static guard that keeps those slugs out of the pattern-walk fallback when no instance of the engine is currently configured — without it, a stale directory left under `patterns/grit-rules/` would resurrect the skill as if it were a user pattern. Bundles return `[]` because their bundle directory is already a pattern.

## Operator-facing handles

* `settings.rule_engines: list[RuleEngineConfig]` — the declarative wiring (`lib/settings.py`).
* `settings.grit_dir` — preserved as the legacy single-grit fallback. Empty `rule_engines` + populated `grit_dir` triggers the back-compat path with a one-shot warning.
* `settings.bundle_autoload: bool` — gates the auto-discovery step.
* `lib.rules.engine_rule_disable` — operator-toggleable per-engine rule disables (`/api/rules?engine=…` and similar UI).
* `HOOK_MANAGER_GRIT_BIN` — env override for the grit binary path in tests.

## Add a new rule engine — step-by-step playbook

Three distinct paths cover three distinct needs. Pick by what's actually new: a new tool kind, a new rule pack, or a new language. The two bundles under `example/rule/` are the working references for Paths A (Grit-flavoured) and B (BundleEngine).

### Path A — Wrap a new linter/checker as a new engine kind (Python adapter)

Use when the integration shape doesn't already fit one of the existing kinds: e.g. wrapping `ruff`, `eslint`, `semgrep`, `mypy`, a domain-specific structural checker, or any tool whose rule source can't be expressed as a bundle manifest. Concrete steps:

1. **Author the adapter class** at `lib/rule_engines/<your_kind>_engine.py`. Implement every method on the `RuleEngine` Protocol from `lib/rule_engines/base.py`:
   * Set `kind = '<your_kind>'` on the class. Accept `id`, `language_ids`, `project_root`, plus any engine-specific kwargs in `__init__`.
   * `parse_rules()` — return `list[Rule]`. If rules live on disk, parse them; if the config IS the rule source (Radon-style), synthesise one or more `Rule` objects. Stamp engine-specific keys (`language`, `guide`, threshold values, …) into `Rule.metadata`. If you want operators to filter rule firings to a deployed guide, include a `guide` key whose value is a pattern slug under `patterns/`.
   * `applies_to(rule, file_path, content)` — return `True` when the rule should be evaluated. Keep it pure and cheap; this gets called per-edit.
   * `applicable_rules(file_path, content)` — if your rules are static, just delegate: `return default_applicable_rules(self, file_path, content)`. Only write your own when you do per-file discovery (like Grit walking up to a repo-local `.grit/`).
   * `run(rule, file_path, repo_root)` — invoke the underlying tool (in-process when possible: Radon imports `radon.complexity`; Grit shells out). Return `Violation(rule_id, file_path, match_count, detail)` on match or `None` when clean. Use a per-rule timeout if you shell out.
   * `contributed_skills()` — return a list of `{'id': '<slug>', 'kind': 'auto', 'engine_id': self.id}` dicts if your engine wants to inject an auto-generated `SKILL.md` (the deployer reads engine state to materialise the body). Return `[]` if not.
   * `reserved_auto_skill_ids()` — **classmethod** returning a `frozenset` of every slug the engine kind exclusively owns. The skill_registry uses this to skip those slugs in its pattern-walk fallback when no instance of your engine is currently configured. If you return any slug from `contributed_skills()`, list it here too.
   * `write_index()` — return a dict describing on-disk index artefacts written (or just engine state if there's nothing to write). Used by `/api/rules` reload paths.

2. **Register the class in the factory map** at `lib/rule_engines/__init__.py`:
   ```python
   from lib.rule_engines.your_kind_engine import YourKindEngine
   _ENGINE_KINDS: dict[str, Type] = {
       'grit': GritEngine,
       'bundle': BundleEngine,
       'radon': RadonEngine,
       'your_kind': YourKindEngine,
   }
   ```
   Then add a `_build_engine()` branch that reads `RuleEngineConfig` fields and constructs the instance. Tilde-expand any path fields with `os.path.expanduser(str(...))` the way the grit branch does. Add your kind to the module's `__all__` and the docstring's precedence list if helpful.

3. **Extend `RuleEngineConfig`** (`lib/settings.py`) only if you need new fields. `grit_dir`, `bundle_root`, `language_ids`, `min_grade`, and `severity` are already there as optionals; reuse them when they fit (Radon reuses `min_grade` + `severity`). Add new optionals — typed and defaulted to `None` — only when the existing surface won't carry your config. Keep new fields engine-specific in spirit; the model deliberately collects all engines' fields in one schema.

4. **(Optional) Register language extensions**. If your engine targets a language that isn't yet in `lib/languages/`, either:
   * Add a `Language` definition under `lib/languages/<lang_id>.py` and register it in `lib/languages/__init__.py::_REGISTRY` (preferred; gives a proper `parse_class_metadata` and `framework_hooks` seam), **or**
   * Add an entry to `_FALLBACK_EXTENSIONS` in `hook_manager/handlers/rule_check.py` if you only need extension routing and don't have parser hooks yet. **Easy to miss:** without either of these, the PostToolUse handler will never match your engine for files of that language even though `applies_to()` would have said yes.

5. **Wire it into settings**. Add to `settings.json`:
   ```json
   {
     "rule_engines": [
       {"id": "your_kind", "kind": "your_kind", "language_ids": ["python"], "enabled": true}
     ]
   }
   ```
   Restart the hook process (or call `lib.settings.reload_settings()` in tests) so the next `all_engines()` rebuild picks it up. Engines are not cached — every `all_engines()` call re-reads settings.

6. **Verify**. Trigger an edit on a file your engine should match and confirm:
   * A `rule.check` span appears in the trace with `engine_tags` including your engine id.
   * Violations appear in the `rule_triggers` table with `source='post-edit-hook'`.
   * `regin doctor` reports the engine cleanly (it iterates `all_engines()`).

### Path B — Ship a new rule pack with no Python changes (bundle)

Use when you have a self-contained set of rules + a runner script (Node/Python/Bash) and don't need a new engine kind. `BundleEngine` already handles discovery, parsing, applicability, and JSON-over-stdin dispatch — you supply the manifest and runner. The reference implementation is `example/rule/frontend-style-convention/` (Node runner; copy its `regin-bundle.yaml`, `bin/frontend-ux-runner.mjs`, and `checkers/*.mjs` layout as a template).

1. **Create the bundle directory** under `settings.patterns_dir` (default `~/.local/share/regin/patterns/`): `~/.local/share/regin/patterns/<your-bundle-id>/`. The id must match `^[a-z0-9][a-z0-9-]*$`.

2. **Write `regin-bundle.yaml`** at the bundle root:
   ```yaml
   schema: rule-bundle/v1
   id: your-bundle-id
   language_ids: [python]
   rules_dir: rules
   checkers_dir: checkers
   runner:
     kind: python      # or 'node' or 'shell'
     entry: checkers/runner.py
     timeout_seconds: 10
   severity_default: warn
   description: One-line bundle description
   ```

3. **Author rules** under `<bundle_root>/<rules_dir>/*.yaml` (or `.yml`/`.json`). Each rule file is a YAML/JSON object with at minimum `id`, `summary`, optional `severity`, `triggers` (filename globs and/or content substrings), `checker` (the script name your runner dispatches on), `options`, and `metadata` (use `guide: <pattern-slug>` to scope this rule by skill deployment). `example/rule/frontend-style-convention/accessibility.yaml` is a working multi-rule file.

4. **Author the runner** at the path in `runner.entry`. It reads a single JSON object from stdin and writes one JSON object to stdout:
   ```
   stdin:  {"repo_root": "...", "file_path": "...", "rule": {"id": ..., "checker": ..., "options": {...}, "metadata": {...}}}
   stdout: {"matches": 0, "details": []}
   ```
   `matches > 0` produces a `Violation`. Exit non-zero only on infrastructure errors; the runner contract is to always emit JSON for rule decisions. Stay within `runner.timeout_seconds` (default 10). `example/rule/frontend-style-convention/bin/frontend-ux-runner.mjs` is the canonical Node implementation of this contract.

5. **Pick a wiring strategy** — two equivalent options:
   * **Auto-discovery** (recommended for hub-shipped bundles): leave `settings.bundle_autoload` true. The bundle is picked up automatically on the next `all_engines()` call. The `regin-python-conventions` import flow takes this path implicitly via the grit engine.
   * **Explicit**: add a `rule_engines` entry with `kind: 'bundle'` and `bundle_root: <abs path>`. Explicit always wins on id collision with auto-discovery. The `frontend-style-convention` example uses this path because regin runs with `bundle_autoload: false`.

6. **Doctor the bundle** with `regin pattern rules-doctor` (or call `validate_bundle()` from `lib/rule_engines/manifest.py`). The doctor's `_dry_run_runner()` builds a representative payload from the first rule and invokes the runner against an empty temp file — a clean run means manifest + runner + interpreter are all wired correctly. Use `regin pattern enable-rules` to scaffold a working example bundle (`scaffold_bundle()` in `manifest.py`) and edit from there.

**No code changes touch `lib/rule_engines/__init__.py`, `lib/settings.py`, or `hook_manager/handlers/rule_check.py` for this path** — the bundle is data, not code.

### Path C — Teach an existing engine to handle a new language

Use when the engine kind already supports your tool but you want to extend coverage to a new file type (e.g. add TypeScript to a grit setup that already covers Java).

1. **Register the language** at `lib/languages/<lang_id>.py`: construct a `Language(id=..., file_extensions=(...), parse_class_metadata=..., framework_hooks={...})` and add it to the `_REGISTRY` dict in `lib/languages/__init__.py`. Java and Python ship by default.

2. **Add the language id to the engine's config** in `settings.json`:
   ```json
   {"id": "grit", "kind": "grit", "language_ids": ["java", "typescript"]}
   ```

3. **Lay down rule sources** in whatever location the engine expects — for grit, `<grit_dir>/patterns/typescript/*.grit`; for a bundle, edit the manifest's `language_ids`; for radon, language is implicit.

4. **(Skip-if-registered) Update `_FALLBACK_EXTENSIONS`** in `hook_manager/handlers/rule_check.py` only if you chose not to add a `Language` definition. The handler always tries the `lib.languages` registry first.

## Gotchas worth remembering

* `reserved_auto_skill_ids()` is a **classmethod** on purpose — the skill_registry queries it via the `_ENGINE_KINDS` class map without instantiating the engine. If you implement it as an instance method it will be silently invisible to the deployer.
* `_FALLBACK_EXTENSIONS` in `hook_manager/handlers/rule_check.py` is the easiest source of "my rules never fire" — if the language isn't in `lib.languages` AND not in this fallback, `_engines_for_file()` returns `[]` no matter what `applies_to()` says.
* If your engine's `kind` doesn't match its class entry in `_ENGINE_KINDS`, `_build_engine()` raises `ValueError("unknown rule engine kind")` — the error surfaces at the first `all_engines()` call, not at settings load.
* When `parse_rules()` returns `[]` for a file, the handler emits a `no_applicable_rules` (or `all_rules_out_of_scope`) `rule.check` span and short-circuits — useful signal to look for when debugging "engine is configured but nothing happens."
* Engines are not cached. If you add a debug print to `_load_engines()` it'll fire on every Edit/Write/MultiEdit — that's by design (`reload_settings()` and tests rely on it).
* The `frontend-style-convention` bundle requires a one-time `npm install` inside the imported bundle dir before its Node checkers can run — `node_modules/` is intentionally not packaged into the importable zip.