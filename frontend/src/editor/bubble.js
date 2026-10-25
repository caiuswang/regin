// The bar that rises over a text selection on the rich face: the inline marks
// the head's toolbar also has, offered where the writer's hand already is.
//
// It shows only over a selection a mark can go on — text, not code, and no
// reference, picture or opaque block inside it — and never while another
// overlay (`@`, `/`) is open, so at most one floats at a time. The link
// popover and the shortcut list take the focus, which hides the bar too.
//
// Each press is one undo step, the way the toolbar's are: history closed
// before the command and after it (`newUndoStep`, in `extensions.js`).
//
// No DOM and no Vue: the bar is drawn by `EditorBubble.vue`.

import { TextSelection } from '@tiptap/pm/state'
import { SLASH_MENU } from './slash.js'
import { REF_MENTION } from './extensions.js'

const ATOMS = new Set(['ref', 'image', 'opaque'])

const toggle = (name) => (chain) => chain[name]()

export const BUBBLE_ITEMS = [
  { id: 'bold', label: 'Bold', icon: 'bold', mark: 'bold', run: toggle('toggleBold') },
  { id: 'italic', label: 'Italic', icon: 'italic', mark: 'italic', run: toggle('toggleItalic') },
  { id: 'strike', label: 'Strike', icon: 'strikethrough', mark: 'strike', run: toggle('toggleStrike') },
  { id: 'code', label: 'Code', icon: 'code', mark: 'code', run: toggle('toggleCode') },
  { id: 'link', label: 'Link', icon: 'link', mark: 'link' },
  { id: 'reference', label: 'Reference', icon: 'at-sign' },
]

function holdsNoMarkable(state, from, to) {
  let blocked = false
  state.doc.nodesBetween(from, to, (node) => {
    if (blocked) return false
    if (node.type.spec.code || ATOMS.has(node.type.name)) blocked = true
    return !blocked
  })
  return !blocked
}

// Whether the bar belongs over `state`'s selection. `focused` is whether the
// face (or the bar itself) holds the focus.
export function bubbleShows(state, { focused = true, editable = true } = {}) {
  const { selection } = state
  if (!focused || !editable || selection.empty || !(selection instanceof TextSelection)) return false
  if (SLASH_MENU.getState(state)?.active || REF_MENTION.getState(state)?.active) return false
  const { from, to } = selection
  if (!state.doc.textBetween(from, to).trim()) return false
  return holdsNoMarkable(state, from, to)
}

// Runs `item` on the selection as one undo step; the selection stays, so the
// bar stays over it.
export function runBubbleItem(editor, item) {
  if (!item.run) return
  item.run(editor.chain().focus().newUndoStep()).run()
  editor.commands.newUndoStep()
}

// Reference: an `@` after the selection, which opens the reference menu
// there. The selected text stays as it was; a space goes first when the
// selection ends inside a word, since an `@` inside a word opens nothing.
export function insertReference(editor) {
  const { state } = editor
  const { to } = state.selection
  const before = state.doc.textBetween(Math.max(to - 1, 0), to, '', '￼')
  const text = before && !/\s/.test(before) ? ' @' : '@'
  editor.chain().focus().newUndoStep().setTextSelection(to).insertContent(text).run()
  editor.commands.newUndoStep()
}
