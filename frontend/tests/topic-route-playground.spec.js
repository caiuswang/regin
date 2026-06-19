/**
 * Topic-route playground: probe a query, see the keyword route + per-topic
 * exemplar lean, and stamp 👍/👎 to build a topic exemplar case. Seeds a
 * negative exemplar for a real authoritative topic via the API (warm-embedder
 * write path), then asserts the panel surfaces it as a suppressing candidate.
 */
import { test, expect } from './auth-fixture.js'

const API_BASE = 'http://localhost:8321'
const TOPIC = 'memory-recall-pipeline'
const QUERY = 'debug the memory recall ranking pipeline'

test('playground previews a topic route and its exemplar lean', async ({ page }) => {
  const token = await page.request.post(`${API_BASE}/api/auth/login`, {
    data: { username: 'claude-admin', password: 'claude-admin-2026' },
  }).then(r => r.json()).then(d => d.token)
  const headers = { Authorization: `Bearer ${token}` }

  // Seed a negative exemplar for a real topic on this exact query.
  const seed = await page.request.post(`${API_BASE}/api/memory/exemplars`, {
    headers,
    data: { topic_id: TOPIC, query: QUERY, polarity: 'negative' },
  }).then(r => r.json())
  expect(seed.written).toBeGreaterThan(0)

  await page.goto('/memory')
  // The playground lives under the Topics tab since the view was split into
  // Memories / Topics / Recall tabs.
  await page.getByRole('tab', { name: 'Topics' }).click()
  const panel = page.locator('section', { hasText: 'Topic-route playground' })
  await expect(panel).toBeVisible()

  await panel.getByPlaceholder(/a prompt/).fill(QUERY)
  await panel.getByRole('button', { name: 'Preview route' }).click()

  // The routed banner now explains *why* it routed: the matched strategy and
  // the keywords that drove the keyword route (not the pos/neg exemplar scores).
  await expect(panel.getByText('fuzzy keyword overlap')).toBeVisible()
  await expect(panel.getByText(/^\s*matched/)).toBeVisible()

  // The routed banner reports the keyword route, and the seeded negative
  // exemplar pushes the candidate row to a `suppressed` state.
  const row = panel.locator('tr', { hasText: 'Memory recall pipeline' })
  await expect(row).toBeVisible()
  await expect(row.getByText('suppressed')).toBeVisible()

  // Drill into the case: the stored query text is visible and revertable.
  await row.getByRole('button', { name: /Memory recall pipeline/ }).click()
  const cases = panel.locator('li', { hasText: QUERY })
  await expect(cases).toHaveCount(1)
  await expect(cases.getByText('suppress', { exact: true })).toBeVisible()

  await page.screenshot({ path: 'topic-route-playground.png', fullPage: true })

  // Revert that single case via the delete (✕ icon) button; the candidate's
  // suppressed state clears once its only negative is gone.
  await cases.getByRole('button', { name: 'Revert this case (delete)' }).click()
  await expect(panel.locator('li', { hasText: QUERY })).toHaveCount(0)

  // Don't leave any fabricated case in the live memory DB.
  await page.request.delete(`${API_BASE}/api/memory/exemplars`, {
    headers, data: { topic_id: TOPIC },
  })
})
