/**
 * Editing and dropping a queued prompt from the TRACE session page
 * (`components/TraceQueuedPrompts.vue`), the desktop counterpart of the /live
 * card's queued list (`live-stop-and-queue.spec.js`).
 *
 * Both surfaces read the same server rows (`_queued_prompts`) and drive the
 * same routes, so what is pinned here is the thing that differs: the trace
 * page's block used to be read-only and keyed by ARRAY INDEX, which meant a
 * poll that reordered the queue would have moved an open editor onto a
 * different prompt. Controls follow the row's id, never the page.
 */
import { randomUUID } from 'node:crypto'
import { test, expect } from './auth-fixture.js'

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

const at = i => `2026-05-08T12:00:${String(i).padStart(2, '0')}`
const items = (page) => page.getByTestId('trace-queued-item')

async function post(page, spans) {
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect((await page.request.post('/api/session-spans', {
    headers: { Authorization: `Bearer ${token}` }, data: spans,
  })).ok()).toBeTruthy()
}

/**
 * Patch the map poll's payload without discarding the real one, and return a
 * setter so a test can change what the SERVER serves between polls — the
 * optimistic overrides only settle against the server's own copy, so serving
 * the caught-up row is half of what is under test.
 */
async function makeSession(page, patch) {
  const traceId = randomUUID()
  const sfx = traceId.slice(0, 8)
  await post(page, [
    { trace_id: traceId, span_id: `p-${sfx}`, parent_id: null, name: 'prompt',
      start_time: at(0), attributes: { text: `QUEUE_FIXTURE_${sfx}`, is_test: true } },
    { trace_id: traceId, span_id: `a-${sfx}`, parent_id: `p-${sfx}`,
      name: 'assistant_response', start_time: at(1), duration_ms: 900,
      attributes: { text: 'working on it', is_test: true } },
  ])

  const state = { patch: { ended_at: null, sdk_owned: true, ...patch } }
  await page.route(`**/api/sessions/${traceId}/map*`, async (route) => {
    const res = await route.fetch()
    let body = {}
    try { body = await res.json() } catch { body = {} }
    await route.fulfill({ json: { ...body, ...state.patch } })
  })
  return { traceId, serve: (next) => { state.patch = { ...state.patch, ...next } } }
}

async function seed(page, patch) {
  await page.goto('/trace/sessions')
  const made = await makeSession(page, patch)
  await page.goto(`/trace/sessions/${made.traceId}?view=conversation`)
  return made
}

test('controls follow the row: sdk edits, bridge only dismisses, id-less is inert',
  async ({ page }) => {
    await seed(page, {
      queued_prompts: [
        { id: 'q1', content: 'mine to edit', source: 'sdk' },
        // A bridge steer's keystrokes are already in the pane: its chip can be
        // dismissed but never rewritten.
        { id: 'b7', content: 'typed into the pane', source: 'bridge' },
        { content: "claude code's, not mine" },
      ],
    })

    await expect(items(page)).toHaveCount(3)
    await expect(page.getByTestId('trace-queued-edit')).toHaveCount(1)
    await expect(page.getByTestId('trace-queued-remove')).toHaveCount(2)
  })

test('nothing renders when the queue is empty', async ({ page }) => {
  const { serve } = await seed(page, { queued_prompts: [] })
  await expect(page.getByTestId('trace-queued')).toHaveCount(0)

  // …and the block appears on the poll that first serves a row, rather than
  // needing a reload.
  serve({ queued_prompts: [{ id: 'q1', content: 'just queued', source: 'sdk' }] })
  await expect(items(page)).toHaveCount(1, { timeout: 15_000 })
})

test('a queued prompt is rewritten in its own slot, not the one beside it',
  async ({ page }) => {
    const { serve } = await seed(page, {
      queued_prompts: [
        { id: 'q1', content: 'first', source: 'sdk' },
        { id: 'q2', content: 'teh second', source: 'sdk' },
      ],
    })
    let sent = null
    await page.route('**/api/agent-runs/*/queue/q2', (route) => {
      sent = route.request().postDataJSON()
      return route.fulfill({ json: { updated: true, detail: 'prompt updated' } })
    })

    await page.getByTestId('trace-queued-edit').nth(1).click()
    await page.getByTestId('trace-queued-input').fill('the second')
    await page.getByTestId('trace-queued-save').click()

    await expect(items(page).nth(1)).toContainText('the second')
    expect(sent).toEqual({ prompt: 'the second' })
    await expect(items(page).nth(0)).toContainText('first')

    // And it survives the server catching up.
    serve({
      queued_prompts: [
        { id: 'q1', content: 'first', source: 'sdk' },
        { id: 'q2', content: 'the second', source: 'sdk' },
      ],
    })
    await expect(items(page).nth(1)).toContainText('the second')
    await expect(items(page).nth(0)).toContainText('first')
  })

test('a removed prompt goes at once and stays gone across the poll',
  async ({ page }) => {
    const { serve } = await seed(page, {
      queued_prompts: [
        { id: 'q1', content: 'keep me', source: 'sdk' },
        { id: 'q2', content: 'drop me', source: 'sdk' },
      ],
    })
    let method = null
    await page.route('**/api/agent-runs/*/queue/q2', (route) => {
      method = route.request().method()
      return route.fulfill({ json: { removed: true, detail: 'prompt removed' } })
    })

    await page.getByTestId('trace-queued-remove').nth(1).click()
    await expect(items(page)).toHaveCount(1)
    expect(method).toBe('DELETE')

    // The next polls still carry it (it was in flight when the click landed) —
    // the row must not flicker back.
    await expect(items(page)).toHaveCount(1)
    serve({ queued_prompts: [{ id: 'q1', content: 'keep me', source: 'sdk' }] })
    await expect(items(page)).toHaveCount(1)
    await expect(items(page)).toContainText('keep me')
  })

test('a refused edit puts the row back and says why', async ({ page }) => {
  await seed(page, {
    queued_prompts: [{ id: 'q1', content: 'already running', source: 'sdk' }],
  })
  await page.route('**/api/agent-runs/*/queue/q1', route => route.fulfill({
    json: { updated: false, detail: 'that prompt is already running' },
  }))

  await page.getByTestId('trace-queued-edit').click()
  await page.getByTestId('trace-queued-input').fill('too late')
  await page.getByTestId('trace-queued-save').click()

  await expect(page.getByTestId('trace-queued-notice'))
    .toContainText('that prompt is already running')
  // The editor stays open holding the draft — a refusal is usually "that turn
  // just started", and throwing the typed text away would make the operator
  // retype it to send it to the next one.
  await expect(page.getByTestId('trace-queued-input')).toHaveValue('too late')

  // But the optimistic override must not keep masking the row underneath it:
  // closing the editor shows the server's copy, not the rejected rewrite.
  await page.getByTestId('trace-queued-cancel-edit').click()
  await expect(items(page)).toHaveCount(1)
  await expect(items(page)).toContainText('already running')
})

test('cancelling an edit leaves the prompt as it was', async ({ page }) => {
  await seed(page, {
    queued_prompts: [{ id: 'q1', content: 'as typed', source: 'sdk' }],
  })
  let hits = 0
  await page.route('**/api/agent-runs/*/queue/*', (route) => {
    hits += 1
    return route.fulfill({ json: { updated: true } })
  })

  await page.getByTestId('trace-queued-edit').click()
  await page.getByTestId('trace-queued-input').fill('never mind')
  await page.getByTestId('trace-queued-cancel-edit').click()

  expect(hits).toBe(0)
  await expect(items(page)).toContainText('as typed')
  await expect(page.getByTestId('trace-queued-input')).toHaveCount(0)
})

test('opening a row above the open one still focuses its input', async ({ page }) => {
  await seed(page, {
    queued_prompts: [
      { id: 'q1', content: 'one', source: 'sdk' },
      { id: 'q2', content: 'two', source: 'sdk' },
      { id: 'q3', content: 'three', source: 'sdk' },
    ],
  })

  await page.getByTestId('trace-queued-edit').nth(2).click()
  await expect(page.getByTestId('trace-queued-input')).toBeFocused()

  // Vue patches the list in index order, so this mounts the new editor BEFORE
  // it unmounts the old one — a function ref that accepted the trailing
  // `null` would wipe the element about to be focused.
  await page.getByTestId('trace-queued-edit').first().click()
  await expect(page.getByTestId('trace-queued-input')).toHaveCount(1)
  await expect(page.getByTestId('trace-queued-input')).toHaveValue('one')
  await expect(page.getByTestId('trace-queued-input')).toBeFocused()
})

test('an editor whose row the agent consumed cannot reopen over its successor',
  async ({ page }) => {
    const { serve } = await seed(page, {
      queued_prompts: [
        { id: 'q1', content: 'first', source: 'sdk' },
        { id: 'q2', content: 'second', source: 'sdk' },
      ],
    })

    await page.getByTestId('trace-queued-edit').nth(1).click()
    await page.getByTestId('trace-queued-input').fill('a draft nobody will send')

    // The agent picks the prompt up: its row leaves the queue and takes the
    // editor with it.
    serve({ queued_prompts: [{ id: 'q1', content: 'first', source: 'sdk' }] })
    await expect(page.getByTestId('trace-queued-input')).toHaveCount(0, { timeout: 15_000 })

    // A relaunched runner counts its queue ids from `q1` again, so the id can
    // come back on a different prompt. The stale draft must not reopen over
    // it — saving would PATCH one prompt's body onto another.
    serve({
      queued_prompts: [
        { id: 'q1', content: 'first', source: 'sdk' },
        { id: 'q2', content: 'a brand new prompt', source: 'sdk' },
      ],
    })
    await expect(items(page).nth(1)).toContainText('a brand new prompt', { timeout: 15_000 })
    await expect(page.getByTestId('trace-queued-input')).toHaveCount(0)
  })

test('a session switched in place drops the outgoing queue\'s pending state',
  async ({ page }) => {
    await page.goto('/trace/sessions')
    // `/clear` replaces the route without remounting the view (useSuccessorJump
    // does `router.replace` on the same route), so the composable's ops, busy
    // ids and notice would otherwise carry across — and queue ids are
    // per-runner, so they would mask or hide unrelated rows over here.
    const heir = await makeSession(page, {
      queued_prompts: [{ id: 'q1', content: 'the heir queue', source: 'sdk' }],
    })
    const { serve } = await seed(page, {
      queued_prompts: [{ id: 'q1', content: 'the old queue', source: 'sdk' }],
    })
    await page.route('**/api/agent-runs/*/queue/q1', route => route.fulfill({
      json: { removed: false, detail: 'that prompt is already running' },
    }))

    await page.getByTestId('trace-queued-remove').click()
    await expect(page.getByTestId('trace-queued-notice')).toBeVisible()

    serve({ successor_trace_id: heir.traceId })
    await expect(page).toHaveURL(new RegExp(`${heir.traceId}`), { timeout: 15_000 })

    await expect(items(page)).toContainText('the heir queue')
    await expect(page.getByTestId('trace-queued-notice')).toHaveCount(0)
    // The refused removal is the outgoing session's; the heir's identically-id'd
    // row must not inherit it.
    await expect(items(page)).toHaveCount(1)
  })
