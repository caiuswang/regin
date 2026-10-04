// Splicing text into a textarea at the caret.
//
// Shared because every surface that takes a pasted image now writes an
// `![](image:N)` reference where the paste happened, and six copies of "read
// selectionStart off the component's root element" is how five of them drift.
//
// A field component whose root is a wrapper exposes the textarea itself as
// `textarea`, so that is read before any root.

function fieldOf(area) {
  return area?.textarea ?? area?.$el ?? area
}

/**
 * `text` spliced into `value` at the caret of `el`, or appended when there is
 * no live element to read a caret off.
 *
 * Returns the new string rather than writing through the ref: the caller owns
 * the ref, and on a composer it is a field of a draft object rather than a ref
 * of its own.
 */
export function withCaretInsert(el, value, text) {
  const node = fieldOf(el)
  const current = value || ''
  const at = node?.selectionStart
  if (at === undefined || at === null) return current + text
  return current.slice(0, at) + text + current.slice(node.selectionEnd ?? at)
}

/**
 * The element to read a caret off for `event`, or null to append.
 *
 * A composer binds paste on the whole FORM, so the event may have landed in
 * the title, a tag field or the file input. Only a paste into the body has a
 * caret that means anything to the body's text; anywhere else, an offset read
 * off the wrong field would splice the reference into the middle of a word.
 */
export function caretTarget(event, area) {
  const node = fieldOf(area)
  return node && event?.target === node ? node : null
}
