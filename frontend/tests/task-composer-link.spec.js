/**
 * Capture by reference: pasting a tracker issue into the New-task dialog.
 *
 * The claim under test is the composer's half of the contract — resolve fills
 * only what is still blank, and the capture that follows attaches the ref to
 * the row it just created. The server's half (that a preview writes nothing,
 * and what each failure answers) is pinned in `tests/tasks/test_task_links.py`
 * against a fake provider; here the tracker is stubbed at the network edge so
 * the run never depends on a credential or on someone else's API being up.
 *
 * Both capture surfaces are covered: the /tasks composer and the quick-capture
 * bubble the app shell mounts on every route. They are separate components,
 * and the bubble is the one reached mid-thought from a trace — a link field
 * present in only one of them is the same retyping, moved.
 */
import { randomUUID } from 'node:crypto'
import { test, expect } from './auth-fixture.js'

const ISSUE = {
  provider: 'stub',
  key: 'ENG-42',
  url: 'https://tracker.test/issue/ENG-42',
  issue: {
    provider: 'stub', key: 'ENG-42', url: 'https://tracker.test/issue/ENG-42',
    title: 'Wire the composer to the tracker',
    description: 'The body the tracker already holds.',
    state: 'In Progress', state_category: 'started', labels: [],
  },
}

// Every attach the page makes, so a test can assert what the composer sent
// without the run depending on a tracker the server could reach.
async function stubTracker(page, { hold, issues } = {}) {
  const attaches = []
  await page.route('**/api/task-integrations', route => route.fulfill({
    json: { providers: [{ provider: 'stub', display_name: 'Stub Tracker',
                          ref_hint: 'an issue key like ENG-42',
                          configured: true }] },
  }))
  await page.route('**/api/tasks/links/preview', async (route, request) => {
    if (hold) await hold
    const ref = request.postDataJSON().ref
    return route.fulfill({ json: (issues || {})[ref] || ISSUE })
  })
  await page.route(/\/api\/tasks\/\d+\/links$/, (route, request) => {
    if (request.method() !== 'POST') return route.continue()
    const taskId = Number(request.url().match(/\/tasks\/(\d+)\/links/)[1])
    attaches.push({ taskId, ...request.postDataJSON() })
    return route.fulfill({
      status: 201,
      json: { link: { id: 1, task_id: taskId, provider: 'stub',
                      external_key: ISSUE.key, url: ISSUE.url,
                      issue: ISSUE.issue, stage: null,
                      synced_at: '2026-09-13 00:00:00', sync_error: null,
                      created_at: '2026-09-13 00:00:00' } },
    })
  })
  return attaches
}

async function openComposer(page) {
  await page.goto('/tasks')
  await page.getByTestId('task-new').click()
  await expect(page.getByTestId('task-title')).toBeVisible()
}

// A real paste, not a typed value: the field's whole point is that dropping a
// URL in it is the entire interaction.
async function pasteRef(page, value = ISSUE.key) {
  await page.getByTestId('task-composer-link-input').evaluate((el, text) => {
    const data = new DataTransfer()
    data.setData('text/plain', text)
    el.dispatchEvent(new ClipboardEvent('paste',
      { clipboardData: data, bubbles: true, cancelable: true }))
  }, value)
}

async function resolve(page, ref = ISSUE.key, key = ref) {
  await page.getByTestId('task-composer-link-input').fill(ref)
  await page.getByTestId('task-composer-link-submit').click()
  await expect(page.getByTestId('task-composer-link-resolved')).toContainText(key)
}

test('composer resolves a pasted ref and prefills title and details', async ({ page }) => {
  await stubTracker(page)
  await openComposer(page)
  await resolve(page)

  await expect(page.getByTestId('task-title')).toHaveValue(ISSUE.issue.title)
  await expect(page.locator('#task-body')).toHaveValue(ISSUE.issue.description)
})

test('composer leaves an already-typed title alone when a ref resolves', async ({ page }) => {
  await stubTracker(page)
  await openComposer(page)
  const mine = 'my own words'
  await page.getByTestId('task-title').fill(mine)
  await resolve(page)

  await expect(page.getByTestId('task-title')).toHaveValue(mine)
  // The details were blank, so the tracker still fills them.
  await expect(page.locator('#task-body')).toHaveValue(ISSUE.issue.description)
})

test('capturing with a resolved ref attaches the link to the new task', async ({ page }) => {
  const attaches = await stubTracker(page)
  await openComposer(page)
  await resolve(page)

  const title = `linked capture ${randomUUID().slice(0, 8)}`
  await page.getByTestId('task-title').fill(title)
  const created = page.waitForResponse(r => r.url().endsWith('/api/tasks')
    && r.request().method() === 'POST')
  await page.getByTestId('task-submit').click()
  const taskId = (await (await created).json()).task.id

  await expect(page.getByTestId('task-title')).toBeHidden()
  await expect.poll(() => attaches).toEqual([{ taskId, ref: ISSUE.key }])
})

test('pasting a ref fills the title with no Fetch click', async ({ page }) => {
  await stubTracker(page)
  await openComposer(page)
  await pasteRef(page, ISSUE.url)

  await expect(page.getByTestId('task-title')).toHaveValue(ISSUE.issue.title)
  await expect(page.locator('#task-body')).toHaveValue(ISSUE.issue.description)
})

test('a pasted ref alone captures without a typed title', async ({ page }) => {
  // The read is held until after the click, so this also pins the race the
  // operator actually hits: paste, then capture before the tracker answers.
  let release
  const attaches = await stubTracker(page,
    { hold: new Promise(r => { release = r }) })
  await openComposer(page)
  await pasteRef(page)

  await expect(page.getByTestId('task-title')).toHaveValue('')
  const created = page.waitForResponse(r => r.url().endsWith('/api/tasks')
    && r.request().method() === 'POST')
  await page.getByTestId('task-submit').click()
  release()

  const task = (await (await created).json()).task
  expect(task.title).toBe(ISSUE.issue.title)
  await expect.poll(() => attaches).toEqual([{ taskId: task.id, ref: ISSUE.key }])
})

test('the quick-capture bubble fills its title from a pasted ref too',
  async ({ page }) => {
    const attaches = await stubTracker(page)
    await page.goto('/tasks')
    await page.getByTestId('quick-capture-fab').click()
    await expect(page.getByTestId('quick-capture-form')).toBeVisible()
    await pasteRef(page)

    await expect(page.getByLabel('Idea title')).toHaveValue(ISSUE.issue.title)

    const created = page.waitForResponse(r => r.url().endsWith('/api/tasks')
      && r.request().method() === 'POST')
    await page.getByTestId('quick-capture-save').click()
    const task = (await (await created).json()).task

    expect(task.title).toBe(ISSUE.issue.title)
    await expect.poll(() => attaches).toEqual([{ taskId: task.id, ref: ISSUE.key }])
  })

// The bug this pins: the first resolve filled title and details, which made
// every later ref look like it was overwriting the operator's own words. The
// card updated, the fields did not, and the capture landed describing the
// wrong issue.
const SECOND = {
  provider: 'stub',
  key: 'ENG-99',
  url: 'https://tracker.test/issue/ENG-99',
  issue: {
    provider: 'stub', key: 'ENG-99', url: 'https://tracker.test/issue/ENG-99',
    title: 'The second issue entirely',
    description: 'A different body, from a different issue.',
    state: 'Todo', state_category: 'unstarted', labels: [],
  },
}

const TWO = { [ISSUE.key]: ISSUE, [SECOND.key]: SECOND }

test('a second ref replaces the text the first one filled', async ({ page }) => {
  await stubTracker(page, { issues: TWO })
  await openComposer(page)
  await resolve(page, ISSUE.key)
  await expect(page.getByTestId('task-title')).toHaveValue(ISSUE.issue.title)

  await resolve(page, SECOND.key)

  await expect(page.getByTestId('task-title')).toHaveValue(SECOND.issue.title)
  await expect(page.locator('#task-body')).toHaveValue(SECOND.issue.description)
})

test('a second ref still leaves the operator\'s own title alone', async ({ page }) => {
  await stubTracker(page, { issues: TWO })
  await openComposer(page)
  const mine = 'my own words'
  await page.getByTestId('task-title').fill(mine)
  await resolve(page, ISSUE.key)
  await resolve(page, SECOND.key)

  await expect(page.getByTestId('task-title')).toHaveValue(mine)
  // The details were the tracker's, so they follow the new issue.
  await expect(page.locator('#task-body')).toHaveValue(SECOND.issue.description)
})

test('a title edited after the first resolve outranks the second ref',
  async ({ page }) => {
    await stubTracker(page, { issues: TWO })
    await openComposer(page)
    await resolve(page, ISSUE.key)
    const mine = 'renamed by hand'
    await page.getByTestId('task-title').fill(mine)
    await resolve(page, SECOND.key)

    await expect(page.getByTestId('task-title')).toHaveValue(mine)
  })

test('the bubble replaces its filled title across a close and reopen',
  async ({ page }) => {
    await stubTracker(page, { issues: TWO })
    await page.goto('/tasks')
    await page.getByTestId('quick-capture-fab').click()
    await expect(page.getByTestId('quick-capture-form')).toBeVisible()
    await resolve(page, ISSUE.key)
    await expect(page.getByLabel('Idea title')).toHaveValue(ISSUE.issue.title)

    // Cancel unmounts the bubble but keeps the draft — the baseline has to
    // survive with it, or the tracker's own text reads as the operator's.
    await page.getByRole('button', { name: 'Cancel' }).click()
    await expect(page.getByTestId('quick-capture-form')).toBeHidden()
    await page.getByTestId('quick-capture-fab').click()
    await expect(page.getByTestId('quick-capture-form')).toBeVisible()

    await resolve(page, SECOND.key)
    await expect(page.getByLabel('Idea title')).toHaveValue(SECOND.issue.title)
  })

test('retyping the first issue\'s own title keeps it through a second ref',
  async ({ page }) => {
    // The baseline may only claim a field the tracker actually wrote: the
    // title here is the operator's, even though it reads like issue A's.
    await stubTracker(page, { issues: TWO })
    await openComposer(page)
    await page.getByTestId('task-title').fill('mine for now')
    await resolve(page, ISSUE.key)
    await page.getByTestId('task-title').fill(ISSUE.issue.title)
    await resolve(page, SECOND.key)

    await expect(page.getByTestId('task-title')).toHaveValue(ISSUE.issue.title)
  })
