// node --test frontend/tests/unit
import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { EditorState, TextSelection } from '@tiptap/pm/state'
import { parse, serialize } from '../../src/editor/markdownDoc.js'
import { schema } from '../../src/editor/schema.js'

const CORPUS = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'fixtures', 'bodies')

const roundtrip = (src) => {
  const { doc, segments, retained } = parse(src)
  return serialize(doc, segments, retained)
}

// Every top-level node replaced by an equal copy: what serialize sees once
// every block has been touched.
function touchAll(doc) {
  const copies = []
  doc.forEach((child) => copies.push(child.type.create(child.attrs, child.content, child.marks)))
  return doc.type.create(null, copies)
}

const rewritten = (src) => {
  const { doc, segments, retained } = parse(src)
  return serialize(touchAll(doc), segments, retained)
}

const top = (src) => parse(src).doc.firstChild

// Position just inside the end of the first textblock whose text is `text`.
function endOf(doc, text) {
  let at = null
  doc.descendants((node, pos) => {
    if (at === null && node.isTextblock && node.textContent === text) at = pos + node.nodeSize - 1
    return at === null
  })
  assert.notEqual(at, null, `no textblock reads ${JSON.stringify(text)}`)
  return at
}

function edit(src, fn) {
  const parsed = parse(src)
  const state = EditorState.create({ doc: parsed.doc })
  const tr = fn(state.tr, parsed.doc)
  return { ...parsed, after: tr.doc, out: serialize(tr.doc, parsed.segments, parsed.retained) }
}

// ---------------------------------------------------------------- corpus --

test('test_roundtrip_corpus_is_identity', () => {
  const files = fs.readdirSync(CORPUS).filter((f) => f.endsWith('.md'))
  assert.ok(files.length > 0, `empty corpus at ${CORPUS} — run scripts/export-bodies.py`)
  const failures = []
  for (const file of files) {
    const src = fs.readFileSync(path.join(CORPUS, file), 'utf8')
    const { segments } = parse(src)
    if (segments.map((s) => s.raw).join('') !== src) failures.push(`${file}: segments do not tile the source`)
    else if (roundtrip(src) !== src) failures.push(`${file}: serialize(parse(s)) !== s`)
  }
  assert.deepEqual(failures, [], `${failures.length} of ${files.length} bodies were rewritten`)
})

const shape = (doc) => JSON.stringify(doc.toJSON(), (key, value) => (
  key === 'raw' && typeof value === 'string' ? value.replace(/\n+$/, '') : value))

test('every corpus body, fully re-serialized, parses back to the same document', () => {
  const drift = []
  for (const file of fs.readdirSync(CORPUS).filter((f) => f.endsWith('.md'))) {
    const src = fs.readFileSync(path.join(CORPUS, file), 'utf8')
    if (shape(parse(rewritten(src)).doc) !== shape(parse(src).doc)) drift.push(file)
  }
  assert.deepEqual(drift, [])
})

test('identity holds for inputs outside the corpus', () => {
  const cases = [
    '', '\n', '\n\n\n', '   ', 'x', 'x\n', '\n\n# h\n\n\n',
    'a\r\nb\r\n\r\n- c\r\n- d\r\n', 'a\rb\r\rc',
    '---\ntitle: x\n---\n\n# body\n',
    '[x]: http://a\n\npara [y][x]\n',
    'para\n[x]: http://a\n',
    '<details><summary>x</summary>y</details>\n\npara',
    '```\nunclosed fence\n',
    '- [ ] a\n- b\n', '1. [ ] a\n2. [x] b\n',
    '> \n', '-\n', '|a|\n|-|\n', '  ﻿',
  ]
  for (const src of cases) assert.equal(roundtrip(src), src, JSON.stringify(src))
})

test('parse never throws and stays lossless on arbitrary slices of the corpus', () => {
  const files = fs.readdirSync(CORPUS).filter((f) => f.endsWith('.md')).slice(0, 60)
  let seed = 7
  const rand = (n) => {
    seed = (seed * 1103515245 + 12345) % 2147483648
    return seed % n
  }
  for (const file of files) {
    const src = fs.readFileSync(path.join(CORPUS, file), 'utf8')
    for (let k = 0; k < 5; k += 1) {
      const a = rand(src.length + 1)
      const b = a + rand(src.length - a + 1)
      const slice = src.slice(a, b)
      assert.equal(roundtrip(slice), slice, `${file}[${a}:${b}]`)
    }
  }
})

// ------------------------------------------------------- edit locality --

test('test_edit_rewrites_only_touched_block', () => {
  const src = '* a\n\n| x |y|\n|--|--|\n| 1 | 2 |\n\npara'
  const { doc, after, out } = edit(src, (tr, d) => tr.insertText('!', endOf(d, 'para')))
  assert.equal(out, '* a\n\n| x |y|\n|--|--|\n| 1 | 2 |\n\npara!')
  assert.equal(after.child(0), doc.child(0), 'the list is the same node object')
  assert.equal(after.child(1), doc.child(1), 'the table is the same node object')
  assert.notEqual(after.child(2), doc.child(2))
})

test('an edit keeps every other block and the gaps between untouched blocks byte-exact', () => {
  const src = '# T\n\n\n\n* x\n+ y\n\n\npara one\n\n\n```js\nq\n```\n'
  const { out } = edit(src, (tr, d) => tr.insertText('2', endOf(d, 'para one')))
  // The gaps either side of the edited paragraph are one blank line (Rules 序列化).
  assert.equal(out, '# T\n\n\n\n* x\n+ y\n\npara one2\n\n```js\nq\n```\n')
})

test('an edit inside a table cell re-serializes that table alone', () => {
  const src = 'intro  *kept*\n\n| a |b|\n|:-|-:|\n| 1 | 2 |\n\n<!-- c -->'
  const { out } = edit(src, (tr, d) => tr.insertText('z', endOf(d, '1')))
  assert.equal(out, 'intro  *kept*\n\n| a   |   b |\n| :-- | --: |\n| 1z  |   2 |\n\n<!-- c -->')
})

test('a block inserted between two retained blocks is set off by one blank line', () => {
  const src = 'one\n\n\n\ntwo\n'
  const { out } = edit(src, (tr, d) => {
    const at = d.child(0).nodeSize
    return tr.insert(at, schema.nodes.paragraph.create(null, schema.text('new')))
  })
  assert.equal(out, 'one\n\nnew\n\ntwo\n')
})

test('deleting a block drops it and keeps the remaining bytes', () => {
  const src = '* a\n\nmid\n\n| x |y|\n|--|--|\n'
  const { out } = edit(src, (tr, d) => tr.delete(d.child(0).nodeSize, d.child(0).nodeSize + d.child(1).nodeSize))
  assert.equal(out, '* a\n\n| x |y|\n|--|--|\n')
})

test('a dirty last block keeps the document trailing newline', () => {
  const { out } = edit('a\n\nb\n', (tr, d) => tr.insertText('c', endOf(d, 'b')))
  assert.equal(out, 'a\n\nbc\n')
})

test('a selection replace across blocks rewrites only the blocks it spans', () => {
  const src = 'keep *me*\n\nfirst\n\nsecond\n\n* tail'
  const { out } = edit(src, (tr, d) => {
    const from = endOf(d, 'first') - 1
    const to = endOf(d, 'second') - 1
    return tr.setSelection(TextSelection.create(tr.doc, from, to)).replaceSelectionWith(schema.text('X'), false)
  })
  assert.equal(out, 'keep *me*\n\nfirsXd\n\n* tail')
})

// ------------------------------------------------------- Rules: 解析 --

test('parse: paragraph / heading / blockquote / hr become their nodes', () => {
  assert.equal(top('plain text').type.name, 'paragraph')
  const h = top('### Three')
  assert.equal(h.type.name, 'heading')
  assert.equal(h.attrs.level, 3)
  assert.equal(top('Setext\n---').attrs.level, 2)
  const q = top('> quoted\n> more')
  assert.equal(q.type.name, 'blockquote')
  assert.equal(q.firstChild.type.name, 'paragraph')
  assert.equal(top('***').type.name, 'horizontalRule')
})

test('parse: lists — bullet, ordered with start, task, nested', () => {
  const b = top('* a\n* b\n  - nested')
  assert.equal(b.type.name, 'bulletList')
  assert.equal(b.childCount, 2)
  assert.equal(b.child(1).child(1).type.name, 'bulletList')
  const o = top('3. c\n4. d')
  assert.equal(o.type.name, 'orderedList')
  assert.equal(o.attrs.start, 3)
  const t = top('- [ ] todo\n- [x] done')
  assert.equal(t.type.name, 'taskList')
  assert.deepEqual([t.child(0).attrs.checked, t.child(1).attrs.checked], [false, true])
  assert.equal(t.child(0).textContent, 'todo')
  assert.equal(top('- a\n\n- b').attrs.tight, false)
})

test('parse: code fences keep their language verbatim', () => {
  for (const lang of ['mermaid', 'gherkin', 'js']) {
    const c = top(`\`\`\`${lang}\nx\n\`\`\``)
    assert.equal(c.type.name, 'codeBlock')
    assert.equal(c.attrs.language, lang)
    assert.equal(c.textContent, 'x')
  }
  assert.equal(top('    indented').type.name, 'codeBlock')
})

test('parse: table cells parse inline and keep column align', () => {
  const t = top('| a | **b** | c |\n|:--|:-:|--:|\n| 1 | 2 | 3 |')
  assert.equal(t.type.name, 'table')
  const header = t.child(0)
  assert.equal(header.child(0).type.name, 'tableHeader')
  assert.deepEqual([0, 1, 2].map((i) => header.child(i).attrs.align), ['left', 'center', 'right'])
  assert.equal(t.child(1).child(0).type.name, 'tableCell')
  assert.equal(header.child(1).firstChild.firstChild.marks[0].type.name, 'bold')
})

test('parse: html, comments, front matter, link definitions and unknown shapes are opaque', () => {
  for (const src of ['<details><summary>x</summary>y</details>', '<!-- note -->', '---\ntitle: x\n---', '[x]: http://a', '- [ ] a\n- b', 'text <span>inline</span> html']) {
    const node = top(src)
    assert.equal(node.type.name, 'opaque', src)
    assert.equal(node.attrs.raw, src)
  }
})

test('parse: a reference link is a ref node, by the refs.js grammar', () => {
  const p = top('see [T42 a \\[x\\]](task:42) and [s](session:abcdefgh-1234)')
  const refs = []
  p.forEach((n) => { if (n.type.name === 'ref') refs.push({ ...n.attrs }) })
  assert.deepEqual(refs, [
    { kind: 'task', key: '42', label: 'T42 a [x]' },
    { kind: 'session', key: 'abcdefgh-1234', label: 's' },
  ])
})

test('parse: links that are not references stay links', () => {
  for (const src of ['[x](task:0)', '[x](nope:1)', '[x](https://a.b)', '`[x](task:1)`']) {
    const p = top(src)
    assert.equal(p.firstChild.type.name, 'text', src)
  }
  assert.equal(top('[x](task:0)').firstChild.marks[0].type.name, 'link')
})

test('parse: image:N is an image node with idx; other images keep src', () => {
  const local = top('![shot](image:3)').firstChild
  assert.equal(local.type.name, 'image')
  assert.deepEqual([local.attrs.idx, local.attrs.alt, local.attrs.src], [3, 'shot', null])
  const remote = top('![](https://x/y.png "t")').firstChild
  assert.deepEqual([remote.attrs.idx, remote.attrs.src, remote.attrs.title], [null, 'https://x/y.png', 't'])
})

test('parse: inline marks map to bold / italic / code / strike / link / hardBreak', () => {
  const p = top('**b** *i* `c` ~~s~~ [l](https://x)  \nnext &amp; \\*')
  const marks = []
  p.forEach((n) => marks.push(n.isText ? `${n.marks.map((m) => m.type.name).join('+') || '-'}:${n.text}` : n.type.name))
  assert.deepEqual(marks, ['bold:b', '-: ', 'italic:i', '-: ', 'code:c', '-: ', 'strike:s', '-: ', 'link:l', 'hardBreak', '-:next & *'])
})

test('parse: blank lines between blocks are gap segments, not nodes', () => {
  const { doc, segments } = parse('a\n\n\nb')
  assert.equal(doc.childCount, 2)
  assert.deepEqual(segments.map((s) => s.kind), ['block', 'gap', 'block'])
  assert.equal(segments[1].raw, '\n\n\n')
})

test('parse: an explicit schema is used for every node', () => {
  const { doc } = parse('# x', { schema })
  assert.equal(doc.type.schema, schema)
})

// ----------------------------------------------------- Rules: 序列化 --

test('serialize: a retained node writes its raw byte for byte', () => {
  const src = '*  a\n*  b\n\n| x |y|\n|--|--|\n\n~~~\nfence\n~~~'
  assert.equal(roundtrip(src), src)
})

test('serialize: a dirty node follows the serializer rules', () => {
  const cases = [
    ['__b__ and _i_', '**b** and *i*'],
    ['* a\n* b', '- a\n- b'],
    ['3) a\n4) b', '3. a\n4. b'],
    ['- [x] done\n- [ ] todo', '- [x] done\n- [ ] todo'],
    ['~~~mermaid\ngraph\n~~~', '```mermaid\ngraph\n```'],
    ['````\n```\n````', '````\n```\n````'],
    ['Title\n===', '# Title'],
    ['***', '---'],
    ['> q', '> q'],
    ['a  \nb', 'a\\\nb'],
    ['~~s~~', '~~s~~'],
    ['[l](https://x "t")', '[l](https://x "t")'],
    ['|a|bb|\n|:-:|-|\n|ccc|d|', '|  a  | bb  |\n| :-: | --- |\n| ccc | d   |'],
    ['|中文|x|\n|-|-|\n|1|2|', '| 中文 | x   |\n| ---- | --- |\n| 1    | 2   |'],
    ['|a\\|b|\n|-|\n', '| a\\|b |\n| ---- |\n'],
  ]
  for (const [src, want] of cases) assert.equal(rewritten(src), want, src)
})

test('serialize: a dirty ref keeps its label and target', () => {
  assert.equal(rewritten('see [T42 a \\[x\\]](task:42)'), 'see [T42 a \\[x\\]](task:42)')
  assert.equal(rewritten('[t](task:42)'), '[t](task:42)')
})

test('serialize: a dirty image keeps image:N, a remote image keeps src', () => {
  assert.equal(rewritten('![](image:3) ![a](image:12)'), '![](image:3) ![a](image:12)')
  assert.equal(rewritten('![x](https://h/p.png)'), '![x](https://h/p.png)')
})

test('serialize: an opaque node writes its raw whether or not it was touched', () => {
  const src = '<details><summary>x</summary>y</details>\n\npara'
  assert.equal(rewritten(src), src)
  const { out } = edit(src, (tr) => tr.setNodeMarkup(0, null, { raw: '<details><summary>z</summary>y</details>' }))
  assert.equal(out, '<details><summary>z</summary>y</details>\n\npara')
})

test('serialize: typed < and & do not become markup', () => {
  const doc = schema.nodes.doc.create(null, schema.nodes.paragraph.create(null, schema.text('a <b> &amp; *c*')))
  const { segments, retained } = parse('')
  const out = serialize(doc, segments, retained)
  assert.equal(out, 'a &lt;b> &amp;amp; \\*c\\*')
  assert.equal(parse(out).doc.firstChild.textContent, 'a <b> &amp; *c*')
})

test('serialize: a new document from an empty source', () => {
  const { segments, retained } = parse('')
  const doc = schema.nodes.doc.create(null, [
    schema.nodes.heading.create({ level: 2 }, schema.text('目标')),
    schema.nodes.bulletList.create(null, schema.nodes.listItem.create(null, schema.nodes.paragraph.create(null, schema.text('项')))),
  ])
  assert.equal(serialize(doc, segments, retained), '## 目标\n\n- 项')
})

// ----------------------------------------------------- Invariant 5 --

test('rich → source → rich is lossless', () => {
  const src = '| a |b|\n|-|-|\n\n```mermaid\ngraph TD\n```\n\n<!-- c -->\n[t](task:42) ![](image:3)\n'
  const once = roundtrip(src)
  assert.equal(once, src)
  assert.equal(roundtrip(once), src)
})

// ------------------------------------------- edited-block regressions --

const topTypes = (src) => {
  const names = []
  parse(src).doc.forEach((n) => names.push(n.type.name))
  return names
}

test('an edited list next to a same-kind list does not merge with it', () => {
  const cases = [
    ['- a\n\n* b\n', 'b', '- a\n\n- b!\n'],
    ['* a\n\n- b\n', 'a', '- a!\n\n- b\n'],
    ['1. a\n\n1) b\n', 'b', '1. a\n\n1. b!\n'],
    ['- [ ] a\n\n* b\n', 'b', '- [ ] a\n\n- b!\n'],
  ]
  for (const [src, target, wrong] of cases) {
    const { out } = edit(src, (tr, d) => tr.insertText('!', endOf(d, target)))
    assert.notEqual(out, wrong, src)
    assert.deepEqual(topTypes(out), topTypes(src), `${JSON.stringify(src)} → ${JSON.stringify(out)}`)
  }
  const item = (t) => schema.nodes.listItem.create(null, schema.nodes.paragraph.create(null, schema.text(t)))
  const doc = schema.nodes.doc.create(null, [schema.nodes.bulletList.create(null, item('x')), schema.nodes.bulletList.create(null, item('y'))])
  const { segments, retained } = parse('')
  assert.deepEqual(topTypes(serialize(doc, segments, retained)), ['bulletList', 'bulletList'])
})

test('an entity parse leaves undecoded survives an edit unchanged', () => {
  assert.equal(rewritten('&copy; 2026 &amp; &#169;'), '&copy; 2026 & ©')
})

test('an edit in a CRLF document writes CRLF', () => {
  const { out } = edit('a\r\n\r\nb\r\n\r\n- c\r\n- d\r\n', (tr, d) => tr.insertText('!', endOf(d, 'c')))
  assert.equal(out, 'a\r\n\r\nb\r\n\r\n- c!\r\n- d\r\n')
})

test('an edit at either end keeps the source leading and trailing whitespace', () => {
  assert.equal(edit('\n\n# T\n\nx', (tr, d) => tr.insertText('!', endOf(d, 'T'))).out, '\n\n# T!\n\nx')
  assert.equal(edit('para\n\n\n', (tr, d) => tr.insertText('!', endOf(d, 'para'))).out, 'para!\n\n\n')
})

test('a <br> in a table cell is a hard break, written back as <br>', () => {
  const src = '| a |\n|---|\n| x<br>y |'
  const t = top(src)
  assert.equal(t.type.name, 'table')
  assert.equal(t.child(1).child(0).firstChild.child(1).type.name, 'hardBreak')
  assert.equal(rewritten(src), '| a      |\n| ------ |\n| x<br>y |')
})

test('an edited list next to an opaque list does not merge with it', () => {
  const src = '- <b>x</b>\n\npara\n'
  const { out } = edit(src, (tr, d) => {
    const from = d.child(0).nodeSize
    const item = schema.nodes.listItem.create(null, schema.nodes.paragraph.create(null, schema.text('new')))
    return tr.replaceWith(from, from + d.child(1).nodeSize, schema.nodes.bulletList.create(null, item))
  })
  assert.equal(parse(out).doc.childCount, 2, JSON.stringify(out))
})

test('typing into a whitespace-only source writes its whitespace once', () => {
  for (const src of ['\n\n\n', '  \n', '\r\n\r\n']) {
    const { out } = edit(src, (tr) => tr.insertText('hi', 1))
    assert.equal(out, `${src}hi`, JSON.stringify(src))
  }
})
