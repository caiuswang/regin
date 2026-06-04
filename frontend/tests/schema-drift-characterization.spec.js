/**
 * Characterization of SchemaDriftView's static page chrome.
 *
 * No existing green spec renders the Schema drift page. This spec pins the
 * page heading and the KPI summary strip — both of which render
 * unconditionally (the heading is a static <h1>, the KPI stat-labels are
 * static text), independent of test-DB data. The drift table itself is behind
 * loading / empty / data branches, so it is deliberately NOT asserted here.
 *
 * Portable: the route only requires auth (no feature flag), provided by the
 * shared fixture.
 */
import { test, expect } from './auth-fixture.js'

test('Schema drift page renders its heading and KPI summary strip', async ({ page }) => {
  await page.goto('/schema-drift')

  // Static <h1> renders before the API resolves.
  await expect(page.getByRole('heading', { name: 'Schema drift' })).toBeVisible({ timeout: 10_000 })

  // The five KPI stat-labels render unconditionally. Scope to the KPI grid's
  // .stat-label cells — "Clean"/"Overlaid" also appear as state-filter chips,
  // which would trip Playwright's strict-mode multiple-match check.
  for (const label of ['Schemas', 'Clean', 'With drift', 'Overlaid', 'Pending findings']) {
    await expect(
      page.locator('.kpi-grid .stat-label', { hasText: label }),
    ).toBeVisible()
  }
})
