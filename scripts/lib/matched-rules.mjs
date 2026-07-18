/**
 * "Which CSS rule actually won?" — answered from the cascade, not guessed
 * from source.
 *
 * Reading a stylesheet tells you what a rule *declares*; it does not tell you
 * which declaration survived specificity, order, `!important`, and the
 * inherited chain. That gap is where CSS debugging loops live. This wraps
 * CDP's `CSS.getMatchedStylesForNode` — the API that backs the DevTools
 * Styles panel — so the answer is authoritative.
 *
 * Neither Playwright MCP nor Chrome DevTools MCP exposes this
 * (ChromeDevTools/chrome-devtools-mcp#86 is still open), so it is hand-rolled
 * over a raw CDP session.
 */

// Properties worth reporting by default: the ones that decide layout and
// stacking. Everything else is noise unless explicitly asked for.
const DEFAULT_PROPS = [
  'position', 'top', 'right', 'bottom', 'left', 'z-index',
  'display', 'overflow', 'overflow-x', 'overflow-y',
  'width', 'min-width', 'max-width', 'height', 'min-height', 'max-height',
  'margin', 'padding', 'transform', 'opacity', 'contain', 'isolation',
  'white-space', 'text-overflow', 'table-layout', 'flex', 'grid-template-columns',
]

const TARGET_ATTR = 'data-dm-target'

/**
 * CDP addresses nodes by nodeId, and DOM.querySelector only speaks real CSS —
 * but our selectors may be Playwright locators (`:has-text(...)`). Tag the
 * element Playwright resolved, then look the tag up over CDP.
 */
async function nodeIdFor(page, cdp, selector) {
  const locator = page.locator(selector).first()
  await locator.evaluate((el, attr) => el.setAttribute(attr, '1'), TARGET_ATTR)
  try {
    const { root } = await cdp.send('DOM.getDocument', { depth: -1 })
    const { nodeId } = await cdp.send('DOM.querySelector', {
      nodeId: root.nodeId, selector: `[${TARGET_ATTR}]`,
    })
    return nodeId || null
  } finally {
    await locator.evaluate((el, attr) => el.removeAttribute(attr), TARGET_ATTR)
      .catch(() => {})
  }
}

function sourceOf(sheets, rule) {
  const header = rule.styleSheetId ? sheets.get(rule.styleSheetId) : null
  if (!header) return rule.origin === 'user-agent' ? 'user-agent' : '(inline)'
  const url = header.sourceURL || '(inline <style>)'
  const line = rule.style && rule.style.range
    ? header.startLine + rule.style.range.startLine + 1 : null
  const short = url.replace(/^https?:\/\/[^/]+\//, '').split('?')[0]
  return line ? `${short}:${line}` : short
}

/**
 * Walk the cascade for one property and mark exactly one declaration as the
 * winner. CDP returns matched rules from least to most precedence, so the
 * last non-important declaration wins unless an `!important` one exists.
 */
function resolveWinner(entries) {
  let winner = null
  for (const e of entries) {
    if (e.important) winner = e
    else if (!winner || !winner.important) winner = e
  }
  return winner
}

export async function matchedRules(page, selector, { props } = {}) {
  const wanted = props === 'all' ? null
    : new Set(props && props.length ? props : DEFAULT_PROPS)

  const cdp = await page.context().newCDPSession(page)
  const sheets = new Map()
  cdp.on('CSS.styleSheetAdded', ({ header }) => sheets.set(header.styleSheetId, header))
  await cdp.send('DOM.enable')
  // CSS.enable replays styleSheetAdded for sheets already parsed, so the
  // handler above still sees them despite being attached after navigation.
  await cdp.send('CSS.enable')

  const nodeId = await nodeIdFor(page, cdp, selector)
  if (!nodeId) {
    await cdp.detach().catch(() => {})
    return { selector, found: false }
  }

  const styles = await cdp.send('CSS.getMatchedStylesForNode', { nodeId })

  // property -> [{value, important, from, source}] in cascade order
  const byProp = new Map()
  const push = (name, value, important, from, source) => {
    const prop = name.toLowerCase()
    if (wanted && !wanted.has(prop)) return
    if (!byProp.has(prop)) byProp.set(prop, [])
    byProp.get(prop).push({ value, important, from, source })
  }

  for (const { rule } of styles.matchedCSSRules || []) {
    if (rule.origin === 'user-agent') continue
    const sel = rule.selectorList ? rule.selectorList.text : '(unknown)'
    const src = sourceOf(sheets, rule)
    for (const p of (rule.style && rule.style.cssProperties) || []) {
      if (p.disabled || p.parsedOk === false) continue
      // CDP lists both shorthands and their longhand expansions; the
      // expansions carry no range and would double-report every rule.
      if (!p.range) continue
      push(p.name, p.value, !!p.important, sel, src)
    }
  }
  if (styles.inlineStyle) {
    for (const p of styles.inlineStyle.cssProperties || []) {
      if (p.disabled || !p.range) continue
      push(p.name, p.value, !!p.important, 'element style attribute', '(inline)')
    }
  }

  const out = []
  for (const [prop, entries] of byProp) {
    const winner = resolveWinner(entries)
    out.push({
      property: prop,
      winner: winner
        ? { value: winner.value, from: winner.from, source: winner.source,
            important: winner.important || undefined }
        : null,
      overriddenBy: entries.length - 1,
      ...(entries.length > 1 ? {
        losers: entries.filter((e) => e !== winner).map((e) => ({
          value: e.value, from: e.from, source: e.source,
        })),
      } : {}),
    })
  }
  out.sort((a, b) => a.property.localeCompare(b.property))

  // Inherited declarations matter for properties the element never sets
  // itself — a font or white-space arriving from three levels up.
  const inherited = []
  for (const level of styles.inherited || []) {
    for (const { rule } of level.matchedCSSRules || []) {
      if (rule.origin === 'user-agent') continue
      for (const p of (rule.style && rule.style.cssProperties) || []) {
        if (!p.range || p.disabled) continue
        const prop = p.name.toLowerCase()
        if (wanted && !wanted.has(prop)) continue
        if (byProp.has(prop)) continue
        inherited.push({ property: prop, value: p.value,
                         from: rule.selectorList ? rule.selectorList.text : '?',
                         source: sourceOf(sheets, rule) })
      }
    }
  }

  await cdp.detach().catch(() => {})
  return {
    selector, found: true, properties: out,
    ...(inherited.length ? { inherited: inherited.slice(0, 12) } : {}),
  }
}
