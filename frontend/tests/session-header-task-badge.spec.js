/**
 * The trace header's tasks badge (CAI-46).
 *
 * A subagent keeps its own task list in the same trace and that list is never
 * retired when the subagent finishes, so a session-wide roll-up reported an
 * hours-old plan as current work — the header read `11/12` while the model's
 * own terminal showed 5. The badge now counts the MAIN agent's list; every
 * agent's list stays in the expanded surface, under a heading carrying its own
 * count, so the badge maps onto a section instead of contradicting the list it
 * opens.
 */
import { randomUUID } from 'node:crypto'
import { test, expect } from './auth-fixture.js'

async function seed(page, spans) {
  await page.goto('/trace/sessions')
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect(token).toBeTruthy()
  expect((await page.request.post('/api/session-spans', {
    headers: { Authorization: `Bearer ${token}` }, data: spans,
  })).ok()).toBeTruthy()
}

function fixture(traceId) {
  const sfx = traceId.slice(0, 8)
  const at = i => `2026-05-08T14:0${i}:00`
  const snap = (n, i, todos, agent) => ({
    trace_id: traceId, span_id: `${n}-${sfx}`, parent_id: null, name: 'tool.TodoList',
    start_time: at(i),
    attributes: {
      tool_name: 'TodoList', is_test: true,
      todos: todos.map(([subject, status]) => ({ subject, status })),
      ...(agent ? { agent_id: agent } : {}),
    },
  })
  return { sfx, at, snap }
}

test('badge counts the main agent; the expanded list keeps every agent, sectioned', async ({ page }) => {
  const traceId = randomUUID()
  const { sfx, at, snap } = fixture(traceId)
  await seed(page, [
    { trace_id: traceId, span_id: `p-${sfx}`, parent_id: null, name: 'prompt',
      start_time: at(0), attributes: { text: 'badge scope', is_test: true } },
    snap('s1', 1, [['Sub 1', 'completed'], ['Sub 2', 'completed'],
      ['Sub 3', 'completed'], ['Sub 4', 'completed']], 'ag1'),
    snap('m1', 2, [['Main 1', 'completed'], ['Main 2', 'in_progress']]),
  ])

  await page.goto(`/trace/sessions/${traceId}`)
  const badge = page.locator('[data-testid="trace-tasks-badge"]')
  await expect(badge).toBeVisible({ timeout: 10_000 })
  // 1/2 — the main agent's list. 5/6 would be the session-wide roll-up.
  await expect(badge).toContainText('1/2')
  await expect(badge).toHaveAttribute('title', /main agent's list/)

  await badge.click()
  const list = page.locator('[data-testid="trace-task-list"]')
  await expect(list).toBeVisible()
  // Nothing is hidden: all six rows survive, under one heading per agent.
  await expect(list.locator('li')).toHaveCount(6)
  const sections = page.locator('[data-testid="trace-task-section"]')
  await expect(sections).toHaveCount(2)
  await expect(sections.first()).toContainText('main agent')
  await expect(sections.first()).toContainText('1/2')
  await expect(sections.first()).toContainText('counted in the badge')
  await expect(sections.nth(1)).toContainText('4/4')
})

test('a single-agent session gets no section headings', async ({ page }) => {
  const traceId = randomUUID()
  const { sfx, at, snap } = fixture(traceId)
  await seed(page, [
    { trace_id: traceId, span_id: `p-${sfx}`, parent_id: null, name: 'prompt',
      start_time: at(0), attributes: { text: 'one agent', is_test: true } },
    snap('m1', 1, [['Main 1', 'completed'], ['Main 2', 'pending']]),
  ])

  await page.goto(`/trace/sessions/${traceId}`)
  const badge = page.locator('[data-testid="trace-tasks-badge"]')
  await expect(badge).toBeVisible({ timeout: 10_000 })
  await expect(badge).toContainText('1/2')
  await badge.click()
  await expect(page.locator('[data-testid="trace-task-list"]').locator('li')).toHaveCount(2)
  await expect(page.locator('[data-testid="trace-task-section"]')).toHaveCount(0)
})

test('badge falls back to the subagent list when the main agent wrote none', async ({ page }) => {
  const traceId = randomUUID()
  const { sfx, at, snap } = fixture(traceId)
  await seed(page, [
    { trace_id: traceId, span_id: `p-${sfx}`, parent_id: null, name: 'prompt',
      start_time: at(0), attributes: { text: 'subagent only', is_test: true } },
    snap('s1', 1, [['Only 1', 'completed'], ['Only 2', 'pending']], 'ag1'),
  ])

  await page.goto(`/trace/sessions/${traceId}`)
  const badge = page.locator('[data-testid="trace-tasks-badge"]')
  await expect(badge).toBeVisible({ timeout: 10_000 })
  await expect(badge).toContainText('1/2')
  await badge.click()
  await expect(page.locator('[data-testid="trace-task-list"]').locator('li')).toHaveCount(2)
})

// Two ways the fallback used to leave the badge unexplained: its number matched
// no section, and the roster's placeholder `agent_type` made every subagent
// heading read "agent".
test('two subagent lists get distinct headings and an all-agents total', async ({ page }) => {
  const traceId = randomUUID()
  const { sfx, at, snap } = fixture(traceId)
  await seed(page, [
    { trace_id: traceId, span_id: `p-${sfx}`, parent_id: null, name: 'prompt',
      start_time: at(0), attributes: { text: 'two subagents', is_test: true } },
    snap('s1', 1, [['A one', 'completed']], 'ag1'),
    snap('s2', 2, [['B one', 'in_progress'], ['B two', 'pending']], 'ag2'),
  ])

  await page.goto(`/trace/sessions/${traceId}`)
  const badge = page.locator('[data-testid="trace-tasks-badge"]')
  await expect(badge).toBeVisible({ timeout: 10_000 })
  await expect(badge).toContainText('1/3')
  await expect(badge).toHaveAttribute('title', /all agents/)
  await badge.click()
  // The badge's number is on screen as an explicit total, not orphaned.
  const total = page.locator('[data-testid="trace-task-total"]')
  await expect(total).toContainText('all agents')
  await expect(total).toContainText('1/3')
  const sections = page.locator('[data-testid="trace-task-section"]')
  await expect(sections).toHaveCount(2)
  const labels = await sections.evaluateAll(
    ns => ns.map(n => n.querySelector('span').textContent.trim()))
  expect(new Set(labels).size, `headings collide: ${labels}`).toBe(2)
})
