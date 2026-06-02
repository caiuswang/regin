// Shared metric walkers for the vue-complexity bundle.
//
// Two independent families:
//   scriptComplexity(scriptContent) — cyclomatic complexity of the JS in a
//     <script>/<script setup> block, per-function AND module-aggregate.
//   templateMetrics(ast)            — structural metrics over the
//     @vue/compiler-dom template AST.
//
// Both are defensive: a parse failure returns an empty/zero result rather
// than throwing, so a malformed edit never breaks the PostToolUse hook.
import fs from 'node:fs'
import { parse as parseSfc } from '@vue/compiler-sfc'
import { parse as parseTemplate, NodeTypes } from '@vue/compiler-dom'
import { parse as parseBabel } from '@babel/parser'

export { NodeTypes }

// ── SFC loading ──────────────────────────────────────────────────────────

// Returns the SFC split into the pieces the checkers need. `scriptContent`
// concatenates `<script setup>` and a plain `<script>` (a component can have
// both); `ast` is the parsed template (null when there's no template).
export function loadVueContext(filePath) {
  const source = fs.readFileSync(filePath, 'utf8')
  const sfc = parseSfc(source, { filename: filePath })
  const d = sfc.descriptor
  const scriptParts = []
  if (d.scriptSetup?.content) scriptParts.push(d.scriptSetup.content)
  if (d.script?.content) scriptParts.push(d.script.content)
  const scriptContent = scriptParts.join('\n')
  const template = d.template?.content || ''
  const ast = template.trim()
    ? parseTemplate(template, { comments: false })
    : null
  return { source, sfc, scriptContent, template, ast }
}

// ── Script cyclomatic complexity ─────────────────────────────────────────

// Babel plugins covering the JS/JSX features a Vue SFC script may use. No
// 'typescript' plugin — our SFCs are plain JS; add it here if lang="ts"
// is ever adopted.
const BABEL_PLUGINS = [
  'jsx',
  'topLevelAwait',
  'importAssertions',
  'explicitResourceManagement',
]

const FUNCTION_TYPES = new Set([
  'FunctionDeclaration',
  'FunctionExpression',
  'ArrowFunctionExpression',
  'ObjectMethod',
  'ClassMethod',
  'ClassPrivateMethod',
])

// Keys never worth recursing into — position bookkeeping and comments.
const SKIP_KEYS = new Set([
  'loc', 'start', 'end', 'range', 'extra',
  'leadingComments', 'trailingComments', 'innerComments', 'comments', 'tokens',
])

// Does this node add a linearly-independent path?
function decisionDelta(node) {
  switch (node.type) {
    case 'IfStatement':
    case 'ConditionalExpression':
    case 'ForStatement':
    case 'ForInStatement':
    case 'ForOfStatement':
    case 'WhileStatement':
    case 'DoWhileStatement':
    case 'CatchClause':
      return 1
    case 'SwitchCase':
      // `default:` (test === null) is not a branch.
      return node.test ? 1 : 0
    case 'LogicalExpression':
      return (node.operator === '&&' || node.operator === '||' || node.operator === '??') ? 1 : 0
    case 'OptionalMemberExpression':
    case 'OptionalCallExpression':
      return node.optional ? 1 : 0
    default:
      return 0
  }
}

function functionName(node, hint) {
  if (node.id?.name) return node.id.name
  if (node.key) {
    if (node.key.name) return node.key.name
    if (node.key.value != null) return String(node.key.value)
  }
  return hint || '(anonymous)'
}

function lineSpan(node) {
  if (!node.loc) return 0
  return node.loc.end.line - node.loc.start.line + 1
}

// Recursively walk the Babel AST. Decision points are attributed to the
// nearest enclosing function (so nested functions don't inflate the outer
// one) and always to the module total. `nameHint` carries an inferred name
// down from VariableDeclarator/AssignmentExpression/property parents so
// arrow functions assigned to a const get a useful label.
export function scriptComplexity(scriptContent) {
  const empty = { moduleCC: 0, functions: [], parsed: false }
  if (!scriptContent || !scriptContent.trim()) return { ...empty, parsed: true }

  let ast
  try {
    ast = parseBabel(scriptContent, {
      sourceType: 'module',
      errorRecovery: true,
      plugins: BABEL_PLUGINS,
    })
  } catch {
    return empty
  }

  const functions = []
  const module = { decisions: 0 }

  function visit(node, currentFn, nameHint) {
    if (!node || typeof node.type !== 'string') return

    const delta = decisionDelta(node)
    if (delta) {
      module.decisions += delta
      if (currentFn) currentFn.cc += delta
    }

    let fnForChildren = currentFn
    if (FUNCTION_TYPES.has(node.type)) {
      const record = {
        name: functionName(node, nameHint),
        cc: 1,
        lines: lineSpan(node),
        line: node.loc?.start?.line ?? 0,
      }
      functions.push(record)
      fnForChildren = record
    }

    // Infer a name hint for an immediately-nested function child.
    let childHint
    if (node.type === 'VariableDeclarator') childHint = node.id?.name
    else if (node.type === 'AssignmentExpression') childHint = node.left?.name || node.left?.property?.name
    else if (node.type === 'ObjectProperty' || node.type === 'Property') childHint = node.key?.name

    for (const key of Object.keys(node)) {
      if (SKIP_KEYS.has(key)) continue
      const value = node[key]
      if (Array.isArray(value)) {
        for (const child of value) {
          if (child && typeof child.type === 'string') visit(child, fnForChildren, childHint)
        }
      } else if (value && typeof value.type === 'string') {
        visit(value, fnForChildren, childHint)
      }
    }
  }

  visit(ast.program, null, undefined)

  return {
    moduleCC: module.decisions + 1,
    functions,
    parsed: true,
  }
}

// ── Template structural metrics ──────────────────────────────────────────

// Directives we classify. Keyed by the @vue/compiler-dom directive `name`
// (the part after `v-`); shorthands `:` and `@` normalize to bind/on.
const COUNTED_DIRECTIVES = ['if', 'else-if', 'else', 'for', 'bind', 'on', 'model', 'slot', 'show']
const CONDITIONAL_LOOP = new Set(['if', 'else-if', 'for'])

function isComponentTag(tag) {
  if (!tag) return false
  // PascalCase or contains a hyphen → custom component, not a native element.
  if (/[A-Z]/.test(tag)) return true
  if (tag.includes('-')) return true
  return false
}

// Walk the template AST collecting all structural metrics in one pass.
export function templateMetrics(ast) {
  const directives = Object.fromEntries(COUNTED_DIRECTIVES.map((d) => [d, 0]))
  const result = {
    depth: 0,
    nodeCount: 0,
    componentCount: 0,
    bindingCount: 0,
    conditionalLoopCount: 0,
    directives,
    directiveTotal: 0,
    components: new Set(),
  }
  if (!ast) return finalize(result)

  function walk(node, depth) {
    if (!node) return
    const type = node.type

    if (type === NodeTypes.ELEMENT) {
      result.nodeCount += 1
      if (depth > result.depth) result.depth = depth
      if (isComponentTag(node.tag)) result.components.add(node.tag)

      for (const prop of node.props || []) {
        if (prop.type === NodeTypes.DIRECTIVE) {
          // `v-bind:x` / `:x` → name 'bind'; `v-on`/`@` → 'on'; `v-if` → 'if'.
          const name = prop.name
          if (name in directives) {
            directives[name] += 1
            result.directiveTotal += 1
            if (CONDITIONAL_LOOP.has(name)) result.conditionalLoopCount += 1
          }
          if (name === 'bind' || name === 'model') result.bindingCount += 1
        }
      }
    } else if (type === NodeTypes.INTERPOLATION) {
      result.bindingCount += 1
    }

    const nextDepth = type === NodeTypes.ELEMENT ? depth + 1 : depth
    for (const child of node.children || []) walk(child, nextDepth)
    // v-if/v-else-if/v-else compile to an IF node with `branches`.
    if (node.branches) for (const branch of node.branches) walk(branch, depth)
  }

  walk(ast, 0)
  return finalize(result)
}

function finalize(result) {
  result.componentCount = result.components.size
  delete result.components
  return result
}
