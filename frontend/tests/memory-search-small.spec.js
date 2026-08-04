import { test, expect } from './auth-fixture'

// With a SMALL dataset (below the page size) the pager stays hidden but the
// search box must still be present and functional.
//
// The Recall half of this test was dropped: that tab is now a query probe, and
// the `Filter memories…` table it asserted no longer exists in the app.
test('search shows on small Topics data; filters work', async ({ page }) => {
  await page.route('**/api/memory/topics', (r) => r.fulfill({ json: { topics: [
    { id: 't0', name: 'Alpha cluster', summary: 's', member_count: 1 },
    { id: 't1', name: 'Beta cluster', summary: 's', member_count: 2 },
  ] } }))
  await page.route('**/api/memory/topic-feedback*', (r) => r.fulfill({ json: {
    summary: [{ topic_id: 'alpha-topic', scored: 1, fails: 0, fail_rate: 0, status: 'routing' }],
    recent: [],
  } }))
  await page.route('**/api/memory?*', (r) => r.fulfill({ json: { items: [], pagination: { total: 0, page: 0, size: 50 }, stats: {} } }))

  await page.goto('/memory?tab=topics')
  const clusterFilter = page.getByPlaceholder('Filter clusters…')
  await expect(clusterFilter).toBeVisible()          // shown even for 2 rows
  // `getByRole('searchbox')`, not `getByPlaceholder`: the Taxonomy panel grew a
  // second input with the same placeholder, so the plain placeholder locator is
  // ambiguous under strict mode. This one is the table filter under test.
  await expect(page.getByRole('searchbox', { name: 'Filter topics…' })).toBeVisible()
  await clusterFilter.fill('Beta')
  await expect(page.getByText('Beta cluster')).toBeVisible()
  await expect(page.getByText('Alpha cluster')).toHaveCount(0)
})
