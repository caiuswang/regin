// The document schema the markdown parser and serializer run against.
//
// Node and mark names, and their attrs, are Tiptap's (`codeBlock`, `bulletList`,
// `bold`, …) so the rich editor's schema — built from Tiptap extensions — can
// stand in for this one: `parse(src, { schema: editor.schema })` resolves every
// type by name. Three nodes have no Tiptap counterpart and are defined here:
//
//   opaque — a block regin cannot represent (HTML, link definitions, front
//            matter, anything the lexer emits that has no node). Atomic; its
//            `raw` is written back verbatim whether or not it was touched.
//   ref    — `[label](kind:key)` (utils/refs.js), an inline atom.
//   image  — an inline atom; `idx` set for `![alt](image:N)`, `src` otherwise.
//
// Table cells carry `align` beyond Tiptap's attrs, because a GFM delimiter row
// stores it per column and a re-serialized table must keep it.

import { Schema } from '@tiptap/pm/model'

export const opaqueSpec = {
  group: 'block',
  atom: true,
  selectable: true,
  attrs: { raw: { default: '' } },
  parseDOM: [{ tag: 'div[data-opaque]', getAttrs: (dom) => ({ raw: dom.getAttribute('data-raw') ?? '' }) }],
  toDOM: (node) => ['div', { 'data-opaque': '', 'data-raw': node.attrs.raw }],
}

export const refSpec = {
  group: 'inline',
  inline: true,
  atom: true,
  attrs: { kind: {}, key: {}, label: { default: '' } },
  parseDOM: [{
    tag: 'span[data-ref]',
    getAttrs: (dom) => ({
      kind: dom.getAttribute('data-kind'),
      key: dom.getAttribute('data-key'),
      label: dom.textContent,
    }),
  }],
  toDOM: (node) => ['span', { 'data-ref': '', 'data-kind': node.attrs.kind, 'data-key': node.attrs.key }, node.attrs.label],
}

export const imageSpec = {
  group: 'inline',
  inline: true,
  atom: true,
  draggable: true,
  attrs: { src: { default: null }, alt: { default: '' }, title: { default: null }, idx: { default: null } },
  parseDOM: [{
    tag: 'img[src]',
    getAttrs: (dom) => ({ src: dom.getAttribute('src'), alt: dom.getAttribute('alt') ?? '', title: dom.getAttribute('title') }),
  }],
  toDOM: (node) => ['img', { src: node.attrs.src, alt: node.attrs.alt, title: node.attrs.title, 'data-idx': node.attrs.idx }],
}

const cellAttrs = {
  colspan: { default: 1 },
  rowspan: { default: 1 },
  colwidth: { default: null },
  align: { default: null },
}

export const schema = new Schema({
  nodes: {
    doc: { content: 'block+' },
    paragraph: { group: 'block', content: 'inline*', toDOM: () => ['p', 0], parseDOM: [{ tag: 'p' }] },
    heading: {
      group: 'block',
      content: 'inline*',
      defining: true,
      attrs: { level: { default: 1 } },
      toDOM: (node) => [`h${node.attrs.level}`, 0],
      parseDOM: [1, 2, 3, 4, 5, 6].map((level) => ({ tag: `h${level}`, attrs: { level } })),
    },
    blockquote: { group: 'block', content: 'block+', defining: true, toDOM: () => ['blockquote', 0], parseDOM: [{ tag: 'blockquote' }] },
    codeBlock: {
      group: 'block',
      content: 'text*',
      marks: '',
      code: true,
      defining: true,
      attrs: { language: { default: null } },
      toDOM: () => ['pre', ['code', 0]],
      parseDOM: [{ tag: 'pre', preserveWhitespace: 'full' }],
    },
    horizontalRule: { group: 'block', toDOM: () => ['hr'], parseDOM: [{ tag: 'hr' }] },
    bulletList: { group: 'block', content: 'listItem+', attrs: { tight: { default: true } }, toDOM: () => ['ul', 0], parseDOM: [{ tag: 'ul' }] },
    orderedList: {
      group: 'block',
      content: 'listItem+',
      attrs: { start: { default: 1 }, tight: { default: true } },
      toDOM: (node) => ['ol', { start: node.attrs.start }, 0],
      parseDOM: [{ tag: 'ol' }],
    },
    listItem: { content: 'paragraph block*', defining: true, toDOM: () => ['li', 0], parseDOM: [{ tag: 'li' }] },
    taskList: { group: 'block', content: 'taskItem+', attrs: { tight: { default: true } }, toDOM: () => ['ul', { 'data-type': 'taskList' }, 0] },
    taskItem: {
      content: 'paragraph block*',
      defining: true,
      attrs: { checked: { default: false } },
      toDOM: () => ['li', { 'data-type': 'taskItem' }, 0],
    },
    table: { group: 'block', content: 'tableRow+', isolating: true, tableRole: 'table', toDOM: () => ['table', ['tbody', 0]] },
    tableRow: { content: '(tableCell | tableHeader)*', tableRole: 'row', toDOM: () => ['tr', 0] },
    tableHeader: { content: 'block+', attrs: cellAttrs, isolating: true, tableRole: 'header_cell', toDOM: () => ['th', 0] },
    tableCell: { content: 'block+', attrs: cellAttrs, isolating: true, tableRole: 'cell', toDOM: () => ['td', 0] },
    opaque: opaqueSpec,
    text: { group: 'inline' },
    hardBreak: { group: 'inline', inline: true, selectable: false, toDOM: () => ['br'], parseDOM: [{ tag: 'br' }] },
    ref: refSpec,
    image: imageSpec,
  },
  marks: {
    link: {
      attrs: { href: {}, title: { default: null } },
      inclusive: false,
      toDOM: (mark) => ['a', { href: mark.attrs.href, title: mark.attrs.title }, 0],
    },
    bold: { toDOM: () => ['strong', 0], parseDOM: [{ tag: 'strong' }, { tag: 'b' }] },
    italic: { toDOM: () => ['em', 0], parseDOM: [{ tag: 'em' }, { tag: 'i' }] },
    strike: { toDOM: () => ['s', 0], parseDOM: [{ tag: 's' }, { tag: 'del' }] },
    code: { toDOM: () => ['code', 0], parseDOM: [{ tag: 'code' }] },
  },
})
