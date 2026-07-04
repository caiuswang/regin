import { test, expect } from './auth-fixture.js'

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
]

// Measure the app content pane's horizontal overflow. Returns the pane's
// scroll/client widths plus, when it overflows, the offending descendants so a
// failure names the culprit instead of just a number. A 1px rounding slack is
// allowed (sub-pixel layout).
async function contentOverflow(page) {
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

async function settle(page) {
  await page.waitForLoadState('domcontentloaded')
  // Give data-driven views a beat to fetch + render their tables/grids.
  await page.waitForLoadState('networkidle').catch(() => {})
  await page.waitForTimeout(300)
}

// Detect a text column *collapsed* so narrow (a flex sibling that won't shrink
// eating all the width) that its text wraps one CHARACTER per line — the
// "renders vertically" breakage. This does NOT trigger horizontal overflow (the
// text wraps), so the overflow invariant above misses it.
//
// The bar is deliberately strict — box < 24px wide (can't fit even ~2
// characters) AND taller than ~4 lines. A merely-narrow-but-functional cell
// (e.g. a long id that legitimately word-wraps inside a scrollable table
// column, ~26px+) is NOT flagged; only a true collapse-to-a-sliver is.
async function squishedColumns(page) {
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
