/**
 * A task body is an authored document, so it renders as markdown.
 *
 * Deliberately the opposite rule to an agent's launch brief
 * (`agent-prompt-verbatim.spec.js`), and the pair is the point: a brief is
 * INPUT text full of paths and fenced source, where a renderer eats every `*`
 * and `#`; a task body is prose somebody wrote, and a feature's spec now lives
 * in it. Two different things, two different rules, one test each.
 */
import { test, expect } from './auth-fixture.js'

// The rich face is the default editor now; these specs drive the source one.
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('regin_editor_mode', 'source')
    localStorage.setItem('regin_editor_mode:compose', 'source')
  })
})

const BODY = [
  '## What this is for',
  '',
  'A spec with **bold**, `code` and a path like `lib/tasks/store.py`.',
  '',
  '* first item',
  '* second item',
].join('\n')

function task(overrides = {}) {
  return {
    id: 80, title: 'a specced feature', body: BODY, status: 'idea',
    source: 'web', tags: [], links: [], params: {},
    autorun: false, project_id: null, priority: 'none', priority_rank: 4,
    parent_id: null, assignee: null, assignee_name: null, rounds: 1,
    created_at: '2026-09-14', updated_at: '2026-09-14',
    external_id: null, created_by: null, ...overrides,
  }
}

async function stub(page, body) {
  await page.route(/\/api\/tasks\/80$/, (route) =>
    route.fulfill({ json: { task: body, grain: { children: 0, runs: 0 } } }))
  await page.route(/\/api\/tasks\/80\/comments$/, (route) =>
    route.fulfill({ json: { threads: [] } }))
  await page.route(/\/api\/tasks\/80\/links$/, (route) =>
    route.fulfill({ json: { links: [] } }))
}

test('a task body renders as markdown, not as literal text',
  async ({ page }) => {
    await stub(page, task())
    await page.goto('/tasks/80')

    const details = page.locator('.tbe-body')
    await expect(details.locator('h2')).toHaveText('What this is for')
    await expect(details.locator('strong')).toHaveText('bold')
    await expect(details.locator('li')).toHaveCount(2)

    // The marker characters are consumed, not printed — the failure this
    // replaces showed `##` and `**` verbatim on a 5,000-character spec.
    await expect(details).not.toContainText('## What this is for')
    await expect(details).not.toContainText('**bold**')
  })

test('a code span survives with its markers intact', async ({ page }) => {
  // The half of the brief-verbatim worry that DOES apply here: a path inside
  // backticks must reach the page whole rather than being eaten as emphasis.
  await stub(page, task())
  await page.goto('/tasks/80')

  await expect(page.locator('.tbe-body code')
    .filter({ hasText: 'lib/tasks/store.py' })).toBeVisible()
})

test('a task with no body still offers the details card to write one into',
  async ({ page }) => {
    // Changed deliberately when the body became editable in place: a card that
    // hid itself when empty was a task you could never give details to.
    await stub(page, task({ body: null }))
    await page.goto('/tasks/80')

    await expect(page.locator('.tbe-body')).toHaveCount(0)
    await expect(page.getByText('No details yet.')).toBeVisible()
    await expect(page.getByTestId('task-body-edit')).toBeVisible()
  })
