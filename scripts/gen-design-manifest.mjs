#!/usr/bin/env node
/**
 * gen-design-manifest — emit a machine-readable inventory of the frontend
 * design system to `frontend/design-system.json`.
 *
 * Why: the vocabulary is ~290 custom properties, ~365 hand-written classes
 * and 15 `components/ui/` primitives spread across `style.css` and the SFCs.
 * With no index, an agent rediscovers it by grep — measured at ~12 CSS/token
 * greps per UI session, with `style.css` re-read up to 15 times in one
 * session. One manifest read replaces all of it.
 *
 * Static analysis only: `cva()` variant tables and `defineProps` come off the
 * AST, tokens and class names off the PostCSS tree. Nothing is executed, so
 * this is safe to run in a hook or a build step.
 *
 * Usage: node scripts/gen-design-manifest.mjs [--out <path>] [--check]
 *   --check  exit 1 if the on-disk manifest is stale (for CI / a Stop hook)
 */

import { readFileSync, writeFileSync, readdirSync, statSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { createRequire } from 'node:module'
import { dirname, resolve, join, relative } from 'node:path'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(HERE, '..')
const FRONTEND = join(ROOT, 'frontend')
const SRC = join(FRONTEND, 'src')

// Resolve the frontend's own installs rather than this script's — regin has
// no package.json of its own at the repo root.
const require = createRequire(join(FRONTEND, 'package.json'))
const { parse: parseSFC } = require('@vue/compiler-sfc')
const acorn = require('acorn')
const postcss = require('postcss')

// ---- args ------------------------------------------------------------------
const argv = process.argv.slice(2)
const outFlag = argv.indexOf('--out')
const OUT = outFlag !== -1 ? resolve(argv[outFlag + 1]) : join(FRONTEND, 'design-system.json')
const CHECK = argv.includes('--check')

// ---- fs helpers ------------------------------------------------------------
function walk(dir, ext, out = []) {
  for (const name of readdirSync(dir)) {
    if (name === 'node_modules' || name.startsWith('.')) continue
    const p = join(dir, name)
    const st = statSync(p)
    if (st.isDirectory()) walk(p, ext, out)
    else if (name.endsWith(ext)) out.push(p)
  }
  return out
}
const rel = (p) => relative(FRONTEND, p)

// ---- AST helpers -----------------------------------------------------------
const keyName = (node) => node.key
  ? (node.key.name ?? node.key.value ?? null)
  : null

function objectKeys(objNode) {
  if (!objNode || objNode.type !== 'ObjectExpression') return []
  return objNode.properties.map(keyName).filter(Boolean)
}

function findProp(objNode, name) {
  if (!objNode || objNode.type !== 'ObjectExpression') return null
  const hit = objNode.properties.find((p) => keyName(p) === name)
  return hit ? hit.value : null
}

/** Depth-first walk over an ESTree AST, visiting every object with a `type`. */
function walkAst(node, visit) {
  if (!node || typeof node !== 'object') return
  if (Array.isArray(node)) { for (const n of node) walkAst(n, visit); return }
  if (typeof node.type === 'string') visit(node)
  for (const k of Object.keys(node)) {
    if (k === 'type' || k === 'start' || k === 'end' || k === 'loc') continue
    walkAst(node[k], visit)
  }
}

/**
 * Extract the `cva()` variant table and `defineProps` keys from one script
 * body. Returns null when the file declares neither.
 */
function analyseScript(code) {
  let ast
  try {
    ast = acorn.parse(code, {
      ecmaVersion: 'latest', sourceType: 'module', allowAwaitOutsideFunction: true,
    })
  } catch {
    return null // not parseable as plain JS (e.g. TS syntax) — skip, don't crash
  }
  let variants = null
  let defaults = null
  let props = null
  walkAst(ast, (node) => {
    if (node.type !== 'CallExpression') return
    const callee = node.callee
    if (callee.type === 'Identifier' && callee.name === 'cva') {
      const config = node.arguments[1]
      const vObj = findProp(config, 'variants')
      if (vObj && vObj.type === 'ObjectExpression') {
        variants = {}
        for (const group of vObj.properties) {
          const g = keyName(group)
          if (g) variants[g] = objectKeys(group.value)
        }
      }
      const dObj = findProp(config, 'defaultVariants')
      if (dObj && dObj.type === 'ObjectExpression') {
        defaults = {}
        for (const p of dObj.properties) {
          const k = keyName(p)
          if (k && p.value.type === 'Literal') defaults[k] = p.value.value
        }
      }
    }
    if (callee.type === 'Identifier' && callee.name === 'defineProps') {
      const arg = node.arguments[0]
      if (arg && arg.type === 'ObjectExpression') props = objectKeys(arg)
    }
  })
  if (!variants && !props) return null
  return { variants, defaults, props }
}

function analyseComponent(file) {
  const raw = readFileSync(file, 'utf8')
  const { descriptor, errors } = parseSFC(raw, { filename: file })
  if (errors && errors.length) return { file: rel(file), parseError: true }
  const code = [descriptor.script?.content, descriptor.scriptSetup?.content]
    .filter(Boolean).join('\n')
  if (!code.trim()) return null
  const info = analyseScript(code)
  if (!info) return null
  const name = file.split('/').pop().replace('.vue', '')
  return {
    name,
    file: rel(file),
    ...(info.variants ? { variants: info.variants } : {}),
    ...(info.defaults ? { defaultVariants: info.defaults } : {}),
    ...(info.props ? { props: info.props } : {}),
  }
}

// ---- CSS -------------------------------------------------------------------
/**
 * Tokens are grouped by the scope that declares them, because the same name
 * resolves differently under `@theme`, `:root`, and the dark override — an
 * agent reading one flat map would pick the wrong value for dark mode.
 */
function analyseCss(cssPath) {
  const root = postcss.parse(readFileSync(cssPath, 'utf8'))
  const tokens = { theme: {}, root: {}, dark: {} }
  const classes = new Set()

  root.walkAtRules('theme', (at) => {
    at.walkDecls(/^--/, (d) => { tokens.theme[d.prop] = d.value })
  })
  root.walkRules((rule) => {
    const sel = rule.selector
    if (sel === ':root') {
      rule.walkDecls(/^--/, (d) => { tokens.root[d.prop] = d.value })
    } else if (/data-theme\s*=\s*["']?dark/.test(sel)) {
      rule.walkDecls(/^--/, (d) => { tokens.dark[d.prop] = d.value })
    }
    // Hand-written class layer: capture bare `.name` selectors so the agent
    // can see there's an existing class before writing utilities by hand.
    for (const part of sel.split(',')) {
      const m = part.trim().match(/^\.([a-z][a-z0-9-]*)$/i)
      if (m) classes.add(m[1])
    }
  })
  return { tokens, classes: [...classes].sort() }
}

// ---- build -----------------------------------------------------------------
const uiDir = join(SRC, 'components', 'ui')
const primitives = walk(uiDir, '.vue')
  .map(analyseComponent)
  .filter(Boolean)
  .sort((a, b) => a.name.localeCompare(b.name))

const sharedDir = join(SRC, 'components')
const sharedFiles = readdirSync(sharedDir)
  .filter((n) => n.endsWith('.vue'))
  .map((n) => join(sharedDir, n))
const shared = sharedFiles.map(analyseComponent).filter(Boolean)
  .sort((a, b) => a.name.localeCompare(b.name))

const css = analyseCss(join(SRC, 'assets', 'style.css'))

const inventory = {
  views: walk(join(SRC, 'views'), '.vue').map(rel).sort(),
  components: walk(sharedDir, '.vue').map(rel).sort(),
  composables: walk(join(SRC, 'composables'), '.js').map(rel).sort(),
}

const manifest = {
  $comment: 'Generated by scripts/gen-design-manifest.mjs — do not edit by hand. '
    + 'Read this before writing frontend CSS: it is the authoritative list of '
    + 'tokens, primitives and existing classes to reuse.',
  tokens: css.tokens,
  classes: css.classes,
  primitives,
  shared,
  inventory,
  counts: {
    themeTokens: Object.keys(css.tokens.theme).length,
    rootTokens: Object.keys(css.tokens.root).length,
    darkTokens: Object.keys(css.tokens.dark).length,
    classes: css.classes.length,
    primitives: primitives.length,
    views: inventory.views.length,
    components: inventory.components.length,
  },
}

const json = JSON.stringify(manifest, null, 2) + '\n'

if (CHECK) {
  let current = null
  try { current = readFileSync(OUT, 'utf8') } catch { /* missing counts as stale */ }
  if (current !== json) {
    console.error(`design-system.json is stale — run: node scripts/gen-design-manifest.mjs`)
    process.exit(1)
  }
  console.log('design-system.json is up to date')
} else {
  writeFileSync(OUT, json)
  console.log(`wrote ${rel(OUT)}:`, JSON.stringify(manifest.counts))
}
