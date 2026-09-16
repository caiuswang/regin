import { test, expect } from './auth-fixture.js'

/**
 * Nav glyph identity (AppLayout.vue's navGroups + NavIcon.vue's registry).
 *
 * Two links sharing an `icon` key is invisible in review — the registry lookup
 * succeeds and the rail just renders the same picture twice, which is how
 * /features shipped wearing the Tasks glyph. These read the drawn paths rather
 * than the key, so a copied glyph body fails the same way a copied key does.
 */

// NavIcon renders `NAV_ICONS[name] || []`, so an unregistered name is an empty
// svg, not an error: the signature of a typo'd key is zero paths.
async function navSignatures(page, itemSelector) {
  const items = page.locator(itemSelector)
  await expect(items.first()).toBeVisible()
  return items.evaluateAll((els) =>
    els.map((el) => ({
      label: el.textContent.trim(),
      paths: Array.from(el.querySelectorAll('svg.nav-icon path'))
        .map((p) => p.getAttribute('d'))
        .join('|'),
    })),
  )
}

function duplicates(sigs) {
  const seen = new Map()
  const dupes = []
  for (const s of sigs) {
    if (seen.has(s.paths)) dupes.push(`${seen.get(s.paths)} / ${s.label}`)
    else seen.set(s.paths, s.label)
  }
  return dupes
}

test.describe('Sidebar nav glyphs', () => {
  test('no two rail items draw the same icon', async ({ page }) => {
    await page.goto('/')
    const sigs = await navSignatures(page, 'nav.sb-nav .sb-item')
    expect(sigs.length).toBeGreaterThan(5)
    expect(duplicates(sigs)).toEqual([])
  })

  test('every rail item draws a registered icon', async ({ page }) => {
    await page.goto('/')
    const sigs = await navSignatures(page, 'nav.sb-nav .sb-item')
    expect(sigs.filter((s) => s.paths === '')).toEqual([])
  })

  test('no two drawer links draw the same icon', async ({ page }) => {
    await page.setViewportSize({ width: 480, height: 900 })
    await page.goto('/')
    await page.getByRole('button', { name: /menu|navigation/i }).first().click()
    const sigs = await navSignatures(page, 'nav.drawer-nav .drawer-link')
    expect(sigs.length).toBeGreaterThan(5)
    expect(duplicates(sigs)).toEqual([])
  })
})
