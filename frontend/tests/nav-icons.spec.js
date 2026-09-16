/**
 * Every sidebar nav link draws a glyph (defect #144).
 *
 * `NavIcon` renders `NAV_ICONS[name] || []`, so a nav entry naming a glyph the
 * registry does not hold is not an error — it is a silent, empty 18px svg. The
 * Defects link shipped that way for its whole life: `icon: 'alert-triangle'`
 * is a name from `ui/Icon.vue`, a different registry, and the rail rendered a
 * blank box beside the label.
 *
 * Asserted against the rendered DOM rather than by re-parsing the two source
 * files, because the failure is visual and the render is the only place both
 * registries meet.
 */
import { test, expect } from './auth-fixture.js'

test('every sidebar nav link renders a glyph with at least one path',
  async ({ page }) => {
    await page.goto('/tasks')
    await page.waitForSelector('.app-shell')

    const links = await page.$$eval('.sb-item', (items) =>
      items.map((item) => ({
        label: (item.getAttribute('aria-label') || item.innerText || '').trim(),
        paths: item.querySelectorAll('.nav-icon path').length,
      })))

    expect(links.length).toBeGreaterThan(5)
    const blank = links.filter((link) => link.paths === 0)
    expect(blank, `nav links rendering an empty glyph: ${
      blank.map((b) => b.label).join(', ')}`).toEqual([])
  })

test('the Pipeline link is one of them', async ({ page }) => {
  await page.goto('/tasks')
  const icon = page.locator('.sb-item', { hasText: 'Pipeline' })
    .locator('.nav-icon path')
  await expect(icon.first()).toBeAttached()
})
