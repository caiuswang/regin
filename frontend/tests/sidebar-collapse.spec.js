import { test, expect } from './auth-fixture.js'
import { contentOverflow, settle } from './helpers/overflow.js'

/**
 * Collapsible sidebar rail (AppSidebar.vue).
 *
 * The rail has two states and almost every assertion below only fails in one of
 * them, so each case pins the state explicitly rather than trusting the
 * persisted default: `regin_sidebar_collapsed` survives reloads by design.
 */

const RAIL = '.sidebar'
const EXPANDED_W = 248
const COLLAPSED_W = 76

async function railWidth(page) {
  return page.evaluate((sel) => {
    const el = document.querySelector(sel)
    return el ? Math.round(el.getBoundingClientRect().width) : null
  }, RAIL)
}

// The rail animates its width; wait for it to stop moving rather than guessing
// a timeout, so the assertion measures the settled state.
async function settledRailWidth(page) {
  let last = null
  for (let i = 0; i < 20; i++) {
    const w = await railWidth(page)
    if (w !== null && w === last) return w
    last = w
    await page.waitForTimeout(60)
  }
  return last
}

// `evaluateAll` does NOT auto-wait, so every bulk read below has to be gated on
// the rail actually having rendered — otherwise it silently returns [] and the
// assertion passes (or fails) on an empty set.
async function readyItems(page) {
  const items = page.locator('nav.sb-nav .sb-item')
  await expect(items.first()).toBeVisible()
  return items
}

test.describe('Sidebar rail collapse', () => {
  test('starts expanded and toggles to the icon rail and back', async ({ page }) => {
    await page.goto('/')
    await expect(page.locator(RAIL)).toBeVisible()
    expect(await settledRailWidth(page)).toBe(EXPANDED_W)

    await page.getByRole('button', { name: 'Collapse sidebar' }).click()
    expect(await settledRailWidth(page)).toBe(COLLAPSED_W)

    await page.getByRole('button', { name: 'Expand sidebar' }).click()
    expect(await settledRailWidth(page)).toBe(EXPANDED_W)
  })

  test('collapsed state survives a reload', async ({ page }) => {
    await page.goto('/')
    await page.getByRole('button', { name: 'Collapse sidebar' }).click()
    expect(await settledRailWidth(page)).toBe(COLLAPSED_W)

    await page.reload()
    await expect(page.locator(RAIL)).toBeVisible()
    expect(await settledRailWidth(page)).toBe(COLLAPSED_W)
    await expect(page.getByRole('button', { name: 'Expand sidebar' })).toBeVisible()
  })

  test('backslash toggles the rail, but not while typing in a field', async ({ page }) => {
    await page.goto('/')
    expect(await settledRailWidth(page)).toBe(EXPANDED_W)

    await page.locator('body').press('\\')
    expect(await settledRailWidth(page)).toBe(COLLAPSED_W)

    await page.locator('body').press('\\')
    expect(await settledRailWidth(page)).toBe(EXPANDED_W)

    // Typing a literal backslash into the command palette must reach the input,
    // not the rail — this is the case a bare window keydown handler gets wrong.
    await page.locator('.sidebar .sb-search').click()
    const field = page.locator('input:visible').first()
    await expect(field).toBeFocused()
    await field.press('\\')
    expect(await settledRailWidth(page)).toBe(EXPANDED_W)
    await expect(field).toHaveValue('\\')
  })

  test('collapsed rail hides labels but keeps every item named and reachable', async ({ page }) => {
    await page.goto('/')
    const items = await readyItems(page)
    const namesExpanded = await items.evaluateAll(els =>
      els.map(e => e.getAttribute('aria-label')))
    expect(namesExpanded.length).toBeGreaterThan(5)
    await expect(items.filter({ hasText: 'Patterns' })).toBeVisible()

    await page.getByRole('button', { name: 'Collapse sidebar' }).click()
    await settledRailWidth(page)

    // No visible label text survives...
    const labelCount = await page.locator('nav.sb-nav .sb-item-label').count()
    expect(labelCount, 'collapsed rail still renders link label text').toBe(0)

    // ...but the accessible names are unchanged, so the nav is still usable.
    const namesCollapsed = await items.evaluateAll(els =>
      els.map(e => e.getAttribute('aria-label')))
    expect(namesCollapsed).toEqual(namesExpanded)

    // Every item is centered in the 76px rail (icon-only).
    const offsets = await items.evaluateAll(els => els.map((e) => {
      const r = e.getBoundingClientRect()
      const icon = e.querySelector('svg').getBoundingClientRect()
      return Math.round((icon.left - r.left) - (r.right - icon.right))
    }))
    expect(Math.max(...offsets.map(Math.abs)), 'icons not centered in the rail')
      .toBeLessThanOrEqual(1)
  })

  test('a badge renders as a number expanded and a dot collapsed, and never at zero', async ({ page }) => {
    await page.goto('/')
    const items = await readyItems(page)
    // Badge counts arrive from /api later than the nav itself, so settle first
    // or the "expanded pill" read races the fetch and the case goes vacuous.
    await settle(page)
    const badged = await items.evaluateAll(els =>
      els.filter(e => e.querySelector('.sb-badge'))
        .map(e => ({ name: e.getAttribute('aria-label'), text: e.querySelector('.sb-badge').textContent.trim() })))
    test.skip(!badged.length, 'no badged nav link in the dev DB (nothing unread, no pending drift)')

    // Nothing may render an empty or zero pill in either state.
    for (const b of badged) {
      expect(Number(b.text), `badge "${b.text}" rendered at zero`).toBeGreaterThan(0)
      expect(b.name, `badge count missing from accessible name: ${b.name}`).toContain(b.text)
    }
    expect(await page.locator('nav.sb-nav .sb-badge-dot').count(),
      'dot badge leaked into the expanded rail').toBe(0)

    await page.getByRole('button', { name: 'Collapse sidebar' }).click()
    await settledRailWidth(page)

    expect(await page.locator('nav.sb-nav .sb-badge').count(),
      'numeric pill leaked into the collapsed rail').toBe(0)
    expect(await page.locator('nav.sb-nav .sb-badge-dot').count()).toBe(badged.length)
  })

  test('the active route stays marked in both states', async ({ page }) => {
    await page.goto('/patterns')
    await settle(page)
    const active = page.locator('nav.sb-nav .sb-item.is-active')
    await expect(active).toHaveCount(1)
    await expect(active).toHaveAttribute('aria-current', 'page')
    await expect(active).toHaveAttribute('aria-label', 'Patterns')

    await page.getByRole('button', { name: 'Collapse sidebar' }).click()
    await settledRailWidth(page)
    await expect(active).toHaveCount(1)
    await expect(active).toHaveAttribute('aria-current', 'page')
    const bg = await active.evaluate(e => getComputedStyle(e).backgroundImage)
    expect(bg, 'active gradient lost when collapsed').toContain('gradient')
  })

  test('collapsing does not make the content pane scroll sideways', async ({ page }) => {
    const errors = []
    page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })

    await page.goto('/trace/sessions')
    await settle(page)
    await page.getByRole('button', { name: 'Collapse sidebar' }).click()
    await settledRailWidth(page)
    await settle(page)

    const m = await contentOverflow(page)
    expect(
      m.scrollWidth,
      `collapsed rail: content pane overflows; offenders: ${m.offenders.join(', ')}`
    ).toBeLessThanOrEqual(m.clientWidth + 1)
    expect(errors, `console errors: ${errors.join(' | ')}`).toEqual([])
  })

  test('the rail renders from theme tokens in dark mode', async ({ page }) => {
    await page.addInitScript(() => window.localStorage.setItem('regin_theme', 'dark'))
    await page.goto('/')
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')

    // The shell surface must follow the inverted ramp, not a baked-in light hex.
    const bg = await page.locator(RAIL).evaluate(e => getComputedStyle(e).backgroundColor)
    const [r, g, b] = bg.match(/\d+/g).map(Number)
    expect((r + g + b) / 3, `sidebar stayed light in dark mode (${bg})`).toBeLessThan(90)
  })
})
