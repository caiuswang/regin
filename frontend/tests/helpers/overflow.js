/**
 * Shared no-horizontal-overflow / squished-column / settle helpers.
 *
 * Extracted from responsive.spec.js so a second spec file (live-card.spec.js)
 * can reuse the exact same detectors without `import`-ing one .spec.js from
 * another — doing that would re-execute responsive.spec.js's top-level
 * `test.describe(...)` calls a second time under the importing file (they'd
 * register twice in `playwright test --list`). A plain, non-spec module is
 * the safe way to share this logic.
 */

/**
 * Measure the app content pane's horizontal overflow. Returns the pane's
 * scroll/client widths plus, when it overflows, the offending descendants so
 * a failure names the culprit instead of just a number. A 1px rounding slack
 * is allowed (sub-pixel layout).
 */
export async function contentOverflow(page) {
  return page.evaluate(() => {
    const pane = document.querySelector('.content-scroll')
    if (!pane) return { pane: false, scrollWidth: 0, clientWidth: 0, offenders: [] }
    const slack = pane.clientWidth + 1
    const offenders = []
    if (pane.scrollWidth > slack) {
      for (const el of pane.querySelectorAll('*')) {
        const r = el.getBoundingClientRect()
        if (r.right > pane.getBoundingClientRect().right + 1 && r.width > 0) {
          offenders.push(
            `${el.tagName.toLowerCase()}.${(el.className || '').toString().split(/\s+/).slice(0, 3).join('.')}`
          )
          if (offenders.length >= 6) break
        }
      }
    }
    return {
      pane: true,
      scrollWidth: pane.scrollWidth,
      clientWidth: pane.clientWidth,
      offenders: [...new Set(offenders)],
    }
  })
}

export async function settle(page) {
  await page.waitForLoadState('domcontentloaded')
  // Give data-driven views a beat to fetch + render their tables/grids.
  await page.waitForLoadState('networkidle').catch(() => {})
  await page.waitForTimeout(300)
}

/**
 * Detect a text column *collapsed* so narrow (a flex sibling that won't
 * shrink eating all the width) that its text wraps one CHARACTER per line —
 * the "renders vertically" breakage. This does NOT trigger horizontal
 * overflow (the text wraps), so `contentOverflow` above misses it.
 *
 * The bar is deliberately strict — box < 24px wide (can't fit even ~2
 * characters) AND taller than ~4 lines. A merely-narrow-but-functional cell
 * (e.g. a long id that legitimately word-wraps inside a scrollable table
 * column, ~26px+) is NOT flagged; only a true collapse-to-a-sliver is.
 */
export async function squishedColumns(page) {
  return page.evaluate(() => {
    const pane = document.querySelector('.content-scroll')
    if (!pane) return []
    const out = []
    for (const el of pane.querySelectorAll('*')) {
      const hasText = [...el.childNodes].some(
        (n) => n.nodeType === 3 && n.textContent.trim().length > 8)
      if (!hasText) continue
      const b = el.getBoundingClientRect()
      const lh = parseFloat(getComputedStyle(el).lineHeight) || 16
      if (b.width > 0 && b.width < 24 && b.height > lh * 4) {
        out.push(`${el.tagName.toLowerCase()} w=${Math.round(b.width)} "${el.textContent.trim().slice(0, 24)}"`)
        if (out.length >= 5) break
      }
    }
    return out
  })
}
