import { test, expect } from './auth-fixture'

// With a SMALL dataset (below the page size) the pager stays hidden but the
// search box must still be present and functional on both tabs.
test('search shows on small Topics/Recall data; filters work', async ({ page }) => {
  await page.route('**/api/memory/topics', (r) => r.fulfill({ json: { topics: [
    { id: 't0', name: 'Alpha cluster', summary: 's', member_count: 1 },
    { id: 't1', name: 'Beta cluster', summary: 's', member_count: 2 },
  ] } }))
  await page.route('**/api/memory/topic-feedback*', (r) => r.fulfill({ json: {
    summary: [{ topic_id: 'alpha-topic', scored: 1, fails: 0, fail_rate: 0, status: 'routing' }],
    recent: [],
  } }))
  await page.route('**/api/memory/exemplars*', (r) => r.fulfill({ json: {
    neg_weight: 1, pos_weight: 1,
    summary: [
      { memory_id: 'm0', title: 'Alpha memory', kind: 'lesson', pos_count: 1, neg_count: 0, last_created: '2026-06-20T00:00:00' },
      { memory_id: 'm1', title: 'Beta memory', kind: 'lesson', pos_count: 1, neg_count: 0, last_created: '2026-06-20T00:00:00' },
    ],
  } }))
  await page.route('**/api/memory?*', (r) => r.fulfill({ json: { items: [], pagination: { total: 0, page: 0, size: 50 }, stats: {} } }))

  await page.goto('/memory?tab=topics')
  const clusterFilter = page.getByPlaceholder('Filter clusters…')
  await expect(clusterFilter).toBeVisible()          // shown even for 2 rows
  await expect(page.getByPlaceholder('Filter topics…')).toBeVisible()
  await clusterFilter.fill('Beta')
  await expect(page.getByText('Beta cluster')).toBeVisible()
  await expect(page.getByText('Alpha cluster')).toHaveCount(0)

  await page.goto('/memory?tab=recall')
  const memFilter = page.getByPlaceholder('Filter memories…')
  await expect(memFilter).toBeVisible()
  await memFilter.fill('Alpha')
  await expect(page.locator('tr', { hasText: 'Alpha memory' })).toBeVisible()
  await expect(page.locator('tr', { hasText: 'Beta memory' })).toHaveCount(0)
})
