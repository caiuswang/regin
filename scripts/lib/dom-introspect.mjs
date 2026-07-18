/**
 * In-page introspection payloads for dom-measure.
 *
 * Each export is serialized into the browser by Playwright's `evaluate`,
 * so it must be **self-contained**: helpers are nested inside, because a
 * module-level function would not exist in the page's scope. Returns plain
 * JSON so the agent reads structured facts rather than re-deriving them
 * from a screenshot.
 */

/**
 * Full diagnosis of one element plus its ancestor chain.
 *
 * The ancestor walk is the point: a clipping parent, a scroll container, a
 * stacking-context boundary, or a fixed-position containing block are all
 * invisible both in the element's own styles and in a screenshot, and are
 * the usual root cause of a layout bug that "makes no sense".
 */
export function explainElement(el, opts) {
  const maxDepth = (opts && opts.maxDepth) || 12

  // An element's paint order is decided by its nearest stacking-context
  // ancestor, not by its own z-index — so a z-index that "does nothing"
  // almost always means a context boundary sits above it.
  const stackingReasons = (node, cs, isRoot) => {
    if (isRoot) return ['root element']
    const out = []
    const pos = cs.position
    const z = cs.zIndex
    if ((pos === 'absolute' || pos === 'relative') && z !== 'auto') {
      out.push(`position: ${pos} + z-index: ${z}`)
    }
    if (pos === 'fixed' || pos === 'sticky') out.push(`position: ${pos}`)
    if (z !== 'auto' && node.parentElement) {
      const pd = getComputedStyle(node.parentElement).display
      if (/flex|grid/.test(pd)) out.push(`flex/grid child + z-index: ${z}`)
    }
    const op = parseFloat(cs.opacity)
    if (!Number.isNaN(op) && op < 1) out.push(`opacity: ${cs.opacity}`)
    if (cs.mixBlendMode && cs.mixBlendMode !== 'normal') {
      out.push(`mix-blend-mode: ${cs.mixBlendMode}`)
    }
    for (const p of ['transform', 'filter', 'backdropFilter', 'perspective',
                     'clipPath', 'maskImage']) {
      if (cs[p] && cs[p] !== 'none') out.push(`${p}: ${String(cs[p]).slice(0, 60)}`)
    }
    if (cs.isolation === 'isolate') out.push('isolation: isolate')
    if (cs.willChange && /transform|opacity|filter|perspective/.test(cs.willChange)) {
      out.push(`will-change: ${cs.willChange}`)
    }
    if (cs.contain && /paint|layout|strict|content/.test(cs.contain)) {
      out.push(`contain: ${cs.contain}`)
    }
    return out
  }

  // These properties make an element the containing block for
  // `position: fixed` descendants — why a fixed child can anchor to a
  // parent box instead of the viewport.
  const fixedCbReasons = (cs) => {
    const out = []
    for (const p of ['transform', 'perspective', 'filter', 'backdropFilter']) {
      if (cs[p] && cs[p] !== 'none') out.push(`${p}: ${String(cs[p]).slice(0, 60)}`)
    }
    if (cs.willChange && /transform|perspective|filter/.test(cs.willChange)) {
      out.push(`will-change: ${cs.willChange}`)
    }
    if (cs.contain && /paint|layout|strict|content/.test(cs.contain)) {
      out.push(`contain: ${cs.contain}`)
    }
    return out
  }

  const LAYOUT_PROPS = [
    'display', 'position', 'zIndex', 'overflow', 'overflowX', 'overflowY',
    'width', 'height', 'minWidth', 'maxWidth', 'minHeight', 'maxHeight',
    'boxSizing', 'flexBasis', 'flexGrow', 'flexShrink', 'flexWrap',
    'alignItems', 'justifyContent', 'gap',
    'gridTemplateColumns', 'gridTemplateRows',
    'marginTop', 'marginRight', 'marginBottom', 'marginLeft',
    'paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft',
    'borderWidth', 'borderRadius',
    'whiteSpace', 'textOverflow', 'wordBreak', 'overflowWrap',
    'fontSize', 'fontFamily', 'fontWeight', 'lineHeight', 'letterSpacing',
    'color', 'backgroundColor', 'opacity', 'transform', 'tableLayout',
    'visibility', 'pointerEvents', 'contain', 'isolation', 'willChange',
  ]

  // Drop CSS initial values so the payload carries signal, not defaults.
  const DEFAULTS = new Set(['none', 'normal', 'auto', 'visible', '0px', 'static'])
  const pick = (cs) => {
    const out = {}
    for (const p of LAYOUT_PROPS) {
      const v = cs[p]
      if (v === '' || v == null || DEFAULTS.has(v)) continue
      out[p] = typeof v === 'string' && v.length > 80 ? v.slice(0, 80) + '…' : v
    }
    return out
  }

  const describe = (node) => {
    const id = node.id ? `#${node.id}` : ''
    const cn = node.getAttribute && node.getAttribute('class')
    const cls = cn && cn.trim()
      ? '.' + cn.trim().split(/\s+/).slice(0, 4).join('.') : ''
    return `${node.tagName.toLowerCase()}${id}${cls}`
  }
  const rect = (node) => {
    const r = node.getBoundingClientRect()
    const n = (x) => +x.toFixed(1)
    return { x: n(r.x), y: n(r.y), w: n(r.width), h: n(r.height) }
  }
  const sourceOf = (node) => node.getAttribute('data-v-inspector')
    || node.getAttribute('data-component') || null

  // An element that is attached but not rendered measures as an all-zero
  // box. Reporting those numbers as if they were real is the worst thing
  // this tool could do — every downstream conclusion would be wrong — so
  // find the ancestor responsible and say so instead.
  const notRenderedReason = () => {
    const r = el.getBoundingClientRect()
    if (r.width !== 0 || r.height !== 0) return null
    let n = el
    while (n && n !== document.documentElement) {
      if (n.tagName === 'DETAILS' && !n.open) {
        return { selector: describe(n), reason: 'ancestor <details> is closed',
                 fix: `--reveal 'open-details'` }
      }
      const ncs = getComputedStyle(n)
      if (ncs.display === 'none') {
        return { selector: describe(n), reason: 'display: none',
                 fix: 'reveal the tab/panel that mounts this element' }
      }
      if (ncs.visibility === 'hidden') {
        return { selector: describe(n), reason: 'visibility: hidden', fix: null }
      }
      if (ncs.contentVisibility === 'hidden') {
        return { selector: describe(n), reason: 'content-visibility: hidden', fix: null }
      }
      n = n.parentElement
    }
    return { selector: describe(el), reason: 'zero-size box (not laid out)', fix: null }
  }
  const hidden = notRenderedReason()
  if (hidden) {
    return {
      element: describe(el),
      source: sourceOf(el),
      rendered: false,
      hiddenBy: hidden,
      note: 'Element is in the DOM but not rendered — geometry would be all '
          + 'zeros and is omitted. Add a --reveal step, then re-run.',
    }
  }

  const cs = getComputedStyle(el)
  const selfStacking = stackingReasons(el, cs, el === document.documentElement)
  const result = {
    element: describe(el),
    source: sourceOf(el),
    text: (el.textContent || '').trim().slice(0, 60),
    box: rect(el),
    computed: pick(cs),
    // The target's own context matters as much as its ancestors': an
    // element that opens a stacking context traps its children's z-index.
    ...(selfStacking.length ? { createsStackingContext: selfStacking } : {}),
    overflow: {
      scrollWidth: el.scrollWidth, clientWidth: el.clientWidth,
      scrollHeight: el.scrollHeight, clientHeight: el.clientHeight,
      overflowingX: el.scrollWidth > el.clientWidth + 1,
      overflowingY: el.scrollHeight > el.clientHeight + 1,
    },
    ancestors: [],
  }

  // Measure the text's own width independently of its box: a `table-fixed`
  // cell keeps its frozen width no matter how far the text spills, so the
  // box rect alone cannot reveal the overflow.
  try {
    const range = document.createRange()
    range.selectNodeContents(el)
    const r = range.getBoundingClientRect()
    result.contentWidth = +r.width.toFixed(1)
    result.contentOverflowsBox = r.width > el.clientWidth + 1
  } catch { /* element has no text content */ }

  let node = el.parentElement
  let depth = 1
  let nearestStacking = null
  let clippedBy = null
  let scrollParent = null
  let fixedContainer = null

  while (node && depth <= maxDepth) {
    const acs = getComputedStyle(node)
    const isRoot = node === document.documentElement
    const stacking = stackingReasons(node, acs, isRoot)
    const fixedCb = fixedCbReasons(acs)
    const ov = acs.overflow + acs.overflowX + acs.overflowY
    const hides = /hidden|clip/.test(ov)
    const scrolls = /auto|scroll/.test(ov)
      && (node.scrollWidth > node.clientWidth + 1
          || node.scrollHeight > node.clientHeight + 1)

    const flags = []
    if (stacking.length) flags.push('stacking-context')
    if (hides) flags.push('clips-overflow')
    if (scrolls) flags.push('scroll-container')
    if (fixedCb.length) flags.push('containing-block-for-fixed')

    if (!nearestStacking && stacking.length) {
      nearestStacking = { selector: describe(node), depth, reasons: stacking }
    }
    if (!clippedBy && hides) {
      clippedBy = { selector: describe(node), depth, overflow: acs.overflow }
    }
    if (!scrollParent && scrolls) scrollParent = { selector: describe(node), depth }
    if (!fixedContainer && fixedCb.length) {
      fixedContainer = { selector: describe(node), depth, reasons: fixedCb }
    }

    // Emit only ancestors carrying layout-relevant signal — a chain of
    // plain <div>s is noise the agent pays tokens to read. A component-root
    // stamp always counts: it is what maps this element back to a file.
    const interesting = flags.length
      || sourceOf(node)
      || acs.display !== 'block'
      || acs.position !== 'static'
      || parseFloat(acs.paddingLeft) > 0
      || acs.tableLayout === 'fixed'
    if (interesting) {
      result.ancestors.push({
        depth,
        selector: describe(node),
        ...(sourceOf(node) ? { source: sourceOf(node) } : {}),
        box: rect(node),
        ...(flags.length ? { flags } : {}),
        display: acs.display,
        ...(acs.position !== 'static' ? { position: acs.position } : {}),
        ...(acs.zIndex !== 'auto' ? { zIndex: acs.zIndex } : {}),
        ...(acs.overflow !== 'visible' ? { overflow: acs.overflow } : {}),
        ...(acs.tableLayout !== 'auto' ? { tableLayout: acs.tableLayout } : {}),
        ...(stacking.length ? { stackingReasons: stacking } : {}),
      })
    }
    node = node.parentElement
    depth++
  }

  result.diagnosis = {
    nearestStackingContext: nearestStacking,
    clippedBy,
    scrollParent,
    containingBlockForFixed: fixedContainer,
  }
  return result
}

/**
 * Resolved values of the CSS custom properties in effect at the document
 * root, optionally narrowed to names containing `filter`.
 *
 * The *resolved* value is what matters: a token differs between light and
 * dark themes and can be overridden per-subtree, so the declaration in
 * style.css does not tell you what it became at runtime.
 */
export function resolveTokens(filter) {
  const cs = getComputedStyle(document.documentElement)
  const out = {}
  for (const name of Array.from(cs)) {
    if (!name.startsWith('--')) continue
    if (filter && !name.includes(filter)) continue
    out[name] = cs.getPropertyValue(name).trim()
  }
  return out
}
