import { test, expect } from './auth-fixture.js'

test.describe('Navigation', () => {
  test('navbar renders logo and search', async ({ page }) => {
    await page.goto('/')
    await expect(page.locator('.sidebar a.sb-brand-name', { hasText: 'regin' })).toBeVisible()
    await expect(page.locator('.sidebar button.sb-search[aria-label="Open quick search"]')).toBeVisible()
  })
})

test.describe('Dashboard', () => {
  test('loads dashboard with stat cards', async ({ page }) => {
    await page.goto('/')
    await expect(page.locator('h1')).toHaveText('Dashboard')
    await expect(page.locator('.stat-card')).not.toHaveCount(0)
  })

  test('shows repositories section', async ({ page }) => {
    await page.goto('/')
    await expect(page.locator('.stat-card', { hasText: 'Repos registered' })).toBeVisible()
  })
})

test.describe('Patterns', () => {
  test('loads pattern list page', async ({ page }) => {
    await page.goto('/patterns')
    await expect(page.locator('h1')).toHaveText('Patterns & Skills')
    await expect(page.getByTestId('library-tab-patterns')).toHaveClass(/is-active/)
  })
})

test.describe('Rules', () => {
  test('loads rules page', async ({ page }) => {
    await page.goto('/rules')
    await expect(page.locator('h1')).toHaveText('Rules')
  })
})

test.describe('Skills', () => {
  test('loads skills page', async ({ page }) => {
    await page.goto('/skills')
    await expect(page.locator('h1')).toHaveText('Patterns & Skills')
    await expect(page.getByTestId('library-tab-skills')).toHaveClass(/is-active/)
    await expect(page.locator('h2.section-heading').first()).toContainText('Global')
  })

  test('the library tab strip switches between the two lists', async ({ page }) => {
    await page.goto('/patterns')
    await expect(page.getByTestId('library-tab-patterns')).toHaveClass(/is-active/)
    await page.getByTestId('library-tab-skills').click()
    await expect(page).toHaveURL(/\/skills$/)
    await expect(page.getByTestId('library-tab-skills')).toHaveClass(/is-active/)
    // The leaving tab's teleported actions must not linger in the shared header.
    await expect(page.getByRole('button', { name: 'New pattern' })).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'New skill' })).toHaveCount(1)
  })
})

// The Library shell is new chrome above two existing lists, so the claim worth
// pinning is that it paints clean in both themes at both widths — not that it
// exists, which the tests above already say.
test.describe('Library shell renders clean', () => {
  for (const path of ['/patterns', '/skills']) {
    for (const theme of ['light', 'dark']) {
      for (const width of [1440, 390]) {
        test(`${path} — ${theme} @ ${width}px`, async ({ page }) => {
          const errors = []
          page.on('console', m => { if (m.type() === 'error') errors.push(m.text()) })
          page.on('pageerror', e => errors.push(String(e)))
          await page.setViewportSize({ width, height: 900 })
          await page.addInitScript(t => localStorage.setItem('regin_theme', t), theme)

          await page.goto(path)
          await expect(page.locator('h1')).toHaveText('Patterns & Skills')
          await expect(page.getByTestId('library-tabs').locator('a')).toHaveCount(2)
          await expect(page.locator('html')).toHaveAttribute('data-theme', theme)
          expect(errors, `${path} console errors: ${errors.join(' | ')}`).toEqual([])
        })
      }
    }
  }
})

test.describe('Tags', () => {
  test('legacy /tags redirects to patterns', async ({ page }) => {
    await page.goto('/tags')
    await expect(page).toHaveURL(/\/patterns$/)
    await expect(page.locator('h1')).toHaveText('Patterns & Skills')
  })
})
