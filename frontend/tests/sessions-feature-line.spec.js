/**
 * F152 — a session row reads like a task row: the feature it belongs to as a
 * `#<id> <title>` line above the title, the resolved `display_title` clamped
 * to two lines, and a URL-backed Feature filter (`?feature_id=`).
 *
 * The list is mocked so the shapes are exact; the filter test asserts the
 * request the view actually sends.
 */
import { test, expect } from './auth-fixture.js'

const NOW = '2026-07-10T13:00:00.000000'

function row(traceId, overrides = {}) {
  return {
    trace_id: traceId, title: null, title_source: null,
    display_title: null, display_title_source: null, task: null, features: [],
    status: 'ended', ended_reason: null, is_test: 0,
    started_at: '2026-07-10T11:00:00.000000', last_seen: '2026-07-10T12:00:00.000000',
    span_count: 3, file_edits: 0, tool_calls: 0, skill_reads: 0, rule_checks: 0,
    plans: 0, prompts: 1, agent_type: 'claude', agent_kind: 'claude',
    origin: 'session', category: 'user', is_workflow: false, is_run: false,
    model: null, cwd: null, repos: [], primary_repo: null, is_multi_repo: false,
    context_pct: null, context_pct_all: null, active_work_ms: null,
    active_pct: null, idle_ms: null,
    tags: [{ slug: 'user', source: 'auto', builtin: true }],
    ...overrides,
  }
}

async function mockList(page, rows, seen = []) {
  await page.route('**/api/sessions?*', (route) => {
    seen.push(new URL(route.request().url()))
    return route.fulfill({ json: {
      items: rows, sessions: rows,
      pagination: { next_cursor: null, size: 50, has_next: false },
      tag_counts: { user: rows.length }, builtin_tags: [],
      server_now: NOW, server_now_utc: '2026-07-10T09:00:00.000Z',
    } })
  })
  await page.route('**/api/session-tags', (route) => route.fulfill({ json: { tags: [] } }))
  await page.route('**/api/features/152', (route) =>
    route.fulfill({ json: { id: 152, title: 'Session列表页优化' } }))
}

const LONG = '/feature-pipeline\nfix the session list title logic so that '
  + 'every row says what it was doing, '.repeat(8)

test('feature line above the title, +N hint, no feature tag', async ({ page }) => {
  await mockList(page, [row('feat-row', {
    title: 'Refine', title_source: 'claude_ai_title',
    display_title: 'Refine', display_title_source: 'claude_ai_title',
    features: [
      { id: 152, title: 'Session列表页优化', is_primary: true },
      { id: 7, title: 'Other', is_primary: false },
    ],
  }), row('plain-row', { title: 'hello', title_source: 'first_prompt',
    display_title: 'hello', display_title_source: 'first_prompt' })])
  await page.goto('/trace/sessions')

  const featured = page.locator('.srow', { hasText: 'Refine' })
  const line = featured.getByTestId('session-feature-line')
  await expect(line).toContainText('#152 Session列表页优化')
  await expect(line).toContainText('+1')
  await expect(line).toHaveAttribute('title', '#152 Session列表页优化\n#7 Other')
  const lineBox = await line.boundingBox()
  const titleBox = await featured.locator('.srow__title').boundingBox()
  expect(lineBox.y + lineBox.height).toBeLessThanOrEqual(titleBox.y + 1)
  await expect(featured.locator('.srow__meta')).not.toContainText('feature-152')

  const plain = page.locator('.srow', { hasText: 'hello' })
  await expect(plain.getByTestId('session-feature-line')).toHaveCount(0)
})

test('title collapses newlines and clamps to two lines', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await mockList(page, [row('long-row', {
    title: LONG, title_source: 'first_prompt',
    display_title: LONG, display_title_source: 'first_prompt',
  })])
  await page.goto('/trace/sessions')

  const text = page.locator('.srow .srow__title-text')
  await expect(text).toContainText('/feature-pipeline fix the session list')
  const { height, lineHeight } = await text.evaluate((el) => ({
    height: el.getBoundingClientRect().height,
    lineHeight: parseFloat(getComputedStyle(el).lineHeight),
  }))
  expect(height).toBeGreaterThan(lineHeight * 1.5)
  expect(height).toBeLessThanOrEqual(lineHeight * 2 + 1)
  await expect(page.locator('.srow .srow__title')).toHaveAttribute('title', LONG)
})

test('task title renders its feature ref as words', async ({ page }) => {
  await mockList(page, [row('task-row', {
    title: '/skill', title_source: 'first_prompt',
    display_title: 'Build · [F152 Session列表页优化](feature:152)',
    display_title_source: 'task', task: { id: 452, title: 'x' },
  })])
  await page.goto('/trace/sessions')
  await expect(page.locator('.srow__title-text'))
    .toHaveText('Build · F152 Session列表页优化')
})

test('feature_id in the URL filters the list and shows a clearable chip', async ({ page }) => {
  const seen = []
  await mockList(page, [row('a', { title: 'a', display_title: 'a' })], seen)
  await page.goto('/trace/sessions?feature_id=152')

  await expect.poll(() => seen.some(u => u.searchParams.get('feature_id') === '152')).toBe(true)
  const chip = page.getByText('#152 Session列表页优化', { exact: false }).first()
  await expect(chip).toBeVisible()

  await page.getByRole('button', { name: 'Clear filter: #152 Session列表页优化' }).click()
  await expect(page).not.toHaveURL(/feature_id=/)
  await expect.poll(() => seen.at(-1)?.searchParams.get('feature_id') ?? null).toBe(null)
})
