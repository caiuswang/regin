// The selection bar's display rule (spec Rules 浮现样式条) and the shortcut
// list's labels on each platform (Rules 快捷键一览).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { getSchema } from '@tiptap/core'
import { EditorState, NodeSelection, TextSelection } from '@tiptap/pm/state'
import { parse } from '../../src/editor/markdownDoc.js'
import { richExtensions } from '../../src/editor/extensions.js'
import { BUBBLE_ITEMS, bubbleShows } from '../../src/editor/bubble.js'
import { SHORTCUTS, chordLabel, isMac, shortcutLabel } from '../../src/editor/shortcuts.js'

const schema = getSchema(richExtensions())

function stateOf(src) {
  return EditorState.create({ schema, doc: parse(src, { schema }).doc })
}

// The positions of the first occurrence of `text` in the doc.
function find(state, text) {
  let at = null
  state.doc.descendants((node, pos) => {
    if (at === null && node.isText && node.text.includes(text)) at = pos + node.text.indexOf(text)
  })
  return { from: at, to: at + text.length }
}

function selecting(src, text) {
  const state = stateOf(src)
  const { from, to } = find(state, text)
  return state.apply(state.tr.setSelection(TextSelection.create(state.doc, from, to)))
}

test('the bar offers the six inline items, in order', () => {
  assert.deepEqual(BUBBLE_ITEMS.map((i) => i.label), ['Bold', 'Italic', 'Strike', 'Code', 'Link', 'Reference'])
})

test('a text selection in a paragraph shows the bar', () => {
  assert.equal(bubbleShows(selecting('one abc two\n', 'abc')), true)
})

test('a collapsed caret, an unfocused face or a read-only face shows nothing', () => {
  const state = selecting('one abc two\n', 'abc')
  const caret = state.apply(state.tr.setSelection(TextSelection.create(state.doc, state.selection.to)))
  assert.equal(bubbleShows(caret), false)
  assert.equal(bubbleShows(state, { focused: false }), false)
  assert.equal(bubbleShows(state, { editable: false }), false)
})

test('a selection inside a code block shows nothing', () => {
  assert.equal(bubbleShows(selecting('```ts\nconst abc = 1\n```\n', 'abc')), false)
})

test('a selection spanning a reference or a code block shows nothing', () => {
  const ref = stateOf('see [F1](feature:1) now\n')
  assert.equal(ref.doc.firstChild.child(1).type.name, 'ref')
  assert.equal(bubbleShows(selecting('see [F1](feature:1) now\n', 'see')), true)
  const span = ref.apply(ref.tr.setSelection(TextSelection.create(ref.doc, 1, ref.doc.content.size - 1)))
  assert.equal(bubbleShows(span), false)
  const mixed = stateOf('abc\n\n```\ncode\n```\n')
  const across = mixed.apply(mixed.tr.setSelection(TextSelection.create(mixed.doc, 1, mixed.doc.content.size - 1)))
  assert.equal(bubbleShows(across), false)
})

test('a node selection shows nothing', () => {
  const state = stateOf('one\n\n---\n')
  let hr = null
  state.doc.forEach((node, pos) => { if (node.type.name === 'horizontalRule') hr = pos })
  assert.notEqual(hr, null)
  assert.equal(bubbleShows(state.apply(state.tr.setSelection(NodeSelection.create(state.doc, hr)))), false)
})

test('a selection of only whitespace shows nothing', () => {
  assert.equal(bubbleShows(selecting('one   two\n', '   ')), false)
})

test('the shortcut list carries the rows the spec names', () => {
  const mac = Object.fromEntries(SHORTCUTS.map((row) => [row.action, shortcutLabel(row, true)]))
  assert.equal(mac['Bold / Italic / Strike / Inline code'], '⌘B / ⌘I / ⌘⇧S / ⌘E')
  assert.equal(mac['Heading 1–4'], '⌘⌥1 … ⌘⌥4')
  assert.equal(mac.Save, '⌘Enter')
  assert.equal(mac['Slash menu / Reference'], '/ · @')
  assert.equal(mac['Focus mode on/off'], '⌘⇧F / Esc')
})

test('off a Mac, ⌘ reads Ctrl and chords join with +', () => {
  assert.equal(chordLabel(['Mod', 'B'], false), 'Ctrl+B')
  assert.equal(chordLabel(['Mod', 'Shift', 'S'], false), 'Ctrl+Shift+S')
  assert.equal(chordLabel(['Mod', 'Alt', 'C'], false), 'Ctrl+Alt+C')
  assert.equal(isMac('MacIntel'), true)
  assert.equal(isMac('Win32'), false)
  assert.equal(isMac('Linux x86_64'), false)
})
