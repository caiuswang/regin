// The `/` menu: block types and inserts, offered where a writer starts a block.
//
// `/` opens it at the start of a paragraph or after whitespace, never inside a
// word (`lib/orm`, a URL) or in code. It and the `@` menu are never open
// together: each refuses to start while the other is active, read off the
// state before the keystroke. The one deliberate hand-off — the Reference item
// typing an `@` — marks its transaction so the `@` menu may start from it.
//
// Every item is one undo step: the `/query` text goes in the same transaction
// as the action, with history closed before and after. The image item is the
// exception the browser forces: the file picker is the host's and answers
// later, so the query is removed first and the pictures arrive as their own
// step (`insertImages`).
//
// No DOM and no Vue: the menu is drawn by whoever passes `render`.

import { Extension } from '@tiptap/core'
import { PluginKey } from '@tiptap/pm/state'
import { isInTable } from '@tiptap/pm/tables'
import Suggestion from '@tiptap/suggestion'

export const SLASH_MENU = new PluginKey('slashMenu')
export const SLASH_HANDOFF = 'slashHandoff'

export const SLASH_GROUPS = ['Text', 'Lists', 'Blocks', 'Insert']

const heading = (level) => ({
  id: `h${level}`, label: `Heading ${level}`, group: 'Text', icon: 'heading',
  keywords: [`h${level}`, 'heading', 'title'],
  run: (chain) => chain.setHeading({ level }),
})

export const SLASH_ITEMS = [
  heading(1), heading(2), heading(3), heading(4),
  { id: 'bullet', label: 'Bulleted list', group: 'Lists', icon: 'list',
    keywords: ['bullet', 'list', 'ul'], run: (chain) => chain.toggleBulletList() },
  { id: 'numbered', label: 'Numbered list', group: 'Lists', icon: 'list-ordered',
    keywords: ['numbered', 'ordered', 'ol'], run: (chain) => chain.toggleOrderedList() },
  { id: 'checklist', label: 'Checklist', group: 'Lists', icon: 'list-checks',
    keywords: ['checklist', 'todo', 'task'], run: (chain) => chain.toggleTaskList() },
  { id: 'quote', label: 'Quote', group: 'Blocks', icon: 'message-square',
    keywords: ['quote', 'blockquote'], run: (chain) => chain.toggleBlockquote() },
  { id: 'divider', label: 'Divider', group: 'Blocks', icon: 'minus',
    keywords: ['divider', 'rule', 'hr'], run: (chain) => chain.setHorizontalRule() },
  { id: 'code', label: 'Code block', group: 'Blocks', icon: 'code',
    keywords: ['code', 'fence'], run: (chain) => chain.setCodeBlock({ language: null }) },
  { id: 'diagram', label: 'Diagram', group: 'Blocks', icon: 'workflow',
    keywords: ['diagram', 'mermaid'], run: (chain) => chain.setCodeBlock({ language: 'mermaid' }) },
  { id: 'table', label: 'Table', group: 'Insert', icon: 'table',
    keywords: ['table', 'grid'], run: (chain) => chain.insertTable({ rows: 2, cols: 3, withHeaderRow: true }) },
  { id: 'image', label: 'Image', group: 'Insert', icon: 'image',
    keywords: ['image', 'picture', 'upload', 'file'] },
  { id: 'reference', label: 'Reference', group: 'Insert', icon: 'link',
    keywords: ['reference', 'mention', 'link', 'task', 'feature', 'defect'],
    run: (chain) => chain.insertContent('@').setMeta(SLASH_HANDOFF, true) },
]

// What the menu lists for `query` (the text after `/`): everything when it is
// empty, otherwise items whose label or a keyword starts with it, then those
// that merely contain it. A table cell cannot hold a table, and the image item
// needs a host that can attach.
export function slashItems(query, { inTable = false, canAttach = true } = {}) {
  const q = (query ?? '').toLowerCase()
  const offered = SLASH_ITEMS.filter((item) =>
    !(inTable && item.id === 'table') && !(!canAttach && item.id === 'image'))
  if (!q) return offered
  const words = (item) => [item.label.toLowerCase(), ...item.keywords]
  const prefix = offered.filter((item) => words(item).some((w) => w.startsWith(q)))
  const inner = offered.filter((item) => !prefix.includes(item) && words(item).some((w) => w.includes(q)))
  return [...prefix, ...inner]
}

export function inCode(state, range) {
  const $from = state.doc.resolve(range.from)
  if ($from.parent.type.spec.code) return true
  const code = state.schema.marks.code
  return !!code && ($from.marks().some((m) => m.type === code) || state.doc.rangeHasMark(range.from, range.to, code))
}

// Removes `/query` and applies `item`, as one undo step.
export function runSlashItem(editor, range, item, { pickImage } = {}) {
  if (item.id === 'image') {
    editor.chain().focus().newUndoStep().deleteRange(range).run()
    editor.commands.newUndoStep()
    pickImage?.()
    return
  }
  item.run(editor.chain().focus().newUndoStep().deleteRange(range)).run()
  editor.commands.newUndoStep()
}

// `blockedBy` is the other suggestion's key. `canAttach()` says whether the
// host takes pictures; `pickImage()` opens its file picker.
export function SlashCommand({ render, blockedBy, canAttach = () => false, pickImage }) {
  const options = (state) => ({ inTable: isInTable(state), canAttach: canAttach() })
  const queryAt = (state, range) => state.doc.textBetween(range.from, range.to).slice(1)
  return Extension.create({
    name: 'slashCommand',
    // Ahead of the core and list keymaps, so the menu takes Enter and the
    // arrows while it is open.
    priority: 101,
    addProseMirrorPlugins() {
      return [Suggestion({
        editor: this.editor,
        pluginKey: SLASH_MENU,
        char: '/',
        allowedPrefixes: [' ', ' '],
        // A query that matched nothing, typed one character further, closes
        // the menu: the writer is typing a path, not picking a block.
        allow: ({ state, range }) => {
          if (inCode(state, range)) return false
          const q = queryAt(state, range)
          const opts = options(state)
          return slashItems(q, opts).length > 0 || slashItems(q.slice(0, -1), opts).length > 0
        },
        shouldShow: ({ editor }) => !blockedBy?.getState(editor.state)?.active,
        items: ({ editor, query }) => slashItems(query, options(editor.state)),
        command: ({ editor, range, props: item }) => runSlashItem(editor, range, item, { pickImage }),
        render,
      })]
    },
  })
}
