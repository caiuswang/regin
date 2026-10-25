// The rich face's Tiptap schema reads and writes the corpus exactly as the
// default schema does: the same blocks parse to the same node types, and an
// open-then-save is still the identity.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { getSchema } from '@tiptap/core'
import { parse, serialize } from '../../src/editor/markdownDoc.js'
import { richExtensions } from '../../src/editor/extensions.js'

const CORPUS = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'fixtures', 'bodies')
const rich = getSchema(richExtensions())

const bodies = () => fs.readdirSync(CORPUS).filter((f) => f.endsWith('.md'))
  .map((f) => [f, fs.readFileSync(path.join(CORPUS, f), 'utf8')])

function shape(doc) {
  const out = []
  doc.descendants((node) => { out.push(node.type.name) })
  return out.join(' ')
}

test('rich schema parses every corpus body to the default schema\'s node types', () => {
  const differ = []
  for (const [name, src] of bodies()) {
    if (shape(parse(src).doc) !== shape(parse(src, { schema: rich }).doc)) differ.push(name)
  }
  assert.deepEqual(differ, [])
})

test('rich schema round-trips every corpus body byte for byte', () => {
  const failed = []
  for (const [name, src] of bodies()) {
    const { doc, segments, retained } = parse(src, { schema: rich })
    if (serialize(doc, segments, retained) !== src) failed.push(name)
  }
  assert.deepEqual(failed, [])
})

test('rich schema keeps tight lists and cell alignment through a rewrite', () => {
  const src = '- a\n- b\n\n| x | y |\n|:-|-:|\n| 1 | 2 |\n'
  const { doc, segments, retained } = parse(src, { schema: rich })
  const copies = []
  doc.forEach((child) => copies.push(child.type.create(child.attrs, child.content, child.marks)))
  const out = serialize(doc.type.create(null, copies), segments, retained)
  assert.equal(out, '- a\n- b\n\n| x   |   y |\n| :-- | --: |\n| 1   |   2 |\n')
})
