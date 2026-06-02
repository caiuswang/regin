---
title: "vue-complexity"
description: "Complexity thresholds for Vue 3 SFCs — cyclomatic complexity of the <script>/<script setup> block (per-function and aggregate) plus bespoke template metrics (nesting depth, directive density, conditional/loop branches, element count, binding count). Enforced by the generic BundleEngine on every .vue edit."
procedure: vue-complexity
source_repos: [regin]
manual: true
---

# vue-complexity

Use this skill when writing or editing `.vue` files in this repo. regin runs a
complexity rule bundle on every `.vue` edit and warns when a component crosses
the configured thresholds. Keep edits under these limits, or split the
component.

## What is measured

Two independent families, parsed from the SFC by `@vue/compiler-sfc`:

### Script (`<script>` / `<script setup>`)

Cyclomatic complexity is computed over the script block with `@babel/parser`.
Because `<script setup>` puts logic at module top level (outside any function),
the bundle reports BOTH a per-function score and a whole-script aggregate — the
aggregate is what catches an over-loaded `setup` that per-function tools miss.

| Rule | Metric | Threshold |
|------|--------|-----------|
| `vue.script.cyclomatic-function` | max per-function cyclomatic complexity | 15 |
| `vue.script.cyclomatic-module` | whole-script aggregate cyclomatic complexity | 130 |
| `vue.script.function-length` | longest function, in lines | 60 |

### Template (`<template>`)

No off-the-shelf tool computes these; they are bespoke metrics over the
`@vue/compiler-dom` template AST.

| Rule | Metric | Threshold |
|------|--------|-----------|
| `vue.template.max-depth` | element nesting depth | 10 |
| `vue.template.directive-density` | total `v-*` directives | 130 |
| `vue.template.conditional-loop` | `v-if`/`v-else-if`/`v-for` branches | 45 |
| `vue.template.node-count` | template element count | 180 |
| `vue.template.binding-count` | `{{ }}` interpolations + bound attrs | 100 |

Thresholds are calibrated to roughly the 90th–95th percentile of this repo's
own `frontend/src/**/*.vue` files (see `references/calibration.json`), so they
flag genuine outliers — not the median component.

## How to apply

When a warning fires, reduce the offending metric rather than suppressing it:

- **High script complexity** → extract helpers, collapse nested conditionals,
  or move logic into a composable (`use*`) / plain module.
- **High template complexity** → extract inner blocks into child components or
  slots; push branching into `computed` state.

## Verify locally

Recompute the metrics for the whole frontend and re-derive thresholds:

```bash
cd ~/.local/share/regin/patterns/vue-complexity
node references/calibrate.mjs /path/to/repo/frontend/src
```

Run a single checker against one file (the runner's JSON-stdin contract):

```bash
echo '{"file_path":"/abs/path/Foo.vue","rule":{"checker":"template_max_depth","options":{"threshold":10}}}' \
  | node bin/vue-complexity-runner.mjs
```
