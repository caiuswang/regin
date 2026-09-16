/**
 * The task-link card's way into the feature ledger (CAI-253).
 *
 * The card has two subjects and now one link each: the key is what the tracker
 * says, `ledger →` is what regin says. The chip is neither — it is a figure,
 * and it is absent exactly when the ledger is most worth opening.
 */
import { test, expect } from './auth-fixture.js'

function payload(over = {}) {
  return {
    links: [{
      id: 1, task_id: 1, provider: 'linear', external_key: 'CAI-248',
      url: 'https://linear.app/x',
      synced_at: '2026-09-13 05:40:00',
      sync_error: null, created_at: '2026-09-13 05:38:06',
      stage: {
        feature_key: 'CAI-248', stage: 'shipped',
        blurb: 'completed and never walked back',
        rounds: 1, open_defects: 0, escapes: 0,
      },
      issue: {
        provider: 'linear', key: 'CAI-248', url: 'https://linear.app/x',
        title: 'What a feature index is', state: 'Done',
        state_category: 'completed', rounds: 1, labels: [], extra: {},
      },
      ...over,
    }],
  }
}

async function stubLinks(page, body) {
  await page.route(/\/api\/tasks\/\d+\/links/, (route) =>
    route.fulfill({ json: body }))
}

async function seedTask(page) {
  await page.goto('/tasks')
  return page.evaluate(async () => {
    const token = localStorage.getItem('regin_auth_token')
    const res = await fetch('/api/tasks', {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ title: 'ledger-link-fixture' }),
    })
    return (await res.json()).task.id
  })
}

test('every link row carries a ledger link regardless of stage',
  async ({ page }) => {
    const id = await seedTask(page)
    // The stage-less case is the one that matters: the chip is absent exactly
    // when the ledger has the most to say.
    await stubLinks(page, payload({ stage: null, issue: null,
                                    synced_at: null }))
    await page.goto(`/tasks/${id}`)

    const link = page.getByTestId('task-link-ledger-1')
    await expect(link).toBeVisible()
    await expect(link).toHaveAttribute('href', '/features/CAI-248')
  })

test('the stage chip itself takes no link', async ({ page }) => {
  const id = await seedTask(page)
  await stubLinks(page, payload())
  await page.goto(`/tasks/${id}`)

  const chip = page.getByTestId('task-link-stage-1')
  await expect(chip).toBeVisible()
  await expect(chip.locator('a')).toHaveCount(0)
  await expect(chip).not.toHaveAttribute('href', /./)
})

test('never synced reads as the shared stage-not-fetched wording',
  async ({ page }) => {
    const id = await seedTask(page)
    await stubLinks(page, payload({ stage: null, issue: null,
                                    synced_at: null }))
    await page.goto(`/tasks/${id}`)

    // One wording across the card and the ledger page: a phrase that forks by
    // surface is the disagreement the shared cache exists to prevent.
    await expect(page.getByTestId('task-link-never-1'))
      .toHaveText('stage not fetched')
  })

test('a synced link dates itself in the ledger page format', async ({ page }) => {
  const id = await seedTask(page)
  await stubLinks(page, payload())
  await page.goto(`/tasks/${id}`)

  // `asOf`, not the raw timestamp: both surfaces read one cache, so they say
  // its age the same way.
  await expect(page.getByText(/synced 13 Sept/)).toBeVisible()
})
