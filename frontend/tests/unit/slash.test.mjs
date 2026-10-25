// The `/` menu's item table and its filter: every row of the spec's table is
// offered, a query narrows by label or keyword, and the contextual items drop
// out where they cannot apply.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { SLASH_GROUPS, SLASH_ITEMS, slashItems } from '../../src/editor/slash.js'

const ids = (list) => list.map((item) => item.id)

test('every item belongs to a declared group, in group order', () => {
  assert.deepEqual(ids(SLASH_ITEMS), [
    'h1', 'h2', 'h3', 'h4', 'bullet', 'numbered', 'checklist',
    'quote', 'divider', 'code', 'diagram', 'table', 'image', 'reference',
  ])
  const order = SLASH_ITEMS.map((item) => SLASH_GROUPS.indexOf(item.group))
  assert.ok(order.every((g, i) => g >= 0 && (i === 0 || g >= order[i - 1])))
})

test('an empty query lists everything', () => {
  assert.equal(slashItems('').length, SLASH_ITEMS.length)
})

test('a query narrows by keyword, prefix matches first', () => {
  assert.deepEqual(ids(slashItems('tab')), ['table'])
  assert.deepEqual(ids(slashItems('mermaid')), ['diagram'])
  assert.deepEqual(ids(slashItems('todo')), ['checklist'])
  assert.deepEqual(ids(slashItems('H2')), ['h2'])
  assert.deepEqual(ids(slashItems('zq')), [])
})

test('a table cell hides Table; a host that cannot attach hides Image', () => {
  assert.ok(!ids(slashItems('', { inTable: true })).includes('table'))
  assert.ok(!ids(slashItems('', { canAttach: false })).includes('image'))
})
