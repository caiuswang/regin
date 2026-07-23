import { test, expect } from './auth-fixture.js'
import { contentOverflow, settle, squishedColumns } from './helpers/overflow.js'

test.describe('Responsive layout', () => {
  test('mobile shows hamburger button and hides inline nav links', async ({ page, viewport }) => {
    test.skip(!viewport || viewport.width >= 768, 'mobile-viewport-only assertion')
    await page.goto('/')

    const hamburger = page.locator('button.mobile-menu-btn[aria-label="Open navigation"]')
    await expect(hamburger).toBeVisible()

    await expect(page.locator('nav.sb-nav .sb-item', { hasText: 'Patterns' })).toBeHidden()
  })

  test('mobile hamburger opens drawer with nav links', async ({ page, viewport }) => {
    test.skip(!viewport || viewport.width >= 768, 'mobile-viewport-only assertion')
    await page.goto('/')

    await page.locator('button.mobile-menu-btn[aria-label="Open navigation"]').click()

    const drawer = page.locator('.p-drawer')
    await expect(drawer).toBeVisible()
    for (const label of ['Patterns', 'Skills', 'Rules', 'Trace', 'Settings']) {
      await expect(drawer.locator('nav a', { hasText: label })).toBeVisible()
    }
  })

  test('tablet shows inline nav links, no hamburger', async ({ page, viewport }) => {
    test.skip(!viewport || viewport.width < 768, 'tablet+-viewport-only assertion')
    await page.goto('/')

    await expect(page.locator('button.mobile-menu-btn[aria-label="Open navigation"]')).toBeHidden()
    await expect(page.locator('nav.sb-nav .sb-item', { hasText: 'Patterns' })).toBeVisible()
  })
})

/**
 * No-horizontal-overflow invariant.
 *
 * AppLayout wraps every page in `.content { overflow: hidden }` over
 * `.content-scroll { overflow-y: auto }` — and because only overflow-y is set,
 * overflow-x computes to `auto`, so ANY child wider than the viewport makes the
 * whole content pane scroll sideways (headers and all). That sideways scroll IS
 * the "page renders incorrectly on mobile" symptom. The fix for every offender
 * (wrap wide tables in their own overflow-x-auto, stack fixed side-panels,
 * collapse fixed-count grids, restore the trace timeline's local scroll) is
 * exactly what makes this invariant hold again.
 *
 * The invariant is valid at every width, so it runs on both the mobile
 * (iPhone SE, 375px) and tablet projects.
 */

// Routes reachable without a path param. Detail pages are covered separately
// below by clicking into the first row.
const STATIC_ROUTES = [
  '/',
  '/repos',
  '/patterns',
  '/skills',
  '/prompt-templates',
  '/inbox',
  '/memory',
  '/grades',
  '/audit',
  '/rules',
  '/plans',
  '/settings',
  '/account',
  '/trace/sessions',
  '/trace/triggers',
  '/trace/skill-reads',
  '/trace/mcp-calls',
  '/trace/ingest-errors',
  '/live', // no id -> latest session (mobile live-tail card, docs/mobile-progress-card-design.md)
]

// contentOverflow / settle / squishedColumns now live in ./helpers/overflow.js
// so live-card.spec.js can reuse the exact same detectors.

test.describe('No horizontal overflow at narrow widths', () => {
  for (const route of STATIC_ROUTES) {
    test(`content pane does not scroll sideways on ${route}`, async ({ page }) => {
      const errors = []
      page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })

      await page.goto(route)
      await settle(page)

      const m = await contentOverflow(page)
      test.skip(!m.pane, 'content pane not present (redirected to login/other)')
      expect(
        m.scrollWidth,
        `${route}: content pane overflows (${m.scrollWidth} > ${m.clientWidth}); offenders: ${m.offenders.join(', ') || 'unknown'}`
      ).toBeLessThanOrEqual(m.clientWidth + 1)

      // No layout-time console errors on the route either.
      expect(errors, `${route} console errors: ${errors.join(' | ')}`).toEqual([])

      // No column starved so narrow it wraps per-character.
      const squished = await squishedColumns(page)
      expect(squished, `${route} squished columns: ${squished.join(' | ')}`).toEqual([])
    })
  }
})

test.describe('Detail pages — no horizontal overflow', () => {
  test('session trace detail: timeline scrolls locally, pane does not', async ({ page, viewport }) => {
    await page.goto('/trace/sessions')
    await settle(page)
    // On mobile the `.tbl` is `hidden sm:table` and a card list is shown
    // instead, so the first session link in the DOM (inside the hidden table)
    // is not clickable — target the VISIBLE link for whichever layout renders.
    const firstLink = page.locator('a[href^="/trace/sessions/"]:visible').first()
    if (!(await firstLink.count())) test.skip(true, 'no sessions in dev DB')
    await firstLink.click()

    // Conversation view is the default. Its chat column must not be starved to
    // a sliver by the (desktop-only) turns rail — the regression that made the
    // prompt text render one character per line on a phone.
    await page.locator('[class*="whitespace-pre-wrap"]').first().waitFor({ timeout: 10_000 }).catch(() => {})
    await page.waitForTimeout(200)
    const convOverflow = await contentOverflow(page)
    expect(
      convOverflow.scrollWidth,
      `trace detail (conversation): pane overflows; offenders: ${convOverflow.offenders.join(', ')}`
    ).toBeLessThanOrEqual(convOverflow.clientWidth + 1)
    const convSquished = await squishedColumns(page)
    expect(convSquished, `trace detail (conversation) squished: ${convSquished.join(' | ')}`).toEqual([])

    const timelineTab = page.getByRole('button', { name: 'Timeline', exact: true })
    await expect(timelineTab).toBeVisible({ timeout: 10_000 })
    await timelineTab.click()
    await page.locator('[role="row"][aria-level]').first().waitFor({ timeout: 10_000 })
    await page.waitForTimeout(200)

    // The content pane itself must not scroll sideways...
    const m = await contentOverflow(page)
    expect(
      m.scrollWidth,
      `trace detail: pane overflows (${m.scrollWidth} > ${m.clientWidth}); offenders: ${m.offenders.join(', ')}`
    ).toBeLessThanOrEqual(m.clientWidth + 1)

    // ...but on mobile the wide span timeline must remain REACHABLE via a local
    // horizontal scroll container (Pattern M), not be clipped/unreachable.
    if (viewport && viewport.width < 768) {
      const canScroll = await page.evaluate(() => {
        const c = document.querySelector('.p-treetable-table-container')
        if (!c) return null
        const ox = getComputedStyle(c).overflowX
        return { scrollable: (ox === 'auto' || ox === 'scroll'), overflowsLocally: c.scrollWidth > c.clientWidth, ox }
      })
      if (canScroll) {
        expect(canScroll.scrollable, `timeline container overflowX=${canScroll.ox} — must be auto/scroll on mobile`).toBeTruthy()
      }
    }
  })

  test('repo detail: content pane does not scroll sideways', async ({ page }) => {
    await page.goto('/repos')
    await settle(page)
    const firstRepo = page.locator('a[href^="/repos/"]:visible').first()
    if (!(await firstRepo.count())) test.skip(true, 'no repos in dev DB')
    await firstRepo.click()
    await settle(page)

    const m = await contentOverflow(page)
    test.skip(!m.pane, 'content pane not present')
    expect(
      m.scrollWidth,
      `repo detail: pane overflows (${m.scrollWidth} > ${m.clientWidth}); offenders: ${m.offenders.join(', ')}`
    ).toBeLessThanOrEqual(m.clientWidth + 1)
  })

  // /memory is in STATIC_ROUTES, but that visit never selects a memory, so
  // MemoryDetail's aside — where the supersede links live — never mounts. A
  // long-titled supersede link sizes a <Button> (base: inline-flex
  // justify-center) to its full text and pushes the pane sideways.
  test('memory detail: long supersede-link titles do not widen the pane', async ({ page }) => {
    await page.goto('/memory')
    await settle(page)
    const box = page.getByPlaceholder('Search title, body, tags…')
    if (!(await box.count())) test.skip(true, 'memory search box not present')

    // Pick a memory whose detail actually renders a supersede block, and
    // prefer the longest title — the widest thing the aside has to hold.
    const target = await page.evaluate(async () => {
      const headers = { Authorization: `Bearer ${localStorage.getItem('regin_auth_token')}` }
      const list = await (await fetch('/api/memory?status=active&page=0&size=100', { headers })).json()
      let best = null
      for (const m of (list.items || []).filter(m => m.title)) {
        const rel = await (await fetch(`/api/memory/${m.id}/related`, { headers })).json()
        if (!rel.superseded_by && !(rel.supersedes || []).length) continue
        const width = Math.max(...[rel.superseded_by, ...(rel.supersedes || [])]
          .filter(Boolean).map(x => (x.title || x.kind || '').length))
        if (!best || width > best.width) best = { title: m.title, width }
      }
      return best
    })
    if (!target) test.skip(true, 'no memory with supersede links in dev DB')

    await box.fill(target.title)
    await box.press('Enter')
    await settle(page)
    const hit = page.locator('button', { hasText: target.title }).first()
    if (!(await hit.count())) test.skip(true, 'memory list did not surface the target')
    await hit.click()
    await settle(page)

    // Guard against a vacuous pass: the supersede block must have rendered.
    const links = page.locator('aside li button, aside div > button').filter({ hasText: /\S/ })
    expect(await links.count(), 'supersede block never rendered — test would pass vacuously')
      .toBeGreaterThan(0)

    const m = await contentOverflow(page)
    test.skip(!m.pane, 'content pane not present')
    expect(
      m.scrollWidth,
      `memory detail: pane overflows (${m.scrollWidth} > ${m.clientWidth}); offenders: ${m.offenders.join(', ')}`
    ).toBeLessThanOrEqual(m.clientWidth + 1)
    const squished = await squishedColumns(page)
    expect(squished, `memory detail squished: ${squished.join(' | ')}`).toEqual([])
  })
})

// Markdown / long-id detail pages: these render user markdown (wide tables,
// code blocks) and long unbreakable ids in `.cell-code` chips — a distinct
// overflow source from the app-authored `.tbl` tables, on views reachable only
// by a path param (so not in STATIC_ROUTES).
const DETAIL_ROUTES = [
  { list: '/skills', link: 'a[href^="/skills/"]', label: 'skill detail' },
  { list: '/rules', link: 'a[href^="/rules/"]', label: 'rule detail' },
  { list: '/patterns', link: 'a[href^="/patterns/"]', label: 'pattern detail' },
  { list: '/plans', link: 'a[href^="/plans/"]', label: 'plan detail' },
]

test.describe('Markdown detail pages — no horizontal overflow', () => {
  for (const { list, link, label } of DETAIL_ROUTES) {
    test(`${label} content pane does not scroll sideways`, async ({ page }) => {
      await page.goto(list)
      await settle(page)
      const first = page.locator(`${link}:visible`).first()
      if (!(await first.count())) test.skip(true, `no items under ${list} in dev DB`)
      await first.click()
      await settle(page)

      const m = await contentOverflow(page)
      test.skip(!m.pane, 'content pane not present')
      expect(
        m.scrollWidth,
        `${label}: pane overflows (${m.scrollWidth} > ${m.clientWidth}); offenders: ${m.offenders.join(', ')}`
      ).toBeLessThanOrEqual(m.clientWidth + 1)
      const squished = await squishedColumns(page)
      expect(squished, `${label} squished: ${squished.join(' | ')}`).toEqual([])
    })
  }
})

/**
 * `/schema-drift` filter row.
 *
 * Three defects this pins, each of which passes vacuously if only asserted
 * from the diff: (a) `.filter-row`/`.filter-row-label` were defined only in
 * RulesView/PatternsView *scoped* styles, so on this view the row was an
 * unstyled block with the label glued to the first chip; (b) at <=639px the
 * chip's 0.75rem side padding against a 40px touch target rendered a short
 * label ("All") as a circle; (c) the blue ramp inverts under dark, so
 * `.filter-chip.active`'s blue-800 fill went pale behind white text.
 */
async function chipMetrics(page) {
  return page.evaluate(() => {
    const row = document.querySelector('.filter-row')
    if (!row) return null
    const label = row.querySelector('.filter-row-label')
    const chips = [...row.querySelectorAll('.filter-chip')]
    const all = chips[0]
    const rowCS = getComputedStyle(row)
    const lr = label.getBoundingClientRect()
    const ar = all.getBoundingClientRect()

    // Relative luminance / WCAG contrast on the resolved computed colors.
    const lum = (rgb) => {
      const c = rgb.match(/[\d.]+/g).slice(0, 3).map((v) => {
        const s = Number(v) / 255
        return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4
      })
      return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    }
    const active = chips.find((c) => c.classList.contains('active')) || all
    const acs = getComputedStyle(active)
    const [l1, l2] = [lum(acs.backgroundColor), lum(acs.color)].sort((a, b) => b - a)

    return {
      display: rowCS.display,
      flexWrap: rowCS.flexWrap,
      labelToChipGap: ar.left - lr.right,
      allWidth: ar.width,
      allHeight: ar.height,
      activeContrast: (l1 + 0.05) / (l2 + 0.05),
    }
  })
}

test.describe('/schema-drift filter row', () => {
  test('label is spaced off the chips and the row lays out as a wrapping flex row', async ({ page }) => {
    await page.goto('/schema-drift')
    await settle(page)
    const m = await chipMetrics(page)
    test.skip(!m, 'filter row not rendered')

    expect(m.display, 'filter-row must be flex, not an unstyled block').toBe('flex')
    expect(m.flexWrap).toBe('wrap')
    expect(
      m.labelToChipGap,
      `"State:" label sits ${m.labelToChipGap}px from the first chip`
    ).toBeGreaterThanOrEqual(8)
  })

  test('the short "All" chip is a pill, not a circle, and keeps its touch target', async ({ page, viewport }) => {
    test.skip(!viewport || viewport.width >= 640, 'mobile-viewport-only assertion')
    await page.goto('/schema-drift')
    await settle(page)
    const m = await chipMetrics(page)
    test.skip(!m, 'filter row not rendered')

    expect(m.allHeight, 'chip must keep a >=40px tap target').toBeGreaterThanOrEqual(40)
    expect(
      m.allWidth / m.allHeight,
      `"All" chip is ${Math.round(m.allWidth)}x${Math.round(m.allHeight)} — reads as a circle`
    ).toBeGreaterThanOrEqual(1.35)
  })

  test('the active chip stays legible in dark mode', async ({ page }) => {
    await page.addInitScript(() => window.localStorage.setItem('regin_theme', 'dark'))
    await page.goto('/schema-drift')
    await settle(page)
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
    const m = await chipMetrics(page)
    test.skip(!m, 'filter row not rendered')

    expect(
      m.activeContrast,
      `active chip label contrast is ${m.activeContrast.toFixed(2)}:1 in dark mode`
    ).toBeGreaterThanOrEqual(4.5)
  })
})
