// Wraps query matches in <mark> across a subtree that is already rendered.
//
// The search preview reuses the presentation renderers verbatim, and those are
// pure over the span: the text they paint — including MarkdownContent's
// v-html — never passes through markLine(), so there is no string seam to
// highlight at. Marking the rendered text nodes is the only one left.
//
// The unwrap runs in `beforeUpdate` rather than only before a re-mark because
// Vue keeps a reference to the FIRST fragment of every text node it
// interpolates, and splitText leaves that reference on the head. Patching a
// split node would keep the stale <mark> siblings beside the new text, so the
// subtree is restored to whole text nodes before Vue is allowed to touch it.

const MARK_ATTR = 'data-search-hit'
const SKIP = new Set(['SCRIPT', 'STYLE', 'TEXTAREA', 'INPUT', 'MARK'])

// Every text node splitText() produced, so the unwrap can re-merge exactly
// those and nothing else. A subtree-wide normalize() merges them too, but it
// additionally deletes every EMPTY text node — and Vue uses empty text nodes
// as Fragment anchors and keeps live references to them. Removing one makes a
// later insertBefore target a node no longer in the tree (NotFoundError), and
// measurably did: 9 anchors under one preview fell to 5 after a few mark
// cycles.
const SPLIT = new WeakSet()

// Merges our own fragments back into the node ahead of them — which, for the
// first run in a node, is the one Vue interpolated and still references, so
// its identity survives the round trip.
function mergeSplits(parent) {
  let node = parent.firstChild
  while (node) {
    const next = node.nextSibling
    const prev = node.previousSibling
    if (node.nodeType === Node.TEXT_NODE && SPLIT.has(node)
        && prev?.nodeType === Node.TEXT_NODE) {
      prev.nodeValue += node.nodeValue
      SPLIT.delete(node)
      node.remove()
    }
    node = next
  }
}

export function clearMarks(root) {
  const marks = root?.querySelectorAll?.(`mark[${MARK_ATTR}]`)
  if (!marks?.length) return
  const parents = new Set()
  for (const mark of marks) {
    const text = document.createTextNode(mark.textContent)
    SPLIT.add(text)
    parents.add(mark.parentNode)
    mark.replaceWith(text)
  }
  for (const parent of parents) mergeSplits(parent)
}

function matchingTextNodes(root, needle) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode: node => (
      !SKIP.has(node.parentNode?.nodeName?.toUpperCase() || '')
      && node.nodeValue.toLowerCase().includes(needle)
        ? NodeFilter.FILTER_ACCEPT
        : NodeFilter.FILTER_REJECT),
  })
  const out = []
  while (walker.nextNode()) out.push(walker.currentNode)
  return out
}

function markNode(node, query, needle) {
  const lower = node.nodeValue.toLowerCase()
  let rest = node
  let consumed = 0
  let at = lower.indexOf(needle)
  while (at > -1) {
    const hit = rest.splitText(at - consumed)
    const tail = hit.splitText(query.length)
    SPLIT.add(hit)
    SPLIT.add(tail)
    const mark = document.createElement('mark')
    mark.setAttribute(MARK_ATTR, '')
    hit.replaceWith(mark)
    mark.appendChild(hit)
    rest = tail
    consumed = at + query.length
    at = lower.indexOf(needle, consumed)
  }
}

export function applyMarks(root, query) {
  clearMarks(root)
  const text = String(query || '')
  // An empty query must produce no element at all: a zero-length <mark> paints
  // as a 2px amber sliver with nothing in it.
  if (!root || !text) return
  const needle = text.toLowerCase()
  for (const node of matchingTextNodes(root, needle)) markNode(node, text, needle)
}

export const vHighlightText = {
  mounted(el, binding) { applyMarks(el, binding.value) },
  beforeUpdate(el) { clearMarks(el) },
  updated(el, binding) { applyMarks(el, binding.value) },
}
