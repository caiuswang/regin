import { test, expect } from '@playwright/test'

import { ALL_ROUTES } from './routes.js'

const WIDTHS = [390, 1440]
const PHONE = 390
const TABLE_ROUTES = [
  '/getting-started', '/pipeline', '/runs', '/architecture', '/configuration', '/cli',
  '/topics/routing', '/topics/proposals', '/topics/evolution', '/topics/memory',
]

// Everything a route lays out, measured in one pass. A sideways scroll can hide
// behind `overflow-x: hidden` on html/body (the probe would then read 0 on a
// broken page), so the root overflow is reported alongside the widths.
function measure() {
  const scrolls = (el) => ['auto', 'scroll'].includes(getComputedStyle(el).overflowX)
  const cut = (el) => el.scrollWidth > el.clientWidth + 1
  const vw = window.innerWidth
  const right = (el) => el.getBoundingClientRect().right

  const tables = [...document.querySelectorAll('[role="table"], .pl-table')]
  const rows = tables.flatMap((t) => [...t.querySelectorAll('[role="row"], .pl-row')]
    .filter((r) => !r.matches('.pl-head, .block-table-head'))
    .map((r) => {
      const [first, second] = [...r.children].map((c) => c.getBoundingClientRect())
      return { text: r.textContent.slice(0, 40), stacked: !second || second.top >= first.bottom - 1, cut: cut(r) }
    }))

  return {
    scrollWidth: document.documentElement.scrollWidth,
    bodyScrollWidth: document.body.scrollWidth,
    innerWidth: vw,
    rootOverflow: [getComputedStyle(document.documentElement).overflowX, getComputedStyle(document.body).overflowX],
    pres: [...document.querySelectorAll('pre')].map((p) => ({
      text: p.textContent.slice(0, 40), cut: cut(p), scrolls: scrolls(p), right: right(p),
    })),
    tables: tables.map((t) => ({ text: t.textContent.slice(0, 40), cut: cut(t), scrolls: scrolls(t), right: right(t) })),
    rows,
    huds: [...document.querySelectorAll('.hud-bar')].map((h) => ({
      text: h.textContent.slice(0, 40),
      overflow: Math.max(0, ...[...h.querySelectorAll('*')].map((c) => right(c) - right(h))),
    })),
    trackLabels: [...document.querySelectorAll('.play-track-label')].map((l) => ({
      shown: l.getClientRects().length > 0, truncated: cut(l),
    })),
  }
}

for (const width of WIDTHS) {
  test.describe(`${width} px`, () => {
    test.use({ viewport: { width, height: width === PHONE ? 844 : 900 } })

    for (const path of ALL_ROUTES) {
      test(`${path} never scrolls sideways`, async ({ page }) => {
        await page.goto(path)
        // Routes are lazy chunks: measuring before the view mounts would loop
        // over empty lists and pass without checking anything. Every route,
        // the 404 included, renders a HUD bar.
        await expect(page.locator('.hud-bar').first()).toBeVisible()
        await page.evaluate(() => document.fonts.ready)
        const m = await page.evaluate(measure)

        expect(m.huds.length).toBeGreaterThan(0)
        if (TABLE_ROUTES.includes(path)) expect(m.rows.length, 'table rows rendered').toBeGreaterThan(0)
        if (path === '/') expect(m.trackLabels).toHaveLength(5)

        // I1 — and the probe is honest: nothing above the page clips overflow away.
        expect(m.rootOverflow).toEqual(['visible', 'visible'])
        expect(m.scrollWidth).toBeLessThanOrEqual(m.innerWidth)
        expect(m.bodyScrollWidth).toBeLessThanOrEqual(m.innerWidth)

        // G5 — a pre that is wider than its box scrolls inside it.
        for (const pre of m.pres) {
          expect(pre.right, `pre "${pre.text}" ends inside the viewport`).toBeLessThanOrEqual(m.innerWidth)
          if (pre.cut) expect(pre.scrolls, `pre "${pre.text}" scrolls`).toBe(true)
        }

        // G4 — every table fits or scrolls; under 640 px it fits because rows stack.
        for (const t of m.tables) {
          expect(t.right, `table "${t.text}" ends inside the viewport`).toBeLessThanOrEqual(m.innerWidth)
          if (width === PHONE) expect(t.cut, `table "${t.text}" is not cut`).toBe(false)
          else if (t.cut) expect(t.scrolls, `table "${t.text}" scrolls`).toBe(true)
        }
        if (width === PHONE) {
          for (const r of m.rows) {
            expect(r.stacked, `row "${r.text}" puts its label cell on its own line`).toBe(true)
            expect(r.cut, `row "${r.text}" is not cut`).toBe(false)
          }
        }

        // G6 — a HUD bar wraps rather than running past its border.
        for (const h of m.huds) expect(h.overflow, `HUD "${h.text}"`).toBeLessThanOrEqual(1)

        // G7 — phone track keys show the code only; desktop keeps the label, uncut.
        for (const l of m.trackLabels) {
          if (width === PHONE) expect(l.shown).toBe(false)
          else expect(l).toEqual({ shown: true, truncated: false })
        }
      })
    }
  })
}

test.describe('phone track keys', () => {
  test.use({ viewport: { width: PHONE, height: 844 } })

  test('show the code only', async ({ page }) => {
    await page.goto('/#play')
    const keys = page.getByRole('list', { name: 'Stages' }).getByRole('button')
    await expect(keys).toHaveCount(5)
    await expect(keys.first()).toHaveText('0-1', { useInnerText: true })
    await expect(keys.nth(4)).toHaveText('0-5', { useInnerText: true })
    // The hidden label still names the stage for assistive tech.
    await expect(keys.first()).toHaveAccessibleName('0-1 NEW FEATURE')
  })
})

test.describe('track labels just above the phone breakpoint', () => {
  test.use({ viewport: { width: 640, height: 900 } })

  test('wrap rather than truncate', async ({ page }) => {
    await page.goto('/')
    await expect(page.locator('.play-track-label')).toHaveCount(5)
    await page.evaluate(() => document.fonts.ready)
    const cut = await page.locator('.play-track-label').evaluateAll((ls) =>
      ls.filter((l) => l.scrollWidth > l.clientWidth + 1).map((l) => l.textContent))
    expect(cut).toEqual([])
  })
})
