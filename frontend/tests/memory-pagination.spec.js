/**
 * Verifies the client-side search + pagination added to the Memory page's
 * Topics and Recall sub-tabs (MemoryTopics, MemoryTopicFeedback, MemoryExemplars).
 * Each endpoint returns a bounded set with no offset/`q`, so the controls live
 * in useClientPage. We mock large datasets and assert the pager + filter behave.
 */
import { test, expect } from './auth-fixture'

function topics(n) {
  return Array.from({ length: n }, (_, i) => ({
    id: `t${i}`, name: `Cluster ${i} ${i === 3 ? 'needle' : 'haystack'}`,
    summary: `summary ${i}`, member_count: i,
  }))
}
function summary(n) {
  return Array.from({ length: n }, (_, i) => ({
    topic_id: `sumtopic-${i}${i === 5 ? '-needle' : ''}`, scored: i, fails: 0,
    fail_rate: 0, status: 'routing',
  }))
}
function recent(n) {
  return Array.from({ length: n }, (_, i) => ({
    session_id: `s${i}`, topic_id: `rectopic-${i}`,
    query: i === 7 ? 'needle query' : `query ${i}`,
    relevance: '', injected_at: '2026-06-20T00:00:00',
  }))
}
function exemplars(n) {
  return Array.from({ length: n }, (_, i) => ({
    memory_id: `m${i}`, title: `Memory ${i} ${i === 4 ? 'needle' : ''}`,
    kind: 'lesson', pos_count: 1, neg_count: 0, last_created: '2026-06-20T00:00:00',
  }))
}

test('Topics & Recall tabs paginate and filter large tables', async ({ page }) => {
  await page.route('**/api/memory/topics', (r) =>
    r.fulfill({ json: { topics: topics(40) } }))
  await page.route('**/api/memory/topic-feedback*', (r) =>
    r.fulfill({ json: { summary: summary(40), recent: recent(40) } }))
  await page.route('**/api/memory/exemplars*', (r) =>
    r.fulfill({ json: { neg_weight: 1, pos_weight: 1, summary: exemplars(40) } }))
  // Memories-tab list endpoint — keep it empty so the page mounts cleanly.
  await page.route('**/api/memory?*', (r) =>
    r.fulfill({ json: { items: [], pagination: { total: 0, page: 0, size: 50 }, stats: {} } }))

  await page.goto('/memory?tab=topics')

  // --- Topics grid: 24/page, so page 1 shows 24 of 40 cards. ---
  await expect(page.getByText('Cluster 0 haystack')).toBeVisible()
  const pagers = page.locator('text=/\\d+–\\d+ of 40/')
  await expect(pagers.first()).toBeVisible()

  // Filter the clusters grid down to the single "needle" card.
  const clusterFilter = page.getByPlaceholder('Filter clusters…')
  await clusterFilter.fill('needle')
  await expect(page.getByText('Cluster 3 needle')).toBeVisible()
  await expect(page.getByText('Cluster 0 haystack')).toHaveCount(0)
  await clusterFilter.fill('')

  // --- Topic feedback summary table: 15/page over 40 rows. ---
  await expect(page.locator('tr', { hasText: 'sumtopic-0' }).first()).toBeVisible()
  const topicFilter = page.getByPlaceholder('Filter topics…')
  await topicFilter.fill('needle')
  await expect(page.locator('tr', { hasText: 'sumtopic-5-needle' })).toBeVisible()
  await expect(page.locator('tr', { hasText: 'sumtopic-0' })).toHaveCount(0)

  // --- Recent injections table (open the accordion) filters independently. ---
  await page.getByText('Recent injections').click()
  const injFilter = page.getByPlaceholder('Filter injections…')
  await injFilter.fill('needle query')
  await expect(page.locator('tr', { hasText: 'rectopic-7' })).toBeVisible()
  await expect(page.locator('tr', { hasText: 'rectopic-0' })).toHaveCount(0)

  // --- Recall tab exemplars table. ---
  await page.goto('/memory?tab=recall')
  await expect(page.locator('tr', { hasText: 'Memory 0' }).first()).toBeVisible()
  const memFilter = page.getByPlaceholder('Filter memories…')
  await memFilter.fill('needle')
  await expect(page.locator('tr', { hasText: 'Memory 4 needle' })).toBeVisible()
  await expect(page.locator('tr', { hasText: 'Memory 0' })).toHaveCount(0)
})
