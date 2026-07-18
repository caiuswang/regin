/**
 * grab — point at a UI element, get agent-ready context on the clipboard.
 *
 * Closes the last gap in the frontend debugging loop: describing a defect in
 * words ("the column is too narrow") makes an agent guess which element you
 * mean, and that guessing is the expensive part. Pointing removes it.
 *
 * Press Ctrl+Shift+G to arm, hover to highlight, click to copy, Esc to
 * cancel. The payload carries the component stack (from the `data-component`
 * stamps installed in main.js) plus a ready-to-run `dom-measure --explain`
 * command, so one paste gives both the location and the diagnosis.
 *
 * Dev-only: main.js imports this dynamically behind `import.meta.env.DEV`,
 * so it is absent from production bundles entirely.
 *
 * Inspired by react-grab (github.com/aidenybai/react-grab), which is
 * React-only; this reads Vue's `__file` stamps instead of a fiber tree.
 */

const HOTKEY_HINT = 'Ctrl+Shift+G'
const MAX_STACK = 6

/** Short, readable description of an element: tag + id + a few classes. */
function describe(el) {
  const id = el.id ? `#${el.id}` : ''
  const cls = (el.getAttribute('class') || '').trim()
  const classes = cls ? '.' + cls.split(/\s+/).slice(0, 4).join('.') : ''
  return `${el.tagName.toLowerCase()}${id}${classes}`
}

/**
 * A selector that re-finds this exact element later, preferring the form a
 * human can read.
 *
 * Order: id → unique visible text → a short structural path anchored at the
 * nearest component root. The naive alternative (nth-child all the way to
 * <body>) produces a 12-step chain that is unreadable and breaks on any
 * markup change above the target.
 *
 * `:has-text()` is a Playwright locator pseudo, not valid querySelector CSS
 * — which is fine, because dom-measure resolves these through Playwright.
 */
function uniqueSelector(el) {
  if (el.id) return `#${CSS.escape(el.id)}`

  const tag = el.tagName.toLowerCase()
  const text = (el.textContent || '').trim().replace(/\s+/g, ' ')
  if (text && text.length <= 40 && !text.includes('"')) {
    const sameText = [...document.getElementsByTagName(tag)]
      .filter((n) => (n.textContent || '').trim().replace(/\s+/g, ' ') === text)
    if (sameText.length === 1) return `${tag}:has-text("${text}")`
  }

  // Anchor at the nearest component root so the path stays short and stays
  // meaningful — it reads as "inside Card.vue, this cell".
  const anchor = el.closest('[data-component]')
  const parts = []
  let node = el
  while (node && node.nodeType === 1 && node !== document.body && node !== anchor) {
    if (node.id) { parts.unshift(`#${CSS.escape(node.id)}`); break }
    const parent = node.parentElement
    if (!parent) break
    const index = Array.prototype.indexOf.call(parent.children, node) + 1
    const sameTag = [...parent.children].filter((c) => c.tagName === node.tagName)
    // nth-child only earns its keep when siblings are ambiguous.
    parts.unshift(sameTag.length > 1
      ? `${node.tagName.toLowerCase()}:nth-child(${index})`
      : node.tagName.toLowerCase())
    node = parent
  }
  const path = parts.join(' > ')
  if (anchor && anchor !== el) {
    return `[data-component="${anchor.getAttribute('data-component')}"] ${path}`
  }
  return path
}

/** Component files that rendered this element, innermost first. */
function componentStack(el) {
  const out = []
  let node = el
  while (node && node.nodeType === 1) {
    const file = node.getAttribute && node.getAttribute('data-component')
    if (file && out[out.length - 1] !== file) out.push(file)
    node = node.parentElement
  }
  return out.slice(0, MAX_STACK)
}

/**
 * Reveal steps needed to reach this element from a **fresh page load**.
 *
 * Note this describes the load state, not the current one: if you can hover
 * the element then nothing above it is collapsed *right now*, so testing for
 * a closed <details> would never fire. What matters is that a <details>
 * exists above it at all (it reloads closed) and which tab panel it sits in.
 */
function revealHints(el) {
  const hints = []
  if (el.closest('details')) hints.push(`--reveal 'open-details'`)

  // Match the tab that owns *this* panel, not whichever tab happens to be
  // selected elsewhere on the page.
  const panel = el.closest('[role="tabpanel"]')
  const labelledBy = panel && panel.getAttribute('aria-labelledby')
  const tab = labelledBy
    ? document.getElementById(labelledBy)
    : (panel ? document.querySelector('[role="tab"][aria-selected="true"]') : null)
  const tabText = tab && (tab.textContent || '').trim()
  if (tabText && !tabText.includes('"')) {
    hints.unshift(`--reveal 'click:button:has-text("${tabText}")'`)
  }
  return hints
}

function buildPayload(el) {
  const rect = el.getBoundingClientRect()
  const route = location.pathname + location.search
  const selector = uniqueSelector(el)
  const stack = componentStack(el)
  const text = (el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 80)
  const reveal = revealHints(el)

  // A leading wait is not optional on these pages: every data-driven view
  // fetches after mount, so a command that navigates and measures
  // immediately reports "not found" for an element that simply had not
  // rendered yet.
  const cmd = [
    'node scripts/dom-measure.mjs',
    `--route '${route}'`,
    `--reveal 'wait:3000'`,
    ...reveal,
    `--explain '${selector}'`,
  ].join(' \\\n  ')

  return [
    `Element:   ${describe(el)}`,
    text ? `Text:      "${text}"` : null,
    `Route:     ${route}`,
    `Box:       ${Math.round(rect.width)}×${Math.round(rect.height)} at `
      + `(${Math.round(rect.x)}, ${Math.round(rect.y)})`,
    `Selector:  ${selector}`,
    stack.length ? `\nComponent stack (innermost first):\n`
      + stack.map((f) => `  ${f}`).join('\n') : null,
    `\nDiagnose with:\n${cmd}`,
  ].filter(Boolean).join('\n')
}

async function copy(textToCopy) {
  try {
    await navigator.clipboard.writeText(textToCopy)
    return true
  } catch {
    // Clipboard API needs a secure context and an active user gesture;
    // fall back so a denied permission doesn't silently drop the payload.
    const ta = document.createElement('textarea')
    ta.value = textToCopy
    ta.style.cssText = 'position:fixed;opacity:0'
    document.body.appendChild(ta)
    ta.select()
    let ok = false
    try { ok = document.execCommand('copy') } catch { ok = false }
    ta.remove()
    return ok
  }
}

export function installGrab() {
  let armed = false
  let target = null

  const box = document.createElement('div')
  box.style.cssText = 'position:fixed;pointer-events:none;z-index:2147483647;'
    + 'border:2px solid #3b82f6;background:rgba(59,130,246,0.12);'
    + 'border-radius:3px;display:none;transition:all 60ms ease-out'

  const label = document.createElement('div')
  label.style.cssText = 'position:fixed;pointer-events:none;z-index:2147483647;'
    + 'font:12px ui-monospace,monospace;background:#1e293b;color:#f8fafc;'
    + 'padding:4px 8px;border-radius:4px;display:none;max-width:60vw;'
    + 'white-space:nowrap;overflow:hidden;text-overflow:ellipsis'

  document.body.append(box, label)

  const paint = (el) => {
    const r = el.getBoundingClientRect()
    Object.assign(box.style, {
      display: 'block', left: `${r.x}px`, top: `${r.y}px`,
      width: `${r.width}px`, height: `${r.height}px`,
    })
    const stack = componentStack(el)
    label.textContent = `${describe(el)}${stack.length ? `  ←  ${stack[0]}` : ''}`
    // Flip above the element when there is no room below it.
    const top = r.y > 28 ? r.y - 26 : r.y + r.height + 6
    Object.assign(label.style, { display: 'block', left: `${r.x}px`, top: `${top}px` })
  }

  const clear = () => {
    box.style.display = 'none'
    label.style.display = 'none'
    target = null
  }

  const disarm = () => {
    armed = false
    clear()
    document.body.style.cursor = ''
  }

  const flash = (msg, bad) => {
    label.textContent = msg
    label.style.background = bad ? '#b91c1c' : '#15803d'
    label.style.display = 'block'
    setTimeout(() => {
      label.style.background = '#1e293b'
      if (!armed) label.style.display = 'none'
    }, 1400)
  }

  window.addEventListener('keydown', (e) => {
    if (e.ctrlKey && e.shiftKey && (e.key === 'G' || e.key === 'g')) {
      e.preventDefault()
      armed = !armed
      document.body.style.cursor = armed ? 'crosshair' : ''
      if (!armed) clear()
      else flash(`grab armed — click an element (Esc to cancel)`)
      return
    }
    if (e.key === 'Escape' && armed) { e.preventDefault(); disarm() }
  })

  window.addEventListener('mousemove', (e) => {
    if (!armed) return
    const el = document.elementFromPoint(e.clientX, e.clientY)
    if (!el || el === target) return
    target = el
    paint(el)
  }, { passive: true })

  // Capture phase: the app's own click handlers must not fire while grabbing.
  window.addEventListener('click', async (e) => {
    if (!armed || !target) return
    e.preventDefault()
    e.stopPropagation()
    const el = target
    disarm()
    const ok = await copy(buildPayload(el))
    flash(ok ? `copied ${describe(el)} — paste into your agent`
             : 'copy failed — see console', !ok)
    if (!ok) console.warn('[grab] clipboard blocked; payload:\n' + buildPayload(el))
  }, true)

  console.info(`[grab] press ${HOTKEY_HINT} to copy an element's source context`)
}
