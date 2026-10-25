// The Tiptap extensions the rich face is built from.
//
// Their schema has to read and write what `markdownDoc.js` does: the same node
// names and attrs as `schema.js`, which is checked by running the corpus
// through `parse(src, { schema: getSchema(richExtensions()) })` in the unit
// tests. Three things in StarterKit would break that and are off:
//
//   trailingNode — appends an empty paragraph after a final table or code
//                  block, so opening a spec and saving it would add a blank
//                  line;
//   underline    — markdown has no underline, so the mark would be dropped on
//                  save;
//   the lists    — replaced by versions that carry `tight`, without which an
//                  edited list would come back loose.
//
// No DOM and no Vue here, so the schema can be built under `node --test`: the
// rich face hands its Vue node views in through `views`.

import { Extension, InputRule, Node, mergeAttributes } from '@tiptap/core'
import { PluginKey } from '@tiptap/pm/state'
import Suggestion from '@tiptap/suggestion'
import StarterKit from '@tiptap/starter-kit'
import { BulletList, OrderedList, TaskItem, TaskList } from '@tiptap/extension-list'
import { CodeBlockLowlight } from '@tiptap/extension-code-block-lowlight'
import { Table, TableCell, TableHeader, TableRow } from '@tiptap/extension-table'
import { Placeholder } from '@tiptap/extensions'
import { closeHistory } from '@tiptap/pm/history'
import { TextSelection } from '@tiptap/pm/state'
import { TableMap, addColumn, addRow, isInTable, selectedRect } from '@tiptap/pm/tables'
import { SLASH_HANDOFF, SLASH_MENU, SlashCommand, inCode } from './slash.js'
import { editorLowlight } from './highlight.js'

const tight = {
  tight: {
    default: true,
    parseHTML: (el) => el.getAttribute('data-tight') !== 'false',
    renderHTML: (attrs) => (attrs.tight ? {} : { 'data-tight': 'false' }),
  },
}

function withTight(extension) {
  return extension.extend({
    addAttributes() {
      return { ...this.parent?.(), ...tight }
    },
  })
}

function withAlign(extension) {
  return extension.extend({
    addAttributes() {
      return {
        ...this.parent?.(),
        align: {
          default: null,
          parseHTML: (el) => el.style.textAlign || null,
          renderHTML: (attrs) => (attrs.align ? { style: `text-align: ${attrs.align}` } : {}),
        },
      }
    },
  })
}

// Rendered as its source until the opaque card replaces it: what it holds is
// HTML or syntax the editor has no node for, and it is saved as written.
export const Opaque = Node.create({
  name: 'opaque',
  group: 'block',
  atom: true,
  selectable: true,
  addOptions() {
    return { images: () => [] }
  },
  addAttributes() {
    return { raw: { default: '', parseHTML: (el) => el.getAttribute('data-raw') ?? '', rendered: false } }
  },
  parseHTML() {
    return [{ tag: 'div[data-opaque]' }]
  },
  renderHTML({ node, HTMLAttributes }) {
    return ['div', mergeAttributes(HTMLAttributes, { 'data-opaque': '', class: 'rich-opaque' }), ['pre', node.attrs.raw]]
  },
})

// `item(target)` is the live `RefItem` for `kind:key` (`useRefResolve`), for
// the chip a node view draws.
export const Ref = Node.create({
  name: 'ref',
  group: 'inline',
  inline: true,
  atom: true,
  addOptions() {
    return { item: () => undefined }
  },
  addAttributes() {
    return {
      kind: { default: null, parseHTML: (el) => el.getAttribute('data-kind') },
      key: { default: null, parseHTML: (el) => el.getAttribute('data-key') },
      label: { default: '', parseHTML: (el) => el.textContent, rendered: false },
    }
  },
  parseHTML() {
    return [{ tag: 'span[data-ref]' }]
  },
  renderHTML({ node, HTMLAttributes }) {
    return ['span', mergeAttributes(HTMLAttributes, { 'data-ref': `${node.attrs.kind}:${node.attrs.key}`, class: 'rich-ref' }), node.attrs.label]
  },
})

// `resolve(attrs)` is the picture's URL: an attachment's object URL for an
// `![](image:N)`, its own `src` otherwise, or null when the attachment is gone.
export const Image = Node.create({
  name: 'image',
  group: 'inline',
  inline: true,
  atom: true,
  draggable: true,
  addOptions() {
    return { resolve: (attrs) => attrs.src }
  },
  addAttributes() {
    return {
      src: { default: null, rendered: false },
      alt: { default: '' },
      title: { default: null },
      idx: { default: null, parseHTML: (el) => el.getAttribute('data-idx'), renderHTML: (a) => (a.idx === null ? {} : { 'data-idx': a.idx }) },
    }
  },
  parseHTML() {
    return [{ tag: 'img[src]', getAttrs: (el) => ({ src: el.getAttribute('src') }) }]
  },
  renderHTML({ node, HTMLAttributes }) {
    const src = this.options.resolve(node.attrs)
    if (!src) return ['span', { class: 'rich-image-missing', 'data-idx': node.attrs.idx }, `(image:${node.attrs.idx} — no longer attached)`]
    return ['img', mergeAttributes(HTMLAttributes, { src })]
  },
})

// `- [ ] ` typed at the start of a line: the `- ` has already made a bullet
// list by the time the brackets arrive, so the brackets turn that item into a
// task — TaskItem's own rule only fires outside a list.
const TaskInBullet = Extension.create({
  name: 'taskInBullet',
  addInputRules() {
    return [new InputRule({
      find: /^\[([ xX])?\]\s$/,
      handler: ({ state, range, match, chain }) => {
        const $from = state.doc.resolve(range.from)
        if ($from.depth < 2 || $from.node(-1).type.name !== 'listItem') return null
        chain()
          .deleteRange(range)
          .toggleTaskList()
          .updateAttributes('taskItem', { checked: /x/i.test(match[1] ?? '') })
          .run()
        return undefined
      },
    })]
  },
})

// Mod-Enter submits and Mod-k asks for a link: both belong to the host and
// the toolbar, so the editor only reports them.
function Keys({ onSubmit, onLink }) {
  return Extension.create({
    name: 'reginKeys',
    // Ahead of HardBreak, which binds Mod-Enter too.
    priority: 1000,
    addKeyboardShortcuts() {
      return {
        'Mod-Enter': () => { onSubmit?.(); return true },
        'Mod-k': () => { onLink?.(); return true },
      }
    },
  })
}

// Commands the toolbar runs. They live here, inside the lazily loaded editor,
// so the toolbar — on the page from the start — imports no ProseMirror.
//
// `newUndoStep` closes the current history event: on a transaction with steps
// that transaction starts a new one, and dispatched on its own (no steps) it
// makes the next transaction start one. The table
// inserts move the caret into the row or column they made, so what is typed
// next lands there rather than in the cell the press was made from.
function addAndLand(where) {
  return () => ({ state, tr, dispatch }) => {
    if (!isInTable(state)) return false
    if (!dispatch) return true
    const rect = selectedRect(state)
    const [row, col] = where === 'row' ? [rect.bottom, rect.left] : [rect.top, rect.right]
    if (where === 'row') addRow(tr, rect, row)
    else addColumn(tr, rect, col)
    const map = TableMap.get(tr.doc.nodeAt(rect.tableStart - 1))
    const cell = rect.tableStart + map.map[row * map.width + col]
    tr.setSelection(TextSelection.near(tr.doc.resolve(cell + 1)))
    return true
  }
}

// `@` opens the reference menu. The plugin only finds the token; what it
// searches, lists and writes is `useRefMentions`, behind `render`. Any prefix
// is let through so the menu's own rule (no `@` inside a word or an address)
// decides, as it does in the source face. It stays shut while the `/` menu
// is open, except for the `@` that menu's Reference item types.
export const REF_MENTION = new PluginKey('refMention')

function RefMention(render) {
  return Extension.create({
    name: 'refMention',
    // Ahead of the core and list keymaps, so the menu takes Enter, Tab and
    // the arrows while it is open.
    priority: 101,
    addProseMirrorPlugins() {
      return [Suggestion({
        editor: this.editor,
        pluginKey: REF_MENTION,
        char: '@',
        allowedPrefixes: null,
        allow: ({ state, range }) => !inCode(state, range),
        shouldShow: ({ editor, transaction }) =>
          !SLASH_MENU.getState(editor.state)?.active || !!transaction.getMeta(SLASH_HANDOFF),
        // Escape closes the menu until the next keystroke that edits, as in
        // the source face; moving the caret alone does not bring it back.
        shouldResetDismissed: ({ transaction }) => transaction.docChanged,
        render,
      })]
    },
  })
}

function withView(extension, view) {
  return view ? extension.extend({ addNodeView: () => view }) : extension
}

const ToolbarCommands = Extension.create({
  name: 'reginToolbar',
  addCommands() {
    return {
      newUndoStep: () => ({ tr }) => { closeHistory(tr); return true },
      addRowAndLand: addAndLand('row'),
      addColumnAndLand: addAndLand('column'),
    }
  },
})

export function richExtensions({
  placeholder = '', resolveImage, refItem, images, onSubmit, onLink, views = {}, mention = null, slash = null,
} = {}) {
  const options = (extension, opts) => {
    const set = Object.fromEntries(Object.entries(opts).filter(([, v]) => v))
    return Object.keys(set).length ? extension.configure(set) : extension
  }
  return [
    StarterKit.configure({
      trailingNode: false,
      underline: false,
      bulletList: false,
      orderedList: false,
      codeBlock: false,
      link: { openOnClick: false, autolink: false },
    }),
    withTight(BulletList),
    withTight(OrderedList),
    withView(CodeBlockLowlight, views.codeBlock).configure({ lowlight: editorLowlight }),
    withTight(TaskList),
    TaskItem.configure({ nested: true }),
    TaskInBullet,
    Table.configure({ resizable: false }),
    TableRow,
    withAlign(TableHeader),
    withAlign(TableCell),
    options(withView(Opaque, views.opaque), { images }),
    options(withView(Ref, views.ref), { item: refItem }),
    options(withView(Image, views.image), { resolve: resolveImage }),
    Placeholder.configure({ placeholder }),
    Keys({ onSubmit, onLink }),
    ToolbarCommands,
    ...(mention ? [RefMention(mention)] : []),
    ...(slash ? [SlashCommand({ ...slash, blockedBy: REF_MENTION })] : []),
  ]
}
