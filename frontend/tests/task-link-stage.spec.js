/**
 * The pipeline stage on a task's linked issue (CAI-216, CAI-242).
 *
 * The links payload is stubbed rather than seeded through a real attach: the
 * card's only job is to render whatever canonical shape the server computed,
 * and binding this test to a live tracker would make a rendering regression
 * look like a network failure instead.
 */
import { test, expect } from './auth-fixture.js'

const payload = (stage) => ({
  links: [{
    id: 1,
    task_id: 1,
    provider: 'linear',
    external_key: 'CAI-216',
    url: 'https://linear.app/x',
    synced_at: '2026-09-13 05:40:00',
    sync_error: null,
    created_at: '2026-09-13 05:38:06',
    stage,
    issue: {
      provider: 'linear',
      key: 'CAI-216',
      url: 'https://linear.app/x',
      title: 'Show the pipeline stage',
      state: 'In Progress',
      state_category: 'started',
      rounds: stage.rounds,
      labels: [],
      extra: {},
    },
  }],
})

async function stubLinks(page, stage) {
  await page.route(/\/api\/tasks\/\d+\/links/, (route) =>
    route.fulfill({ json: payload(stage) }))
}

/** A real task row, because the detail view renders no link section without
 *  one — only the links payload itself is stubbed. */
async function seedTask(page) {
  await page.goto('/tasks')
  const id = await page.evaluate(async () => {
    const token = localStorage.getItem('regin_auth_token')
    const res = await fetch('/api/tasks', {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ title: 'stage-card-fixture' }),
    })
    return (await res.json()).task.id
  })
  return id
}

test('an escaped feature shows its stage, escapes and round count', async ({ page }) => {
  const id = await seedTask(page)
  await stubLinks(page, {
    feature_key: 'CAI-216',
    stage: 'escaped',
    rounds: 1,
    open_defects: 2,
    escapes: 3,
    blurb: 'shipped, and bugs have been blamed on it since',
  })

  await page.goto(`/tasks/${id}`)

  const badge = page.getByTestId('task-link-stage-1')
  await expect(badge).toHaveText('escaped')
  await expect(badge).toHaveAttribute('title', /blamed/)
  await expect(page.getByTestId('task-link-rounds-1')).toHaveText('1 round')
  await expect(page.getByTestId('task-link-escapes-1')).toHaveText('3 escapes')
  await expect(page.getByTestId('task-link-open-defects-1'))
    .toHaveText('2 open defects')
})

test('a feature that was right first time reads one round', async ({ page }) => {
  const id = await seedTask(page)
  await stubLinks(page, {
    feature_key: 'CAI-216',
    stage: 'shipped',
    rounds: 1,
    open_defects: 0,
    escapes: 0,
    blurb: 'completed and never walked back',
  })

  await page.goto(`/tasks/${id}`)

  await expect(page.getByTestId('task-link-stage-1')).toHaveText('shipped')
  await expect(page.getByTestId('task-link-rounds-1')).toHaveText('1 round')
  // Absent rather than a zero: an empty count is noise on every healthy feature.
  await expect(page.getByTestId('task-link-open-defects-1')).toHaveCount(0)
  await expect(page.getByTestId('task-link-escapes-1')).toHaveCount(0)
})
