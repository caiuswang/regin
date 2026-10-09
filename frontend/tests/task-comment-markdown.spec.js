/**
 * A task comment is an authored document, so it renders as markdown.
 *
 * Same rule as the task body (`task-body-markdown.spec.js`) and the same
 * renderer; the pairing against an agent's verbatim brief
 * (`agent-prompt-verbatim.spec.js`) is what makes it a rule rather than a
 * preference. A resolution note written by an agent arrives with headings, a
 * bold line and a table, and printing that source was the defect.
 */
import { test, expect } from './auth-fixture.js'

const RESOLUTION = [
  '## Resolution',
  '',
  '**产物**: a branch',
  '',
  '| a | b |',
  '| --- | --- |',
  '| 1 | 2 |',
].join('\n')

const TWO_PARAGRAPHS = 'First paragraph.\n\nSecond paragraph.'

function task(overrides = {}) {
  return {
    id: 80, title: 'a commented task', body: null, status: 'idea',
    source: 'web', tags: [], links: [], params: {},
    autorun: false, project_id: null, priority: 'none', priority_rank: 4,
    parent_id: null, assignee: null, assignee_name: null, rounds: 1,
    created_at: '2026-09-14', updated_at: '2026-09-14',
    external_id: null, created_by: null, ...overrides,
  }
}

function thread(id, bodies) {
  return {
    id,
    resolution_state: 'open',
    created_at: '2026-09-14T00:00:00+00:00',
    comments: bodies.map((body, i) => ({
      id: id * 100 + i,
      author_kind: 'agent',
      created_at: '2026-09-14T00:00:00+00:00',
      body,
    })),
  }
}

async function stub(page, threads) {
  await page.route(/\/api\/tasks\/80$/, (route) =>
    route.fulfill({ json: { task: task(), grain: { children: 0, runs: 0 } } }))
  await page.route(/\/api\/tasks\/80\/comments$/, (route) =>
    route.fulfill({ json: { threads } }))
  await page.route(/\/api\/tasks\/80\/links$/, (route) =>
    route.fulfill({ json: { links: [] } }))
}

test('a task comment renders as markdown', async ({ page }) => {
  await stub(page, [thread(1, [RESOLUTION])])
  await page.goto('/tasks/80')

  const body = page.locator('[data-testid="task-thread-1"] .tc-body')
  await expect(body.locator('h2')).toHaveText('Resolution')
  await expect(body.locator('strong')).toHaveText('产物')
  await expect(body.locator('table')).toHaveCount(1)
  await expect(body.locator('table td')).toHaveCount(2)

  await expect(body).not.toContainText('## Resolution')
  await expect(body).not.toContainText('**产物**')
})

test("a comment's block structure survives", async ({ page }) => {
  await stub(page, [thread(1, [TWO_PARAGRAPHS])])
  await page.goto('/tasks/80')

  const body = page.locator('[data-testid="task-thread-1"] .tc-body')
  await expect(body.locator('p')).toHaveCount(2)

  // Two blocks, not one run-together line: the second paragraph starts below
  // the first rather than beside it.
  const first = await body.locator('p').nth(0).boundingBox()
  const second = await body.locator('p').nth(1).boundingBox()
  expect(second.y).toBeGreaterThan(first.y + first.height - 1)
})

test('the composer still edits source', async ({ page }) => {
  await stub(page, [thread(1, [RESOLUTION])])
  await page.goto('/tasks/80')

  const draft = page.getByTestId('task-comment-draft')
  await draft.fill(RESOLUTION)
  await expect(draft).toHaveValue(RESOLUTION)
})
