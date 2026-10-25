# F153 — Rewrite regin's introduction around what regin is now

## Intent

regin's public introduction (README, PRODUCT.md, the website at regin.ccday.top, and the
one-line self-descriptions in CONTEXT/CLAUDE/AGENTS/ARCHITECTURE/CLI help) still sells
regin as **"harness infrastructure — Agent = Model + Harness"**: patterns that guide the
agent, rules that enforce, plus traces and memory. The product has since moved on.
The dashboard's top nav group is now **Workbench (Pipeline + Inbox)**, and the live DB
holds 153 features, 441 tasks and 178 defects. Since July, the commits touching the
pipeline, tasks, live, agent-sdk, launch, kimi and acp scopes outnumber the ones touching
rules and patterns by roughly 10 to 1. A visitor who reads the current README gets no idea
that regin plans features, launches and steers agent runs on several providers, or counts
defects against a done-claim.

This feature rewrites the introduction so that its first screen describes the product that
exists. Harness-era capabilities (patterns/skills, rule engines, memory, topic wikis) are
kept and described as one supporting layer, not the frame.

Non-goals:
- No change to product behaviour, schema or any command's semantics. The non-doc edits are
  self-description strings (the CLI help, the `cli/regin.py` docstring, the `pyproject.toml`
  description, the website `<title>`/meta) and one dashboard default: the Pipeline shell's
  theme defaults to **Pixel** (`registry.js` `DEFAULT_THEME`), with classic kept as the eagerly
  bundled fallback, so the site's screenshots show what a new install shows.
- The design records under `docs/*-design.md`, `docs/research/` and `docs/trace/` are not
  rewritten. They record decisions as of the day they were written.
- Repo-root clutter (`*.png`, `think.md`, `iterate-hook-test.md`, `regin.zip`, `TODO.md`)
  is out of scope. It is listed as a follow-up, not deleted here.
- No new branding: name, logo, palette and fonts stay. `DESIGN.md` is untouched.

## Positioning (decision the operator ratifies at spec approval)

```
Positioning := {
  category : "workbench for building software with AI coding agents"   -- replaces "harness infrastructure"
  loop     : Spec → Run → Watch → Count                                 -- the one mental model, replaces "Agent = Model + Harness"
  layers   : [Workbench, Runs, Observability, Guidance]                 -- order = order of appearance on every surface
}
```

| Layer | What it covers (existing features only) | Dashboard home |
|---|---|---|
| Workbench | feature → spec → acceptance items → tasks/slices → done-claim; defect ledger; rounds-to-done; `regin feature/task/defects` | `/overview` Pipeline, `/inbox` |
| Runs | arming and launching task runs (Claude via Agent SDK; Kimi via ACP or tmux); `/live` steer/stop; mobile card; agent bridge; send_to_user inbox and push channels | `/live`, `/inbox` |
| Observability | session trace, `regin stats time/session/sessions`, session grader, audit | `/trace`, `/audit` |
| Guidance | patterns & skills, rule engines in hooks, cross-session memory, topic wikis | `/patterns`, `/rules`, `/memory`, `/repos/:name/topics` |

Recommended tagline: **"Plan the feature, run the agents, see what they did, count what they got wrong."**
The operator can replace the wording at approval. The loop and the layer order are what
the build is held to.

## Surfaces and what each must say

| Surface | Change | Must not |
|---|---|---|
| `README.md` | Rewrite: tagline, beta banner (kept), one section per layer in table order, quick start, supported providers (Claude full; Kimi for task runs, with its known limits as a link), further reading | Include step-by-step operational procedures. The "Run a task with Kimi" walkthrough moves to `docs/setup.md` |
| `PRODUCT.md` | Rewrite Product Purpose, Positioning, Conversion & proof (belief ladder, 10-second line), Design Principle 5 ("one mental model") to the new loop | Keep "Agent = Model + Harness" as the remembered line |
| `CONTEXT.md` line 3 | One-line self-description → new category | Touch the glossary definitions |
| `CLAUDE.md` + `AGENTS.md` lines 1 and 3 | Same one-line change, byte-identical in both files. The two files used to differ in their `# CLAUDE.md` / `# AGENTS.md` heading and their mirror sentence, so both now carry `# Agent instructions` and one shared sentence naming both files | Diverge (AGENTS.md is a verbatim mirror) |
| `ARCHITECTURE.md` "How It Works" | Replace the diagram and intro paragraph with one covering all four layers, linking to the existing per-subsystem sections | Restructure or rewrite the subsystem sections below it |
| `docs/setup.md` | Absorb the Kimi walkthrough (done in S1 as `## Run a task with Kimi`, so README could drop it without losing the text). Regenerate "CLI reference" from the actual command tree | Drop existing setup steps |
| `cli/app.py` help, `cli/regin.py` docstring, `pyproject.toml` description | Replace "Pattern reference system for AI Agents" with the new category | Change any command |
| `website/` | See below | Use mockups or invented numbers |

Website shape:

```
Nav := [Introduction, Getting Started, Pipeline*, Runs*, Dashboard, Guidance†, Architecture, Configuration, CLI]
  * new page
  † "Topic Wikis" becomes "Guidance". It covers patterns/skills, rules and memory, and links
    to the existing /topics/* sub-pages, which stay. The routes /topics and /topics/* keep resolving.
```

| Website file | Change |
|---|---|
| `index.html`, `router.js` `BASE_TITLE` | title/description/og meta → new category |
| `HomeView.vue` | hero = tagline + loop; "Two complementary mechanisms" section → the four layers |
| `content/features.js` | `PILLARS` re-cut to the layers. Each artifact is real output (named trace id/date, as today). Rule push-back, trace and memory artifacts may be kept under Guidance/Observability |
| `content/cli.js` | `COMMAND_GROUPS` covers every top-level group `regin --help` lists |
| new `PipelineView.vue` + `content/pipeline.js` | The workbench loop end to end, told through one real feature: #139, the Pixel theme (spec → slice table → task graph → runs → defects → ship → ledger), every number read off `regin feature ledger 139` and its spec. Pixel-theme shots, light+dark: Overview scoped to the regin project, the Tasks graph of #139, #139's defects, Features |
| new `RunsView.vue` | Arm, launch, follow and steer. Shots: the Pixel Arm dialog and `/live`, light+dark |
| `ArchitectureView.vue` | The four-layer diagram from ARCHITECTURE.md, modules grouped by layer, the shared capture vocabulary, event-derived counts, the Guidance internals (anchors `#tracing`, `#feedforward`, `#rule-engines`, `#memory` kept for inbound links), databases |
| `DashboardView.vue`, `HomeView.vue` | The tour opens with the Pipeline (sidebar order Workbench → Observability → Library → Engineering); home shows the regin-scoped Overview and uses #139's ledger as the Workbench artifact |
| `README.md` (website) | Content-file list updated |

## Invariants

- I1: No surface in the table above uses "harness" as regin's category, tagline, page title
  or meta description. The word may still name the hook/rule mechanism inside Guidance
  prose and ARCHITECTURE subsystem sections.
- I2: Every layer appears in the same order (Workbench, Runs, Observability, Guidance) in
  README, PRODUCT.md, the website home and the website nav.
- I3: Every claim about a capability names something that exists today: a route in
  `frontend/src/router.js`, a CLI command in `regin --help`, or a section in
  ARCHITECTURE.md. Experimental features (mobile remote control, Kimi) keep an explicit
  experimental/limits marker.
- I4: `CLAUDE.md` and `AGENTS.md` stay byte-identical.

## Acceptance

```gherkin
Feature: the introduction describes the current product

  Scenario: harness is no longer the frame
    When I grep README.md PRODUCT.md website/index.html website/src/router.js website/src/views/HomeView.vue website/src/content for "Agent = Model + Harness" and "harness infrastructure"
    Then there are 0 matches

  Scenario: the pipeline leads
    When I read README.md top to bottom
    Then the first capability section is the Workbench (features, tasks, defects)
    And the four layer sections appear in the order Workbench, Runs, Observability, Guidance

  Scenario: CLI self-description is current
    When I run `.venv/bin/python cli/regin.py --help`
    Then the summary line no longer says "Pattern reference system"
    And pyproject.toml's description matches it in substance

  Scenario: CLI reference is complete
    Given the top-level command list printed by `regin --help`
    Then every group command in it (feature, task, defects, stats, hooks, bridge, grade, messages, events, gate, goal, memory, wiki, schema, logs, trace, topics, rules, pattern, skills, users) appears in docs/setup.md "CLI reference"
    And appears in website/src/content/cli.js COMMAND_GROUPS

  Scenario: Kimi walkthrough lives in setup, not README
    Then README.md contains no "tmux -L regin-bridge attach" line
    And docs/setup.md contains the walkthrough, with its known-limits list intact

  Scenario: mirror holds
    Then `cmp CLAUDE.md AGENTS.md` exits 0

  Scenario: website builds and routes resolve
    When I run `cd website && npm run build`
    Then it exits 0
    And /, /getting-started, /pipeline, /runs, /dashboard, /guidance, /architecture, /configuration, /cli, /topics and /topics/routing each render a non-NotFound page in `npm run preview`

  Scenario: new pages show the real product
    Then PipelineView and RunsView each reference at least one image file under website/src/assets/shots/ captured from the dashboard
    And no shot is a mockup

  Scenario: README links resolve
    When I check every relative link in README.md
    Then each target file exists
```

## Plan

**Base: `f153/integration`** (branched from `master`; docs and website only, no schema change).
Merging S3/S4 to `master` auto-deploys the live site (`.github/workflows/deploy-website.yml`),
so the integration branch lands on `master` only after the operator has looked at `npm run preview`.

| # | Slice | Delivers | Spec refs | Tests | Schema | Depends on | Base | Verify |
|---|---|---|---|---|---|---|---|---|
| S1 | Positioning text | README.md rewritten to the four layers (Kimi walkthrough removed, links checked); PRODUCT.md purpose/positioning/proof/principle 5; CONTEXT, CLAUDE, AGENTS one-liners (byte-identical mirror); ARCHITECTURE "How It Works" diagram + intro; CLI help, `cli/regin.py` docstring, `pyproject.toml` description | Positioning, Surfaces (README, PRODUCT, CONTEXT, CLAUDE/AGENTS, ARCHITECTURE, CLI rows), I1, I2, I3, I4 | test_harness_is_no_longer_the_frame, test_the_pipeline_leads, test_cli_self_description_is_current, test_mirror_holds, test_readme_links_resolve | — | — | f153/integration | `cmp CLAUDE.md AGENTS.md && .venv/bin/python cli/regin.py --help` |
| S2 | Setup + CLI reference | "CLI reference" regenerated from the `regin --help` command tree (the Kimi walkthrough already landed in docs/setup.md in S1; S2 only confirms it with its test) | Surfaces (docs/setup.md row), I3 | test_cli_reference_is_complete, test_kimi_walkthrough_lives_in_setup_not_readme | — | S1 | f153/integration | `.venv/bin/python cli/regin.py --help` |
| S3 | Website copy + IA | index.html and router `BASE_TITLE` meta; new routes /pipeline and /runs; Topic Wikis renamed Guidance with /topics and /topics/* kept; HomeView hero = tagline + loop and the four layers; features.js `PILLARS` re-cut; cli.js `COMMAND_GROUPS` covers every top-level group; website README content list | Website shape, Website file table (index, router, HomeView, features.js, cli.js, README), I1, I2, I3 | test_harness_is_no_longer_the_frame, test_cli_reference_is_complete, test_website_builds_and_routes_resolve | — | S1 | f153/integration | `cd website && npm run build` |
| S4 | Website Pipeline + Runs pages | PipelineView.vue and RunsView.vue, each with ≥1 real light+dark dashboard screenshot (`/overview` ledger, `/live`) under website/src/assets/shots/ | Website file table (PipelineView, RunsView rows), I3 | test_new_pages_show_the_real_product, test_website_builds_and_routes_resolve | — | S3 | f153/integration | `cd website && npm run build` |
| S5 | integrate + verify | Merge S1–S4 on f153/integration; run every acceptance scenario (greps, `cmp`, `regin --help`, README link check, website build + preview route check) | Acceptance | test_harness_is_no_longer_the_frame, test_the_pipeline_leads, test_cli_self_description_is_current, test_cli_reference_is_complete, test_kimi_walkthrough_lives_in_setup_not_readme, test_mirror_holds, test_website_builds_and_routes_resolve, test_new_pages_show_the_real_product, test_readme_links_resolve | — | S2, S4 | f153/integration | `cmp CLAUDE.md AGENTS.md && cd website && npm run build` |

## Follow-ups (not built here)

- Clean the repo root of the stray files (`sessions-*.png`, `think.md`, `iterate-hook-test.md`, `regin.zip`).
- Rename the `regin-plugin`/skill descriptions that still say "procedure guide from regin" if the operator wants the new framing there too.



