/**
 * F180 #8 — the sending end of the message gate.
 *
 * A send to a task run that has already reported finished comes back as
 * `SendRefused`: the composer keeps the draft, says why, and offers to post it
 * as a comment on the task — which opens a thread the task's next run reads.
 * A prompt the session abandoned after acking it (`dropped:`) shows apart from
 * the queue, with its reason and a way back into the composer.
 *
 * The refusal is stubbed (sealing a run needs a live runner); the comment post
 * runs against the suite's server, so the thread existing is its answer.
 */
import { randomUUID } from 'node:crypto'
import { test, expect } from './auth-fixture.js'
import { TEST_USER } from '../e2e-env.js'
import { API_BASE } from './helpers/api-base.js'

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

test.use({ viewport: { width: 1280, height: 900 } })

const at = i => `2026-05-08T12:00:${String(i).padStart(2, '0')}`
const DRAFT = '补充：还要支持暗色'

async function api(request, method, path, data) {
  const { token } = await (await request.post(`${API_BASE}/api/auth/login`, { data: TEST_USER })).json()
  const res = await request[method](`${API_BASE}${path}`, {
    headers: { Authorization: `Bearer ${token}` }, data,
  })
  expect(res.ok(), `${method} ${path}`).toBeTruthy()
  return res.json()
}

async function seedSession(page, { queued = [] } = {}) {
  await page.goto('/trace/sessions')
  const traceId = randomUUID()
  const sfx = traceId.slice(0, 8)
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect((await page.request.post('/api/session-spans', {
    headers: { Authorization: `Bearer ${token}` },
    data: [
      { trace_id: traceId, span_id: `p-${sfx}`, parent_id: null, name: 'prompt',
        start_time: at(0), attributes: { text: `QUEUED_SEND_${sfx}`, is_test: true } },
      { trace_id: traceId, span_id: `a-${sfx}`, parent_id: `p-${sfx}`,
        name: 'assistant_response', start_time: at(1), duration_ms: 900,
        attributes: { text: 'done', is_test: true } },
    ],
  })).ok()).toBeTruthy()
  const served = { queued }
  await page.route(`**/api/sessions/${traceId}/map*`, async (route) => {
    const res = await route.fetch()
    const body = await res.json()
    await route.fulfill({ response: res, json: {
      ...body, ended_at: null, bridge_reachable: true, queued_prompts: served.queued,
    } })
  })
  return { traceId, served }
}

async function refuseSends(page, traceId, taskId) {
  const sends = []
  await page.route(`**/api/sessions/${traceId}/bridge-send`, (route) => {
    sends.push(route.request().postDataJSON())
    return route.fulfill({ json: {
      delivered: false, reason: 'run_sealed',
      detail: 'This task run has reported finished and its session is closing, so the message was not sent. Post it as a task comment and the next run will receive it.',
      task_id: taskId, run_id: 7, id: 99,
      resume: { session_id: traceId, cwd: '/tmp/d345-wt', model: 'claude-opus-5-5' },
    } })
  })
  return sends
}

async function typeAndSend(page) {
  await page.getByTestId('followup-pill').click()
  const ta = page.getByTestId('followup-textarea')
  await ta.fill(DRAFT)
  await page.getByTestId('followup-send').click()
}

test('test_send_after_seal_is_refused_and_draft_kept', async ({ page }) => {
  const { traceId } = await seedSession(page)
  const sends = await refuseSends(page, traceId, 4242)
  await page.goto(`/trace/sessions/${traceId}?view=conversation`)

  await typeAndSend(page)

  await expect.poll(() => sends.length).toBe(1)
  await expect(page.getByTestId('followup-textarea')).toHaveValue(DRAFT)
  await expect(page.getByTestId('refused-send-detail')).toContainText('reported finished')
  await expect(page.getByTestId('refused-send-post-comment')).toHaveText('Post as task comment')
  await expect(page.getByTestId('followup-status')).toContainText('not sent')
})

test('D345: a refused send can resume the same session with the draft', async ({ page }) => {
  const { traceId } = await seedSession(page)
  await refuseSends(page, traceId, 4242)
  const launches = []
  await page.route('**/api/agent-runs', (route) => {
    launches.push(route.request().postDataJSON())
    return route.fulfill({ json: { launched: true, trace_id: traceId } })
  })
  await page.goto(`/trace/sessions/${traceId}?view=conversation`)
  await typeAndSend(page)

  await page.getByTestId('refused-send-resume').click()

  await expect.poll(() => launches.length).toBe(1)
  expect(launches[0]).toEqual({
    resume: traceId, cwd: '/tmp/d345-wt', model: 'claude-opus-5-5', prompt: DRAFT,
  })
  await expect(page.getByTestId('refused-send')).toHaveCount(0)
})

test('D345: a refused resume keeps the draft and says why', async ({ page }) => {
  const { traceId } = await seedSession(page)
  await refuseSends(page, traceId, 4242)
  await page.route('**/api/agent-runs', route => route.fulfill({
    status: 400, json: { error: 'that run is still live — stop it before resuming' },
  }))
  await page.goto(`/trace/sessions/${traceId}?view=conversation`)
  await typeAndSend(page)

  await page.getByTestId('refused-send-resume').click()

  await expect(page.getByTestId('refused-send-error')).toContainText('still live')
  await expect(page.getByTestId('followup-textarea')).toHaveValue(DRAFT)
  await expect(page.getByTestId('refused-send-resume')).toBeEnabled()
})

test('an ordinary send failure offers no task comment', async ({ page }) => {
  const { traceId } = await seedSession(page)
  await page.route(`**/api/sessions/${traceId}/bridge-send`, route => route.fulfill({
    json: { delivered: false, detail: 'pane not reachable' },
  }))
  await page.goto(`/trace/sessions/${traceId}?view=conversation`)

  await typeAndSend(page)

  await expect(page.getByTestId('followup-status')).toContainText('pane not reachable')
  await expect(page.getByTestId('followup-textarea')).toHaveValue(DRAFT)
  await expect(page.getByTestId('refused-send')).toHaveCount(0)
})

test('test_refused_send_posts_as_task_comment', async ({ page, request }) => {
  const { task } = await api(request, 'post', '/api/tasks', {
    title: `F180 refused send ${Date.now()}`,
  })
  const taskId = task.id
  const { traceId } = await seedSession(page)
  await refuseSends(page, traceId, taskId)
  await page.goto(`/trace/sessions/${traceId}?view=conversation`)
  await typeAndSend(page)

  await page.getByTestId('refused-send-post-comment').click()

  await expect(page.getByTestId('followup-textarea')).toHaveValue('')
  const link = page.getByTestId('refused-send-link')
  await expect(link).toHaveAttribute('href', new RegExp(`/tasks/${taskId}#task-thread-\\d+$`))

  const { threads } = await api(request, 'get', `/api/tasks/${taskId}/comments`)
  expect(threads).toHaveLength(1)
  expect(threads[0].resolution_state).toBe('open')
  expect(threads[0].comments.map(c => c.body)).toEqual([DRAFT])
})

test('test_dropped_message_is_shown_as_dropped', async ({ page }) => {
  const { traceId, served } = await seedSession(page, { queued: [
    { id: 'b501', content: 'restore me please', source: 'dropped',
      detail: 'dropped: completed' },
    { id: 'q1', content: 'still waiting', source: 'sdk' },
  ] })
  const dismissed = []
  await page.route(`**/api/agent-runs/${traceId}/queue/*`, (route) => {
    dismissed.push(new URL(route.request().url()).pathname.split('/').pop())
    served.queued = served.queued.filter(q => q.id !== 'b501')
    return route.fulfill({ json: { removed: true, detail: 'chip dismissed' } })
  })
  await page.goto(`/trace/sessions/${traceId}?view=conversation`)

  const dropped = page.getByTestId('trace-dropped-item')
  await expect(dropped).toHaveCount(1)
  await expect(dropped).toContainText('restore me please')
  await expect(page.getByTestId('trace-dropped-reason')).toHaveText('completed')
  await expect(page.getByTestId('trace-queued-item')).toHaveCount(1)
  await expect(page.getByTestId('trace-queued')).toContainText('1 queued')
  await expect(page.getByTestId('trace-dropped')).not.toHaveCSS(
    'background-color',
    await page.getByTestId('trace-queued').evaluate(el => getComputedStyle(el).backgroundColor))

  await page.getByTestId('trace-dropped-restore').click()

  await expect(page.getByTestId('followup-textarea')).toHaveValue('restore me please')
  await expect.poll(() => dismissed).toEqual(['b501'])
  await expect(page.getByTestId('trace-dropped-item')).toHaveCount(0)
})

test('restore appends to a draft already in the composer', async ({ page }) => {
  const { traceId } = await seedSession(page, { queued: [
    { id: 'b502', content: 'the lost line', source: 'dropped', detail: 'dropped: idle timeout' },
  ] })
  await page.route(`**/api/agent-runs/${traceId}/queue/*`,
    route => route.fulfill({ json: { removed: true } }))
  await page.goto(`/trace/sessions/${traceId}?view=conversation`)
  await page.getByTestId('followup-pill').click()
  await page.getByTestId('followup-textarea').fill('first')

  await page.getByTestId('trace-dropped-restore').click()

  await expect(page.getByTestId('followup-textarea')).toHaveValue('first\nthe lost line')
})

test('restore from the timeline view lands in the conversation composer', async ({ page }) => {
  const { traceId } = await seedSession(page, { queued: [
    { id: 'b503', content: 'from the timeline', source: 'dropped', detail: 'dropped: stopped' },
  ] })
  const dismissed = []
  await page.route(`**/api/agent-runs/${traceId}/queue/*`, (route) => {
    dismissed.push(new URL(route.request().url()).pathname.split('/').pop())
    return route.fulfill({ json: { removed: true } })
  })
  await page.goto(`/trace/sessions/${traceId}?view=timeline`)

  await page.getByTestId('trace-dropped-restore').click()

  await expect(page.getByTestId('followup-textarea')).toHaveValue('from the timeline')
  await expect.poll(() => dismissed).toEqual(['b503'])
})

test('no composer, no restore: the dropped chip stays with its text', async ({ page }) => {
  const { traceId } = await seedSession(page, { queued: [
    { id: 'b504', content: 'nowhere to go', source: 'dropped', detail: 'dropped: completed' },
  ] })
  await page.unroute(`**/api/sessions/${traceId}/map*`)
  await page.route(`**/api/sessions/${traceId}/map*`, async (route) => {
    const res = await route.fetch()
    const body = await res.json()
    await route.fulfill({ response: res, json: {
      ...body, bridge_reachable: false, queued_prompts: [
        { id: 'b504', content: 'nowhere to go', source: 'dropped', detail: 'dropped: completed' },
      ] } })
  })
  await page.goto(`/trace/sessions/${traceId}?view=conversation`)

  await expect(page.getByTestId('trace-dropped-item')).toContainText('nowhere to go')
  await expect(page.getByTestId('trace-dropped-restore')).toHaveCount(0)
  await expect(page.getByTestId('trace-dropped-dismiss')).toBeVisible()
})

test('a refused comment post keeps the draft and says why', async ({ page }) => {
  const { traceId } = await seedSession(page)
  await refuseSends(page, traceId, 4243)
  await page.route('**/api/tasks/4243/comments', route => route.fulfill({
    status: 400, json: { error: 'comment body cites image 1, which was not sent' },
  }))
  await page.goto(`/trace/sessions/${traceId}?view=conversation`)
  await typeAndSend(page)

  await page.getByTestId('refused-send-post-comment').click()

  await expect(page.getByTestId('refused-send-error')).toContainText('cites image 1')
  await expect(page.getByTestId('followup-textarea')).toHaveValue(DRAFT)
  await expect(page.getByTestId('refused-send-post-comment')).toBeEnabled()
})
