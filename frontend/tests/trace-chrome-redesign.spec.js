/**
 * Trace-view chrome: the collapsible conversation TOC rail and the follow-up
 * composer docked to the feed bottom.
 *
 * Covers the three acceptance items that have no other in-tree coverage:
 *  1. no horizontal overflow at desktop widths in all four view modes —
 *     `responsive.spec.js` owns the mobile/tablet projects, and its overflow
 *     sweep does not visit the 1120px fold threshold this feature introduces.
 *  4. the sub-1120px auto-fold never rewrites the stored rail preference,
 *     by either route: the chevron is disabled, and `t` is guarded. An
 *     unguarded `t` wrote the key with nothing on screen to show for it,
 *     destroying the choice the user had made at a wide viewport.
 *  6. the composer renders only for a session that is live AND bridgeable.
 */
import { test, expect } from './auth-fixture.js'
import { contentOverflow } from './helpers/overflow.js'
import { BASELINE_TRACE, HEAVY_TRACE } from './helpers/fixtures.js'
import { selectViewMode, waitForViewModes } from './helpers/viewMode.js'

const COLLAPSED_KEY = 'regin_trace_rail_collapsed'
// Between the rail's own `lg` (1024px) visibility floor and the 1120px fold
// threshold: the only band where the rail is both rendered AND auto-folded.
const FOLDED_WIDTH = 1080
const VIEW_MODES = ['Conversation', 'Timeline', 'Log', 'Terminal', 'Messages']

async function openTrace(page, traceId = BASELINE_TRACE) {
  await page.goto(`/trace/sessions/${traceId}`)
  await waitForViewModes(page)
}

// Seed the preference before the app boots: the composable reads localStorage
// at module scope, so setting it after navigation would not be observed until
// a reload.
async function withStoredCollapse(page, value) {
  await page.addInitScript(
    ([k, v]) => window.localStorage.setItem(k, v), [COLLAPSED_KEY, value],
  )
}

const storedCollapse = (page) =>
  page.evaluate((k) => window.localStorage.getItem(k), COLLAPSED_KEY)

// `.content-scroll`, never documentElement: AppLayout sets overflow-x:hidden
// on `.content` above it, so the document measures clean even on a build that
// overflows.
async function expectNoOverflow(page, label) {
  const o = await contentOverflow(page)
  expect(o.pane, 'the .content-scroll pane must exist').toBe(true)
  expect(
    o.scrollWidth,
    `${label} overflows by ${o.scrollWidth - o.clientWidth}px `
    + `— offenders: ${o.offenders.join(', ') || 'none identified'}`,
  ).toBeLessThanOrEqual(o.clientWidth + 1)
}

// Selecting a span is what reveals the detail rail (`detailRailShown` keys off
// `selectedSpan`), and the rail is the third column — without it the widest
// layout, and the 340/296px rules this feature adds, never render.
async function selectFirstSpan(page) {
  await selectViewMode(page, 'Timeline')
  const row = page.locator('[data-testid="spine-row"]').first()
  await expect(row).toBeVisible({ timeout: 10_000 })
  await row.click()
}

test.describe('Trace chrome — no horizontal overflow at desktop widths', () => {
  // 1080 sits in the 1024–1119 band: the rail is rendered but width-folded,
  // and the detail rail is at its 296px form. Neither bound is visited by the
  // three widths the acceptance criteria name.
  for (const width of [924, 1080, 1280, 1440]) {
    test(`${width}px, all four view modes`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 })
      await openTrace(page)

      for (const mode of VIEW_MODES) {
        await selectViewMode(page, mode)
        await page.waitForTimeout(150)
        await expectNoOverflow(page, `${mode} at ${width}px`)
      }
    })

    test(`${width}px, three-column layout with the detail rail open`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 })
      await openTrace(page)
      await selectFirstSpan(page)

      const rail = page.locator('.trace-detail-rail')
      if (width >= 1024) {
        // `v-if="isLgUp && detailRailShown"` — below lg the rail is a sheet.
        await expect(rail).toBeVisible()
        expect(
          (await rail.boundingBox()).width,
          `detail rail at ${width}px should be ${width < 1120 ? 296 : 340}px`,
        ).toBe(width < 1120 ? 296 : 340)
      }
      await expectNoOverflow(page, `three-column at ${width}px`)
    })
  }
})

test.describe('Trace chrome — auto-fold never rewrites the rail preference', () => {
  test('narrowing folds the rail without touching the stored choice', async ({ page }) => {
    await withStoredCollapse(page, '0')
    await page.setViewportSize({ width: 1440, height: 900 })
    await openTrace(page)

    const rail = page.locator('[data-testid="conversation-toc"]')
    const widthOf = async () => (await rail.boundingBox()).width
    expect(await widthOf()).toBeGreaterThan(62)

    await page.setViewportSize({ width: FOLDED_WIDTH, height: 900 })
    await expect.poll(widthOf).toBe(62)
    expect(await storedCollapse(page)).toBe('0')

    // Widening restores the user's own (expanded) choice.
    await page.setViewportSize({ width: 1440, height: 900 })
    await expect.poll(widthOf).toBeGreaterThan(62)
    expect(await storedCollapse(page)).toBe('0')
  })

  test('`t` is inert while the viewport is forcing the mini form', async ({ page }) => {
    await withStoredCollapse(page, '0')
    await page.setViewportSize({ width: FOLDED_WIDTH, height: 900 })
    await openTrace(page)

    await expect(page.locator('[data-testid="toc-rail-toggle"]')).toBeDisabled()

    await page.locator('body').press('t')
    await page.waitForTimeout(100)
    expect(
      await storedCollapse(page),
      '`t` under the auto-fold must not write the preference — the rail is '
      + 'already mini, so the write would be invisible',
    ).toBe('0')

    // And the choice survives back at a wide viewport.
    await page.setViewportSize({ width: 1440, height: 900 })
    await expect.poll(
      async () => (await page.locator('[data-testid="conversation-toc"]').boundingBox()).width,
    ).toBeGreaterThan(62)
  })

  // No `withStoredCollapse` here: addInitScript re-runs on every navigation,
  // so seeding the key would rewrite it during the reload under test. An
  // unset key already means "not collapsed".
  test('`t` toggles and persists above the fold threshold', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await openTrace(page)

    const railWidth = async () =>
      (await page.locator('[data-testid="conversation-toc"]').boundingBox()).width

    await page.locator('body').press('t')
    await expect.poll(railWidth).toBe(62)
    expect(await storedCollapse(page)).toBe('1')

    await page.reload()
    await waitForViewModes(page)
    await expect.poll(railWidth).toBe(62)
  })

  test('`t` is inert while typing', async ({ page }) => {
    await withStoredCollapse(page, '0')
    await page.setViewportSize({ width: 1440, height: 900 })
    await openTrace(page)

    await page.evaluate(() => {
      const el = document.createElement('input')
      el.id = 'typing-probe'
      document.body.appendChild(el)
      el.focus()
    })
    await page.locator('#typing-probe').press('t')

    expect(await page.inputValue('#typing-probe')).toBe('t')
    expect(await storedCollapse(page)).toBe('0')
  })
})

// Patch the map response rather than seeding a tmux pane: reachability is a
// property of a live bridge process, which an E2E run has no way to stand up.
async function asLiveBridged(page, traceId = BASELINE_TRACE) {
  await page.route(`**/api/sessions/${traceId}/map*`, async (route) => {
    const res = await route.fetch()
    const body = await res.json()
    body.ended_at = null
    body.bridge_reachable = true
    await route.fulfill({ response: res, json: body })
  })
}

test.describe('Trace chrome — follow-up composer gating', () => {
  test('absent on a finished session', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await openTrace(page)

    await expect(page.locator('[data-testid="followup-composer"]')).toHaveCount(0)
  })

  // The composer is a flex sibling BELOW the transcript's own scroll region,
  // so the requirement is positional and holds at EVERY scroll offset — not a
  // reserved-padding sum, which only ever cleared the extreme bottom. Rects are
  // clipped to the scroll region first: a row half-scrolled past the region's
  // bottom edge is not on screen, and only screen pixels can collide.
  test('no conversation row intersects the composer at any scroll position', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await asLiveBridged(page, HEAVY_TRACE)
    await openTrace(page, HEAVY_TRACE)

    const dock = page.locator('[data-testid="followup-composer"]')
    await expect(dock).toBeVisible({ timeout: 10_000 })

    // Open it: the tall state is the one that would hide content.
    await page.locator('[data-testid="followup-pill"]').click()
    await expect(page.locator('[data-testid="followup-textarea"]')).toBeFocused()

    const scroller = page.locator('[data-trace-scroller]')
    await expect(
      scroller,
      'the transcript must scroll inside the view — an overlay inside the page '
      + 'scroller can only ever be moved, never removed',
    ).toHaveCount(1)

    const box = await scroller.evaluate(el =>
      ({ scroll: el.scrollHeight, client: el.clientHeight }))
    expect(
      box.scroll,
      'the heavy fixture must overflow its region, or scrolling proves nothing',
    ).toBeGreaterThan(box.client + 100)

    for (const frac of [0, 0.5, 1]) {
      await scroller.evaluate((el, f) => {
        el.scrollTop = (el.scrollHeight - el.clientHeight) * f
      }, frac)
      await page.waitForTimeout(150)

      const seen = await page.evaluate(() => {
        const clip = (a, b) => ({
          top: Math.max(a.top, b.top), bottom: Math.min(a.bottom, b.bottom),
          left: Math.max(a.left, b.left), right: Math.min(a.right, b.right),
        })
        const port = document.querySelector('[data-trace-scroller]').getBoundingClientRect()
        const d = document.querySelector('[data-testid="followup-composer"]').getBoundingClientRect()
        const rows = [...document.querySelectorAll(
          '[data-testid="conversation-prompt-label"], .event-spine-row',
        )].map(el => clip(el.getBoundingClientRect(), port))
          .filter(r => r.bottom - r.top > 1 && r.right - r.left > 1)
        const hit = rows.filter(r =>
          r.bottom > d.top && r.top < d.bottom && r.right > d.left && r.left < d.right)
        return { visible: rows.length, overlapping: hit.length }
      })

      expect(seen.visible, `no transcript rows on screen at scroll ${frac}`)
        .toBeGreaterThan(0)
      expect(
        seen.overlapping,
        `${seen.overlapping} conversation rows are under the composer at scroll ${frac}`,
      ).toBe(0)
    }
  })

  test('present once the session reports live and bridgeable', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })

    await asLiveBridged(page)
    await openTrace(page)

    const pill = page.locator('[data-testid="followup-pill"]')
    await expect(pill).toBeVisible({ timeout: 10_000 })

    await pill.click()
    await expect(page.locator('[data-testid="followup-textarea"]')).toBeFocused()

    await page.keyboard.press('Escape')
    await expect(page.locator('[data-testid="followup-textarea"]')).toHaveCount(0)
    await expect(pill).toBeVisible()
  })
})
