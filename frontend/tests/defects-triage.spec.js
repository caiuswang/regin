/**
 * The triage queue: four terminal verbs on one key each, and nothing that
 * proposes a parent.
 *
 * The queue is stubbed at the API boundary because what these assert is the
 * *surface* — that a chord reaches the right route with the right body, that
 * the count beside the heading is the one the read returned, and that the
 * accept field arrives empty. The server-side rules (the predicate, the path
 * union, the snooze watermark) are pinned in `tests/defects/test_web_triage.py`
 * where they belong.
 */
import { test, expect } from './auth-fixture.js'

function queued(over = {}) {
  return {
    id: 1, feature_id: null, feature_title: null,
    symptom: 'the gutter folds', status: 'open', round: 1,
    blamed_event_kind: null, missing_check: null, suspect_paths: [],
    fix_task_id: null, fix_state: null,
    triage_state: null, triage_reason: null,
    opened_at: '2026-09-14 12:00:00', updated_at: '2026-09-14 12:00:00',
    closed_at: null, images: [],
    ...over,
  }
}

const A = queued({ id: 11, symptom: 'the gutter folds',
                   suspect_paths: ['lib/features/cost.py'] })
const B = queued({ id: 12, symptom: 'the rail clips' })

/** Open the queue and wait for it to have drawn — a chord pressed before the
 *  first read lands has no focused row to act on. */
async function openQueue(page) {
  await page.goto('/defects?scope=triage')
  await expect(page.getByTestId('triage-card-11')).toBeVisible()
}

/** The queue read, plus whatever the page asked for outside it. */
async function stubQueue(page, { rows = [A, B], seen = [] } = {}) {
  await page.route('**/api/defects?*', (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    const url = new URL(route.request().url())
    seen.push(url.search)
    if (url.searchParams.get('scope') !== 'triage') {
      return route.fulfill({ json: { items: [], scope: 'all',
                                     pagination: { total: 0 } } })
    }
    return route.fulfill({
      json: { items: rows, scope: 'triage',
              pagination: { total: rows.length, page: 0, size: 50 } },
    })
  })
  return seen
}

test('the queue renders its own count from its own read', async ({ page }) => {
  await stubQueue(page)
  await page.goto('/defects?scope=triage')

  await expect(page.getByTestId('defect-triage')).toBeVisible()
  // The badge is `pagination.total` off the very read that produced the rows,
  // so it cannot disagree with what is on screen.
  await expect(page.getByTestId('triage-count')).toHaveText('2')
  await expect(page.getByTestId('triage-card-11')).toBeVisible()
  await expect(page.getByTestId('triage-card-12')).toBeVisible()
})

test('the queue preselects no feature', async ({ page }) => {
  await stubQueue(page)
  await openQueue(page)

  await page.keyboard.press('1')
  const field = page.getByTestId('triage-card-11').getByRole('textbox')
  await expect(field).toBeVisible()
  // Empty, on a defect whose suspect path names a real area. Nothing on this
  // surface turns a path into a parent.
  await expect(field).toHaveValue('')
})

test('J and K walk the queue and the cursor is visible', async ({ page }) => {
  await stubQueue(page)
  await openQueue(page)

  await expect(page.getByTestId('triage-card-11'))
    .toHaveAttribute('aria-current', 'true')
  await page.keyboard.press('J')
  await expect(page.getByTestId('triage-card-12'))
    .toHaveAttribute('aria-current', 'true')
  await expect(page.getByTestId('triage-card-11'))
    .not.toHaveAttribute('aria-current', 'true')
  await page.keyboard.press('K')
  await expect(page.getByTestId('triage-card-11'))
    .toHaveAttribute('aria-current', 'true')
})

test('K at the top and J at the bottom do not run off the ends',
  async ({ page }) => {
    await stubQueue(page)
    await openQueue(page)

    await page.keyboard.press('K')
    await expect(page.getByTestId('triage-card-11'))
      .toHaveAttribute('aria-current', 'true')
    await page.keyboard.press('J')
    await page.keyboard.press('J')
    await expect(page.getByTestId('triage-card-12'))
      .toHaveAttribute('aria-current', 'true')
  })

test('accept posts the typed feature id to attribute', async ({ page }) => {
  await stubQueue(page)
  const posted = []
  await page.route('**/api/defects/*/attribute', async (route) => {
    posted.push([route.request().url(), route.request().postDataJSON()])
    await route.fulfill({ json: { defect: { ...A, feature_id: 81 } } })
  })

  await openQueue(page)
  await page.keyboard.press('1')
  await page.getByTestId('triage-card-11').getByRole('textbox').fill('81')
  await page.getByTestId('triage-submit-accept').click()

  expect(posted).toHaveLength(1)
  expect(posted[0][0]).toContain('/api/defects/11/attribute')
  // A number, not a string: the route reads it straight onto a foreign key.
  expect(posted[0][1]).toEqual({ feature_id: 81 })
})

test('mark-duplicate folds the focused defect into the id typed',
  async ({ page }) => {
    await stubQueue(page)
    const posted = []
    await page.route('**/api/defects/*/merge', async (route) => {
      posted.push([route.request().url(), route.request().postDataJSON()])
      await route.fulfill({ json: { defect: B } })
    })

    await openQueue(page)
    await page.keyboard.press('2')
    await page.getByTestId('triage-card-11').getByRole('textbox').fill('12')
    await page.getByTestId('triage-submit-duplicate').click()

    expect(posted).toHaveLength(1)
    // The FOCUSED row is the duplicate; the typed id is the keeper.
    expect(posted[0][0]).toContain('/api/defects/11/merge')
    expect(posted[0][1]).toEqual({ into: 12 })
  })

test('decline sends no reason when none is typed', async ({ page }) => {
  await stubQueue(page)
  const posted = []
  await page.route('**/api/defects/*/decline', async (route) => {
    posted.push(route.request().postDataJSON())
    await route.fulfill({ json: { defect: { ...A, status: 'closed',
                                            triage_state: 'declined' } } })
  })

  await openQueue(page)
  await page.keyboard.press('3')
  await page.getByTestId('triage-submit-decline').click()

  expect(posted).toEqual([{ reason: null }])
})

test('snooze takes no field at all — no deadline to type', async ({ page }) => {
  await stubQueue(page)
  const posted = []
  await page.route('**/api/defects/*/snooze', async (route) => {
    posted.push([route.request().url(), route.request().postDataJSON()])
    await route.fulfill({ json: { defect: { ...A, triage_state: 'snoozed' } } })
  })

  await openQueue(page)
  // The chord fires the POST with nothing in between, so the request is what
  // is waited on — there is no field to appear and nothing else to settle on.
  const sent = page.waitForRequest(/\/api\/defects\/11\/snooze$/)
  await page.keyboard.press('H')
  await sent

  await expect(page.getByTestId('triage-card-11').getByRole('textbox'))
    .toHaveCount(0)
  expect(posted).toHaveLength(1)
  expect(posted[0][0]).toContain('/api/defects/11/snooze')
  expect(posted[0][1]).toEqual({})
})

test('a chord typed into an open field is text, not a command',
  async ({ page }) => {
    await stubQueue(page)
    let snoozes = 0
    await page.route('**/api/defects/*/snooze', async (route) => {
      snoozes += 1
      await route.fulfill({ json: { defect: A } })
    })

    await openQueue(page)
    await page.keyboard.press('1')
    const field = page.getByTestId('triage-card-11').getByRole('textbox')
    await field.click()
    await page.keyboard.type('H')

    await expect(field).toHaveValue('H')
    expect(snoozes).toBe(0)
  })

test('an unsendable field refuses before the post', async ({ page }) => {
  await stubQueue(page)
  let posts = 0
  await page.route('**/api/defects/*/attribute', async (route) => {
    posts += 1
    await route.fulfill({ json: { defect: A } })
  })

  await openQueue(page)
  await page.keyboard.press('1')
  await page.getByTestId('triage-card-11').getByRole('textbox').fill('eight')
  await expect(page.getByTestId('triage-submit-accept')).toBeDisabled()
  expect(posts).toBe(0)
})

test('a POST that never completes does not leave the queue dead',
  async ({ page }) => {
    // `api.post` answers an HTTP refusal with a value but REJECTS when the
    // fetch itself fails. That rejection used to escape past `busy = false`,
    // and every later chord then hit the busy guard and returned in silence.
    await stubQueue(page)
    let first = true
    await page.route('**/api/defects/*/snooze', async (route) => {
      if (first) { first = false; return route.abort('connectionreset') }
      await route.fulfill({ json: { defect: { ...A, triage_state: 'snoozed' } } })
    })

    await openQueue(page)
    await page.keyboard.press('H')
    await expect(page.getByTestId('triage-refusal-11')).toBeVisible()

    // The queue is still live: the same chord reaches the server on the retry.
    const sent = page.waitForRequest(/\/api\/defects\/11\/snooze$/)
    await page.keyboard.press('H')
    await sent
  })

test('an empty queue says so without claiming the ledger is empty',
  async ({ page }) => {
    await stubQueue(page, { rows: [] })
    await page.goto('/defects?scope=triage')

    await expect(page.getByTestId('triage-empty')).toContainText('queue is clear')
    await expect(page.getByTestId('triage-count')).toHaveText('0')
  })

test('the Triage pill is a scope of its own, reachable from All',
  async ({ page }) => {
    const seen = []
    await stubQueue(page, { seen })
    await page.goto('/defects')

    await page.getByRole('tab', { name: 'Triage' }).click()
    await expect(page).toHaveURL(/scope=triage/)
    await expect(page.getByTestId('defect-triage')).toBeVisible()
  })
