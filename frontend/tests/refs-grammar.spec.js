// The JS half of the reference grammar against the shared fixture.
//
// `tests/refs/test_refs_grammar.py` runs the same fixture through
// `lib/refs/grammar.py`; the two passing together is what makes the grammars
// one. Plain Node imports — no page, no Vite.
import { test, expect } from '@playwright/test'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import {
  canonicalKind, decodeFileKey, encodeFileKey, formatRef, parseRefTarget,
  parseRefs, refEndingAt,
} from '../src/utils/refs.js'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..')
const FIXTURE = JSON.parse(
  readFileSync(resolve(ROOT, 'tests/fixtures/refs_grammar_cases.json'), 'utf8'))

test('test_javascript_grammar_parses_every_fixture_case', () => {
  expect(FIXTURE.parse.length).toBeGreaterThanOrEqual(18)
  for (const c of FIXTURE.parse) {
    const got = parseRefs(c.text).map(({ ref, label }) => ({ ref, label }))
    expect(got, c.name).toEqual(c.refs)
  }
})

test('formatRef writes the same labels as format_ref', () => {
  for (const c of FIXTURE.format) {
    expect(formatRef(c.kind, c.key, c.title), JSON.stringify(c)).toBe(c.ref)
  }
  expect(() => formatRef('feature', '0', 'zero is no id')).toThrow()
})

test('file keys are canonical and never leave the repo', () => {
  for (const c of FIXTURE.file_keys) {
    expect(encodeFileKey(c.path), c.path).toBe(c.key)
    if (c.key !== null) expect(decodeFileKey(c.key), c.key).toBe(c.path)
  }
  for (const key of FIXTURE.bad_file_keys) {
    expect(decodeFileKey(key), key).toBeNull()
    expect(parseRefTarget(`file:${key}`), key).toBeNull()
  }
  expect(canonicalKind('lesson')).toBe('memory')
  expect(canonicalKind('constructor')).toBeNull()
})

test('refEndingAt finds only the reference that closes at the caret', () => {
  const text = 'see [F1 a](feature:1) and `[F2 b](feature:2)`'
  const end = text.indexOf(')') + 1

  expect(refEndingAt(text, end)).toMatchObject({ ref: 'feature:1', start: 4, end })
  expect(refEndingAt(text, end - 1)).toBeNull()
  expect(refEndingAt(text, text.length - 1)).toBeNull()
})
