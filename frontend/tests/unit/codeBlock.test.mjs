// F178 S2 — code blocks: the shared highlighter, and the rich schema with
// `CodeBlockLowlight` in it still opening and saving every body unchanged.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { getSchema } from '@tiptap/core'
import { EditorState } from '@tiptap/pm/state'
import { parse, serialize } from '../../src/editor/markdownDoc.js'
import { richExtensions } from '../../src/editor/extensions.js'
import { LANGUAGES, editorLowlight, highlightHtml, isHighlighted } from '../../src/editor/highlight.js'

const CORPUS = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'fixtures', 'bodies')
const rich = getSchema(richExtensions())

test('test_roundtrip_corpus_still_identity', () => {
  const files = fs.readdirSync(CORPUS).filter((f) => f.endsWith('.md'))
  assert.ok(files.length > 0, `empty corpus at ${CORPUS}`)
  const failed = []
  for (const file of files) {
    const src = fs.readFileSync(path.join(CORPUS, file), 'utf8')
    const { doc, segments, retained } = parse(src, { schema: rich })
    if (serialize(doc, segments, retained) !== src) failed.push(file)
  }
  assert.deepEqual(failed, [])
})

test('the rich code block is CodeBlockLowlight with the same language attr', () => {
  const block = rich.nodes.codeBlock
  assert.ok(block, 'no codeBlock node')
  assert.deepEqual(Object.keys(block.spec.attrs), ['language'])
})

test('every picker language but plain and mermaid is highlighted, aliases included', () => {
  const unhighlighted = LANGUAGES.filter((lang) => !isHighlighted(lang))
  assert.deepEqual(unhighlighted, ['plain', 'mermaid'])
})

test('gherkin colours its keywords; an unknown language stays plain text', () => {
  assert.match(highlightHtml('Scenario: x', 'gherkin'), /<span class="hljs-keyword">Scenario<\/span>: x/)
  assert.equal(highlightHtml('a <b> & c', 'unknownlang'), 'a &lt;b&gt; &amp; c')
  assert.equal(highlightHtml('graph TD', 'mermaid'), 'graph TD')
  assert.equal(highlightHtml('x', null), 'x')
})

test('the editor adapter never guesses a language', () => {
  const tree = editorLowlight.highlightAuto('def f(): return 1')
  assert.deepEqual(tree.children, [{ type: 'text', value: 'def f(): return 1' }])
  const unknown = editorLowlight.highlight('python3x', 'x = 1')
  assert.deepEqual(unknown.children, [{ type: 'text', value: 'x = 1' }])
})

test('test_language_select_rewrites_only_that_block (serializer half)', () => {
  const src = 'intro\n\n```\nx\n```\n\n| a |\n|---|\n| 1 |'
  const { doc, segments, retained } = parse(src, { schema: rich })
  let at = null
  doc.forEach((node, offset) => { if (node.type.name === 'codeBlock') at = offset })
  const state = EditorState.create({ doc })
  const tr = state.tr.setNodeAttribute(at, 'language', 'bash')
  assert.equal(serialize(tr.doc, segments, retained), 'intro\n\n```bash\nx\n```\n\n| a |\n|---|\n| 1 |')
})
