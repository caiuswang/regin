import { test, expect } from './auth-fixture.js'
import { contentOverflow, settle, squishedColumns } from './helpers/overflow.js'
import { BASELINE_TRACE } from './helpers/fixtures.js'
import { selectViewMode } from './helpers/viewMode.js'
import { TEST_USER } from '../e2e-env.js'

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
  test('session trace detail: timeline spine wraps, pane does not scroll sideways', async ({ page }) => {
    // Straight to the seeded session. Clicking "the first link in the list"
    // raced every other spec seeding sessions concurrently, so which session
    // these layout assertions ran against was luck.
    await page.goto(`/trace/sessions/${BASELINE_TRACE}`)
    await settle(page)

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

    await selectViewMode(page, 'Timeline')
    const spineRows = page.locator('[data-testid="spine-row"]')
    await spineRows.first().waitFor({ timeout: 10_000 })
    await page.waitForTimeout(200)

    // The content pane itself must not scroll sideways. Unlike the old
    // TreeTable, the event spine reads vertically and its meta chips wrap, so
    // there is no wide-table local-scroll escape hatch to allow for — the pane
    // simply must never overflow horizontally at any width.
    const m = await contentOverflow(page)
    expect(
      m.scrollWidth,
      `trace detail: pane overflows (${m.scrollWidth} > ${m.clientWidth}); offenders: ${m.offenders.join(', ')}`
    ).toBeLessThanOrEqual(m.clientWidth + 1)

    // The spine rows themselves stay reachable (not clipped) on a phone.
    await expect(spineRows.first()).toBeVisible()
  })

  // The redesigned header chrome is the part most likely to reintroduce a
  // sideways scroll: the vitals strip is six cells, and the spend panels are
  // fixed-column grids. Six equal flex cells at 375px are ~60px each and blow
  // the pane out — this case fails on that layout and passes on the reflowing
  // grid, so it is the guard for the strip specifically, not just the route.
  test('session trace detail: vitals strip and spend panels fit a phone', async ({ page }) => {
    // Straight to the seeded session. Clicking "the first link in the list"
    // raced every other spec seeding sessions concurrently, so which session
    // these layout assertions ran against was luck.
    await page.goto(`/trace/sessions/${BASELINE_TRACE}`)
    await settle(page)

    const strip = page.getByTestId('trace-vitals-strip')
    await expect(strip).toBeVisible({ timeout: 10_000 })

    // Width is the wrong assertion on its own: flex cells SHRINK rather than
    // overflow, so a 6-across row "fits" a 375px pane at ~60px a cell while
    // rendering every label one word per line. The real invariant is that each
    // cell keeps enough width to show its label and value — which is what
    // forces the strip to reflow to fewer columns on a phone.
    const MIN_CELL_W = 90
    const cells = await page.evaluate(() => {
      const el = document.querySelector('[data-testid="trace-vitals-strip"]')
      if (!el) return []
      return [...el.children].map(c => ({
        label: (c.querySelector('dt')?.textContent || '?').trim(),
        w: Math.round(c.getBoundingClientRect().width),
      }))
    })
    expect(cells.length, 'vitals strip rendered no cells').toBeGreaterThan(0)
    const starved = cells.filter(c => c.w < MIN_CELL_W)
    expect(
      starved,
      `vitals cells starved below ${MIN_CELL_W}px: ${starved.map(c => `${c.label}=${c.w}px`).join(', ')}`
    ).toEqual([])

    // Expanding the spend disclosure must not widen the pane either — both
    // panels inside it are fixed-column grids.
    await page.getByTestId('trace-spend-toggle').click()
    await page.waitForTimeout(200)
    const m = await contentOverflow(page)
    expect(
      m.scrollWidth,
      `trace detail (spend panel open): pane overflows; offenders: ${m.offenders.join(', ')}`
    ).toBeLessThanOrEqual(m.clientWidth + 1)
  })

  // An OPEN Shell / Edit card is the widest thing the conversation feed ever
  // renders: header bar and dark body are one full-bleed unit inside the
  // spine's own left indent, and the body holds unwrapped command text. The
  // closed row can't prove that — this case opens one and re-measures.
  test('session trace conversation: an expanded tool card does not widen the pane', async ({ page }) => {
    // Straight to the seeded session. Clicking "the first link in the list"
    // raced every other spec seeding sessions concurrently, so which session
    // these layout assertions ran against was luck.
    await page.goto(`/trace/sessions/${BASELINE_TRACE}`)
    await settle(page)
    await page.locator('.event-spine-row').first().waitFor({ timeout: 15_000 }).catch(() => {})

    const opener = page.locator('.event-spine-row span')
      .filter({ hasText: /^(Shell|Update|Write|Create)$/ }).first()
    if (!(await opener.count())) test.skip(true, 'no expandable tool card in the first session')
    await opener.scrollIntoViewIfNeeded()
    // `force`: the sticky trace chrome and the follow-latest pill overlay the
    // feed, so the actionability check never clears even once the row is in
    // view. The row's own click handler is what we're exercising.
    await opener.click({ force: true })
    await page.waitForTimeout(400)

    const m = await contentOverflow(page)
    expect(
      m.scrollWidth,
      `trace conversation (card open): pane overflows; offenders: ${m.offenders.join(', ')}`
    ).toBeLessThanOrEqual(m.clientWidth + 1)
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

/**
 * Session Conversation View on a phone.
 *
 * Every assertion here is geometry read back off the live layout, never a
 * screenshot: the four defects this block guards were each invisible in a
 * single element's rect and obvious only in a measured pair (a flex child
 * overflowing its own box, two viewport-fixed affordances claiming one
 * corner). The pane-level `contentOverflow` invariant above cannot see any of
 * them — none of them makes `.content-scroll` scroll sideways.
 */
test.describe('Trace conversation — phone chrome', () => {
  // These are width-conditional by construction: the header's actions column
  // and the compact strip only exist below `lg`, and the corner collision
  // needs a viewport narrow enough for the rail to reach the FAB.
  const phoneOnly = (viewport) => !viewport || viewport.width >= 1024

  /** Scroll the app content pane, once it exists. */
  async function scrollFeed(page, top) {
    await page.locator('.content-scroll').waitFor({ timeout: 15_000 })
    await page.evaluate((y) => { document.querySelector('.content-scroll').scrollTop = y }, top)
    await page.waitForTimeout(400)
  }

  /** Pairwise intersection of everything pinned to the viewport corners. */
  async function floatCollisions(page) {
    return page.evaluate(() => {
      const boxes = []
      const push = (name, el) => {
        if (!el) return
        const b = el.getBoundingClientRect()
        if (b.width < 4 || b.height < 4) return
        boxes.push({ name, l: b.left, r: b.right, t: b.top, b: b.bottom })
      }
      for (const el of document.querySelectorAll('*')) {
        if (getComputedStyle(el).position !== 'fixed') continue
        push(el.getAttribute('data-testid') || el.className.toString().split(/\s+/)[0] || el.tagName, el)
      }
      // Not `position: fixed`, but in the same fight for a bottom corner and
      // therefore invisible to a fixed-only sweep — which is exactly how a
      // back-to-top moved to bottom-left came to sit on the sidebar's nav rows
      // at desktop widths with this test still green.
      push('trace-rail', document.querySelector('[data-testid="trace-rail"]'))
      push('followup-bubble', document.querySelector('.followup-bubble'))
      push('app-sidebar', document.querySelector('aside.sidebar'))
      const hits = []
      for (let i = 0; i < boxes.length; i++) {
        for (let j = i + 1; j < boxes.length; j++) {
          const a = boxes[i]; const c = boxes[j]
          if (a.l < c.r && c.l < a.r && a.t < c.b && c.t < a.b) hits.push(`${a.name} ∩ ${c.name}`)
        }
      }
      return hits
    })
  }

  // Deliberately NOT width-gated: the corner these three contend for exists at
  // every width, and the desktop collision was the one that survived a
  // phone-only fix.
  test('no two viewport-fixed affordances share a corner', async ({ page }) => {
    await page.goto(`/trace/sessions/${BASELINE_TRACE}`)
    await settle(page)
    await page.locator('.content-scroll').waitFor({ timeout: 15_000 })
    await scrollFeed(page, 1200)

    const hits = await floatCollisions(page)
    expect(hits, `overlapping viewport-fixed affordances: ${hits.join(' | ')}`).toEqual([])
  })

  test('the header actions column is left-aligned below lg, not right-hugging', async ({ page, viewport }) => {
    test.skip(phoneOnly(viewport), 'below-lg-only assertion')
    await page.goto(`/trace/sessions/${BASELINE_TRACE}`)
    await settle(page)

    const actions = page.getByTestId('trace-header-actions')
    await expect(actions).toBeVisible({ timeout: 10_000 })
    const m = await page.evaluate(() => {
      const col = document.querySelector('[data-testid="trace-header-actions"]')
      const title = document.querySelector('[data-testid="trace-sticky-header"] h1')
      const row = col.firstElementChild
      return {
        colLeft: Math.round(col.getBoundingClientRect().left),
        titleLeft: Math.round(title.getBoundingClientRect().left),
        firstChildLeft: Math.round(row.firstElementChild.getBoundingClientRect().left),
        rowLeft: Math.round(row.getBoundingClientRect().left),
      }
    })
    // The column spans the width the title does…
    expect(Math.abs(m.colLeft - m.titleLeft), `actions column starts at ${m.colLeft}, title at ${m.titleLeft}`)
      .toBeLessThanOrEqual(2)
    // …and its controls start there too, instead of being flushed right.
    expect(m.firstChildLeft - m.rowLeft, 'first action is pushed right of the column edge')
      .toBeLessThanOrEqual(2)
  })

  test('the pinned compact strip can restore a collapsed header', async ({ page, viewport }) => {
    test.skip(phoneOnly(viewport), 'below-lg-only assertion')
    test.setTimeout(90_000)

    // Seeds a long session rather than using the baseline one: the toggle
    // appears only once the header has actually left the scrollport, and on a
    // tall viewport the baseline trace is too short to ever scroll that far —
    // which made this pass on the phone project and silently prove nothing on
    // the tablet one.
    const traceId = `e2e-longfeed-${Date.now()}`
    const spans = []
    for (let i = 0; i < 25; i++) {
      const pid = `p${i}-${traceId}`
      spans.push({
        trace_id: traceId, span_id: pid, parent_id: null, name: 'prompt',
        start_time: `2026-05-08T12:${String(i).padStart(2, '0')}:00`,
        attributes: { text: `Turn ${i}: walk the next file and report what changed.`, is_test: true },
      })
      spans.push({
        trace_id: traceId, span_id: `a${i}-${traceId}`, parent_id: pid, name: 'assistant_response',
        start_time: `2026-05-08T12:${String(i).padStart(2, '0')}:30`, duration_ms: 1200,
        attributes: { text: `Answer ${i}. `.repeat(20), is_test: true },
      })
    }
    const { token } = await (await page.request.post('/api/auth/login', { data: TEST_USER })).json()
    expect((await page.request.post('/api/session-spans', {
      headers: { Authorization: `Bearer ${token}` }, data: spans,
    })).ok()).toBeTruthy()

    await page.goto(`/trace/sessions/${traceId}?view=conversation`)
    await settle(page)
    await page.locator('.content-scroll').waitFor({ timeout: 15_000 })
    await page.getByTestId('header-details-toggle').click()
    await page.waitForTimeout(300)

    // Both states, in order — the feed may open already scrolled to its tail,
    // so "the header is on screen" has to be arranged, not assumed.
    // At the top it carries its own Details button a finger's width above the
    // strip's, and two controls for one job is what the gate exists to avoid.
    await page.evaluate(() => { document.querySelector('.content-scroll').scrollTop = 0 })
    await page.waitForTimeout(400)
    await expect(page.getByTestId('header-details-toggle')).toBeVisible()
    await expect(page.getByTestId('compact-details-toggle')).toHaveCount(0)

    await page.evaluate(() => {
      const s = document.querySelector('.content-scroll')
      s.scrollTop = s.scrollHeight
    })
    await page.waitForTimeout(500)

    // The precondition, asserted rather than assumed: the toggle is SUPPOSED
    // to be absent while the header is still on screen carrying its own copy,
    // so a test that never scrolls past it would pass on the wrong reason.
    const cleared = await page.evaluate(() => {
      const s = document.querySelector('.content-scroll').getBoundingClientRect()
      const h = document.querySelector('[data-testid="trace-sticky-header"]').getBoundingClientRect()
      return h.bottom <= s.top + 1
    })
    expect(cleared, 'the header never left the scrollport — the seed is too short').toBe(true)

    // Below lg the full header is NOT sticky, so once it has scrolled away the
    // pull-to-reveal meter (lg-only) and the H shortcut (desktop-only) are both
    // gone: the strip that IS pinned must carry the way back.
    const restore = page.getByTestId('compact-details-toggle')
    await expect(restore).toBeVisible()
    await restore.click()
    await page.waitForTimeout(400)
    await expect(page.getByTestId('trace-vitals-strip')).toBeVisible()
  })

  // The collapsed header IS the digest — session status at a glance while
  // scrolling a long transcript. `shrink-0` inside an `overflow-hidden` row
  // meant a phone saw the first stat and silently lost the rest: the row ran to
  // x=559 against a pane ending at 382. `.content` sets `overflow-x: hidden`,
  // so the pane's own scrollWidth never moved and every existing overflow
  // check stayed green through it.
  test('every collapsed-header stat stays inside the pane', async ({ page, viewport }) => {
    test.skip(phoneOnly(viewport), 'below-lg-only assertion')
    test.setTimeout(90_000)

    // The vitals are injected, not hoped for: every one of the five digest
    // slots renders "—" when its aggregate is null, and a row of dashes fits
    // any width — so against the baseline session this passed just as happily
    // with the overflow still in place.
    await page.route(`**/api/sessions/${BASELINE_TRACE}/map*`, async (route) => {
      const res = await route.fetch()
      const body = await res.json()
      body.span_count_total = 646
      body.total_tokens = 69_000_000
      body.context_pct = 41
      await route.fulfill({ response: res, json: body })
    })

    await page.goto(`/trace/sessions/${BASELINE_TRACE}`)
    await settle(page)
    await page.locator('.content-scroll').waitFor({ timeout: 15_000 })
    await page.getByTestId('header-details-toggle').click()
    await page.waitForTimeout(400)

    const m = await page.evaluate(() => {
      const pane = document.querySelector('.content-scroll').getBoundingClientRect()
      const digest = document.querySelector('[data-testid="trace-header-digest"]')
      if (!digest) return null
      const clipped = [...digest.children]
        .map(c => ({ t: (c.textContent || '').replace(/\s+/g, ' ').trim(), r: Math.round(c.getBoundingClientRect().right) }))
        .filter(c => c.r > Math.round(pane.right) + 1)
      return { clipped, paneRight: Math.round(pane.right), text: digest.textContent.replace(/\s+/g, ' ').trim() }
    })
    expect(m, 'collapsed header rendered no digest').not.toBeNull()
    expect(m.text, 'the injected vitals did not reach the digest').toContain('646')
    expect(
      m.clipped,
      `digest stats cut off past the pane edge (${m.paneRight}): `
        + m.clipped.map(c => `"${c.t}"@${c.r}`).join(', ')
    ).toEqual([])
  })

  // The roster menu is right-anchored to its button and clamped to the
  // viewport. On a phone the button sits far left of the menu's width, so the
  // clamp wins and the menu lands on the left edge — leaving 62px of dead
  // space on the right at 390px, which reads as a misplaced overlay.
  //
  // Seeds its own subagent rather than leaning on the baseline session: the
  // Agents button is absent for a 0-agent session, and a test that skips in
  // every project is coverage that is not there.
  test('the agents popover fills the width it cannot right-anchor in', async ({ page, viewport }) => {
    test.skip(!viewport || viewport.width >= 768, 'one width is enough; pinned below')
    test.setTimeout(90_000)
    // Pinned, not inherited. The defect needs a viewport wide enough that a
    // 320px menu is not already the whole pane (>336px) AND a button sitting
    // left of 328 — and the `mobile` project is an iPhone SE at 320px, where
    // both branches of the placement collapse to the same number and the test
    // would pass against the broken code.
    await page.setViewportSize({ width: 390, height: 844 })

    const traceId = `e2e-popover-${Date.now()}`
    const at = i => `2026-05-08T12:00:${String(i).padStart(2, '0')}`
    const spans = [
      { trace_id: traceId, span_id: `p-${traceId}`, parent_id: null, name: 'prompt',
        start_time: at(0), attributes: { text: 'Map the breakpoints.', is_test: true } },
      { trace_id: traceId, span_id: `ag-${traceId}`, parent_id: `p-${traceId}`, name: 'tool.Agent',
        start_time: at(1),
        attributes: { subagent_type: 'explorer', description: 'Map the breakpoints',
          tool_use_id: `tu-${traceId}`, agent_id: `agent-${traceId}`, is_test: true } },
      { trace_id: traceId, span_id: `sa-${traceId}`, parent_id: `p-${traceId}`, name: 'subagent.start',
        start_time: at(1), attributes: { agent_type: 'explorer', agent_id: `agent-${traceId}`, is_test: true } },
      { trace_id: traceId, span_id: `r-${traceId}`, parent_id: `sa-${traceId}`, name: 'tool.Read',
        start_time: at(2), attributes: { file_path: 'lib/x.py', agent_id: `agent-${traceId}`, is_test: true } },
    ]
    const { token } = await (await page.request.post('/api/auth/login', { data: TEST_USER })).json()
    expect((await page.request.post('/api/session-spans', {
      headers: { Authorization: `Bearer ${token}` }, data: spans,
    })).ok()).toBeTruthy()

    await page.goto(`/trace/sessions/${traceId}?view=conversation`)
    await settle(page)

    const trigger = page.locator('button', { hasText: 'Agents' }).first()
    await expect(trigger).toBeVisible({ timeout: 15_000 })
    await trigger.click({ force: true })

    const popover = page.getByTestId('trace-agents-popover')
    await expect(popover).toBeVisible()

    const m = await page.evaluate(() => {
      const pane = document.querySelector('.content-scroll').getBoundingClientRect()
      const r = document.querySelector('[data-testid="trace-agents-popover"]').getBoundingClientRect()
      const t = [...document.querySelectorAll('button')]
        .find(b => /Agents/.test(b.textContent || '')).getBoundingClientRect()
      return {
        left: Math.round(r.left), right: Math.round(r.right),
        paneLeft: Math.round(pane.left), paneRight: Math.round(pane.right),
        triggerRight: Math.round(t.right),
      }
    })

    // Exactly two placements are correct, and the bug was neither: anchored to
    // the button's right edge, or filling the pane where the button sits too
    // far left for that to fit. What it did instead was keep the anchored WIDTH
    // while the clamp moved it to the pane's left edge — leaving a dead column
    // on the other side that reads as a misplaced overlay.
    const anchored = Math.abs(m.right - m.triggerRight) <= 2
    const filled = (m.left - m.paneLeft) <= 12 && (m.paneRight - m.right) <= 12
    expect(
      anchored || filled,
      `agents popover is neither anchored to its button (right ${m.right} vs ${m.triggerRight}) `
        + `nor filling the pane (${m.left}-${m.right} in ${m.paneLeft}-${m.paneRight})`
    ).toBe(true)
  })

  test('every mode tab in the compact strip can be scrolled fully into view', async ({ page, viewport }) => {
    test.skip(phoneOnly(viewport), 'below-lg-only assertion')
    await page.goto(`/trace/sessions/${BASELINE_TRACE}`)
    await settle(page)

    await page.locator('[data-testid="trace-compact-bar"]').waitFor({ timeout: 15_000 })
    const m = await page.evaluate(() => {
      const rail = document.querySelector('[data-testid="trace-compact-modes"]')
      if (!rail) return null
      rail.scrollLeft = rail.scrollWidth
      const last = rail.lastElementChild.getBoundingClientRect()
      const box = rail.getBoundingClientRect()
      return { lastRight: Math.round(last.right), railRight: Math.round(box.right) }
    })
    expect(m, 'compact strip mode rail not rendered').not.toBeNull()
    expect(
      m.lastRight - m.railRight,
      `last mode tab is clipped by ${m.lastRight - m.railRight}px at the rail's right edge`
    ).toBeLessThanOrEqual(1)
  })
})

/**
 * The follow-up composer is the one piece of trace chrome that is a form, and
 * it is the piece a phone squeezes hardest: an action row of six controls and
 * a meta header of four, both laid out as single-line flex rows.
 */
test.describe('Trace conversation — follow-up composer on a phone', () => {
  test.afterEach(async ({ page }) => {
    await page.unrouteAll({ behavior: 'ignoreErrors' })
  })

  // A steerable session is a server fact (`bridge_reachable` + no `ended_at`),
  // so the composer only exists when the map says so — seed one rather than
  // hoping the scratch DB happens to hold a live session.
  async function seedSteerable(page) {
    const traceId = `e2e-mobile-composer-${Date.now()}`
    const at = i => `2026-05-08T12:00:${String(i).padStart(2, '0')}`
    const spans = [
      {
        trace_id: traceId, span_id: `p-${traceId}`, parent_id: null, name: 'prompt',
        start_time: at(0), attributes: { text: 'Add token-bucket rate limiting.', is_test: true },
      },
      {
        trace_id: traceId, span_id: `a-${traceId}`, parent_id: `p-${traceId}`,
        name: 'assistant_response', start_time: at(1), duration_ms: 2200,
        attributes: { text: 'Here is where the limiter should hook in.', is_test: true },
      },
    ]
    // Seeded over the API, with no page visit first: a `goto` whose SPA route
    // is still resolving cancels the next `goto`, and that race — not any
    // layout fact — is what this spec kept failing on.
    const { token } = await (await page.request.post('/api/auth/login', { data: TEST_USER })).json()
    expect((await page.request.post('/api/session-spans', {
      headers: { Authorization: `Bearer ${token}` }, data: spans,
    })).ok()).toBeTruthy()

    await page.route(`**/api/sessions/${traceId}/map*`, async (route) => {
      const res = await route.fetch()
      const body = await res.json()
      body.ended_at = null
      body.bridge_reachable = true
      body.model = 'claude-opus-4-6-20260514'
      body.context_pct = 28
      await route.fulfill({ response: res, json: body })
    })
    await page.goto(`/trace/sessions/${traceId}?view=conversation`)
    await expect(page.locator('.event-spine:not(.event-spine-row)')).toBeVisible({ timeout: 20_000 })
    await page.getByTestId('followup-pill').click()
    await expect(page.locator('.followup-panel')).toBeVisible()
    return traceId
  }

  /** `.composer-run` vs Send, in whatever phase the row is currently in. */
  async function runRowMetrics(page) {
    return page.evaluate(() => {
      const run = document.querySelector('.composer-run')
      const s = document.querySelector('[data-testid="followup-send"]').getBoundingClientRect()
      const hits = [...run.querySelectorAll('button')]
        .map(el => ({ txt: el.textContent.trim(), r: el.getBoundingClientRect() }))
        // Both axes: the row is allowed to WRAP (Send drops to its own line and
        // the x-ranges legitimately overlap) — what it is never allowed to do
        // is paint one control on top of another.
        .filter(({ r }) => r.left < s.right && s.left < r.right && r.top < s.bottom && s.top < r.bottom)
        .map(({ txt, r }) => `${txt}@${Math.round(r.left)}-${Math.round(r.right)}`)
      const panel = document.querySelector('.followup-panel')
      return {
        scrollWidth: run.scrollWidth,
        clientWidth: run.clientWidth,
        panelOverflow: panel.scrollWidth - panel.clientWidth,
        // How far the row's own right edge runs past the panel's. A row that
        // grows *past* Send and off the panel entirely triggers neither the
        // self-overflow check nor the intersection one — both read clean while
        // the text is unreadable, so the escape needs its own assertion.
        pastPanel: Math.round(run.getBoundingClientRect().right - panel.getBoundingClientRect().right),
        text: run.textContent.trim().slice(0, 40),
        hits,
      }
    })
  }

  function expectRowFits(m, phase) {
    // A flex child that shrinks below its content does not overflow the PANE —
    // it overflows itself, and paints straight over its next sibling.
    expect(
      m.scrollWidth,
      `[${phase}] .composer-run overflows its own box (${m.scrollWidth} > ${m.clientWidth}): "${m.text}"`
    ).toBeLessThanOrEqual(m.clientWidth + 1)
    expect(m.hits, `[${phase}] run controls painted under Send: ${m.hits.join(', ')}`).toEqual([])
    expect(
      m.panelOverflow,
      `[${phase}] the composer panel itself overflows by ${m.panelOverflow}px: "${m.text}"`
    ).toBeLessThanOrEqual(1)
    expect(
      m.pastPanel,
      `[${phase}] the run row runs ${m.pastPanel}px past the panel's right edge: "${m.text}"`
    ).toBeLessThanOrEqual(0)
  }

  test('the run controls fit their own box and never reach under Send', async ({ page, viewport }) => {
    test.skip(!viewport || viewport.width >= 768, 'phone-only assertion')
    test.setTimeout(90_000)
    await seedSteerable(page)

    expectRowFits(await runRowMetrics(page), 'ready')

    // The labels are dropped for width at this breakpoint, so the glyphs are
    // all that is left on screen — the accessible name is the only thing
    // still naming them, and no other project ever renders this form.
    for (const id of ['composer-cancel', 'composer-stop-arm']) {
      const btn = page.getByTestId(id)
      await expect(btn).toHaveAttribute('aria-label', /\S/)
      await expect(btn.locator('.composer-run-label')).toBeHidden()
    }
  })

  // The `ready` phase holds two short buttons; every other phase holds a
  // server sentence, and that is the one that can blow the panel open — an
  // unshrinkable box around text with no width bound can never ellipsize.
  test('a refusal message ellipsizes instead of widening the composer', async ({ page, viewport }) => {
    test.skip(!viewport || viewport.width >= 768, 'phone-only assertion')
    test.setTimeout(90_000)
    const traceId = await seedSteerable(page)

    await page.getByTestId('composer-stop-arm').click()
    await expect(page.getByTestId('composer-stop-confirm')).toBeVisible()
    expectRowFits(await runRowMetrics(page), 'arming')

    await page.getByTestId('composer-stop-keep').click()
    await page.route(`**/api/sessions/${traceId}/bridge-interrupt`, route => route.fulfill({
      json: {
        delivered: false,
        detail: 'no reachable bridge pane for this session; tmux target regin-agent-%14 '
          + 'did not answer and the registry has no other pane for it',
      },
    }))
    await page.getByTestId('composer-cancel').click()
    await expect(page.getByTestId('composer-run-detail')).toBeVisible()
    expectRowFits(await runRowMetrics(page), 'failed')
  })

  // The open panel is a wall across the bottom of the feed, and three controls
  // are anchored to the viewport right there. It publishes `--trace-dock-h` for
  // them to clear; nothing else asserts that global exists or is honoured.
  test('nothing floating is painted over an open composer', async ({ page, viewport }) => {
    test.skip(!viewport || viewport.width >= 768, 'phone-only assertion')
    test.setTimeout(90_000)
    await seedSteerable(page)

    // Polled, not sampled once. The panel settles over a frame or two (the
    // textarea sizes itself, the band is published from a ResizeObserver), and
    // a single read under load catches an intermediate layout — which is a
    // race in the test, not an overlap in the product. The invariant is what
    // converges, so the invariant is what to wait on.
    const overlaps = () => page.evaluate(() => {
      const panel = document.querySelector('.followup-panel')?.getBoundingClientRect()
      if (!panel) return ['no panel']
      const hits = []
      for (const el of document.querySelectorAll('*')) {
        if (getComputedStyle(el).position !== 'fixed') continue
        const r = el.getBoundingClientRect()
        if (r.width < 4 || r.height < 4) continue
        if (r.left < panel.right && panel.left < r.right && r.top < panel.bottom && panel.top < r.bottom) {
          hits.push(el.getAttribute('data-testid') || el.className.toString().split(/\s+/)[0] || el.tagName)
        }
      }
      return hits
    })

    await expect.poll(overlaps, {
      message: 'something floating stayed on top of the open composer',
      timeout: 10_000,
    }).toEqual([])

    // …and the band it cleared by is the one the dock published, not luck.
    const band = await page.evaluate(() => getComputedStyle(document.documentElement)
      .getPropertyValue('--trace-dock-h').trim())
    expect(band, 'the open dock published no band for the floats to clear').toMatch(/^\d+px$/)
  })

  test('the composer meta header stays on one line', async ({ page, viewport }) => {
    test.skip(!viewport || viewport.width >= 768, 'phone-only assertion')
    test.setTimeout(90_000)
    await seedSteerable(page)

    const m = await page.evaluate(() => {
      const head = document.querySelector('.followup-panel > div:first-child')
      const kids = [...head.children].map(c => Math.round(c.getBoundingClientRect().height))
      return { height: Math.round(head.getBoundingClientRect().height), kids }
    })
    // Two wrapped lines read as a collision between "Continue session", the
    // turn/ctx meta and the dismiss ×, all of which are single-line by intent.
    // One line of 11px text plus the row's 10px top padding and the dismiss
    // button's 24px box: 34px. Two lines is 46px, so 36 separates them cleanly.
    expect(m.height, `composer header is ${m.height}px — it has wrapped to two lines`)
      .toBeLessThanOrEqual(36)
  })
})
