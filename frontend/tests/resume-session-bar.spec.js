/**
 * The way back into a session whose server was stopped.
 *
 * Killing `regin serve` takes an SDK-launched child with it, so the run is
 * reaped but the conversation stays on screen with no composer — the registry
 * that made it steerable died with the process. The Resume bar is the exit:
 * it appears exactly where the composer would, only when the server says the
 * CLI could still reopen the id, and it launches through the same route the
 * /live launch sheet posts to.
 *
 * `resumable` / `bridge_reachable` are computed from one server-side branch
 * and neither is something an E2E run can stand up, so both are patched onto
 * the map poll — they are the premise here, not the thing under test.
 */
import { randomUUID } from 'node:crypto'
import { test, expect } from './auth-fixture.js'

const at = i => `2026-05-08T12:00:${String(i).padStart(2, '0')}`

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

const bar = (page) => page.getByTestId('resume-session-bar')
const resumeButton = (page) => page.getByTestId('resume-session-button')
const composer = (page) => page.getByTestId('followup-composer')

function turnSpans(traceId, tag) {
  const prompt = `p-${tag}`
  return [
    {
      trace_id: traceId, span_id: prompt, parent_id: null, name: 'prompt',
      start_time: at(0), attributes: { text: `turn ${tag} prompt`, is_test: true },
    },
    {
      trace_id: traceId, span_id: `a-${tag}`, parent_id: prompt,
      name: 'assistant_response', start_time: at(1), duration_ms: 900,
      attributes: { text: `turn ${tag} answer`, is_test: true },
    },
  ]
}

async function post(page, spans) {
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect((await page.request.post('/api/session-spans', {
    headers: { Authorization: `Bearer ${token}` }, data: spans,
  })).ok()).toBeTruthy()
}

async function seed(page, patch) {
  const traceId = randomUUID()
  await page.goto('/trace/sessions')
  await page.evaluate(() => localStorage.removeItem('regin.focus'))
  await post(page, turnSpans(traceId, traceId.slice(0, 8)))

  await page.route(`**/api/sessions/${traceId}/map*`, async (route) => {
    const res = await route.fetch()
    await route.fulfill({ response: res, json: { ...(await res.json()), ...patch } })
  })
  await page.goto(`/trace/sessions/${traceId}?view=conversation`)
  return traceId
}

const REAPED = {
  ended_at: at(2),
  ended_reason: 'server restarted',
  bridge_reachable: false,
  resumable: true,
  cwd: '/Users/x/repo',
  model: 'claude-opus-5',
}

test('a reaped session offers Resume where the composer would be', async ({ page }) => {
  await seed(page, REAPED)

  await expect(bar(page)).toBeVisible()
  await expect(bar(page)).toContainText('server restarted')
  await expect(composer(page)).toHaveCount(0)
})

test('a steerable session gets the composer and no Resume', async ({ page }) => {
  // The server never sets both, but the client must not render both either:
  // two ways to continue one conversation is two ways to fork it.
  await seed(page, { ended_at: null, bridge_reachable: true, resumable: true })

  await expect(composer(page)).toBeVisible()
  await expect(bar(page)).toHaveCount(0)
})

test('an ended session the CLI cannot reopen offers neither', async ({ page }) => {
  await seed(page, { ...REAPED, resumable: false })

  await expect(bar(page)).toHaveCount(0)
  await expect(composer(page)).toHaveCount(0)
})

test('Resume launches with the session own cwd and model, staying on the page', async ({ page }) => {
  const traceId = await seed(page, REAPED)
  let body = null
  await page.route('**/api/agent-runs', async (route) => {
    body = route.request().postDataJSON()
    await route.fulfill({ json: { launched: true, trace_id: 'sdk-resumed01' } })
  })

  await resumeButton(page).click()
  await expect.poll(() => body).not.toBeNull()

  // The run continues under the id this page is already open on, so a resume
  // that walked the operator to /live would move them off the thread they are
  // reading to show them the same one.
  await expect(page).toHaveURL(new RegExp(`/trace/sessions/${traceId}`))
  // `--resume` resolves the id relative to the working directory, and a
  // continuation that silently changed model is not something the operator
  // asked for — so both ride the launch.
  expect(body).toEqual({ resume: traceId, cwd: '/Users/x/repo', model: 'claude-opus-5' })
})


test('the resumed session swaps the bar for the composer in place', async ({ page }) => {
  // The reload the launch triggers is what does it: the server clears
  // `ended_at` before answering, so the next /map reads live and bridgeable.
  const traceId = randomUUID()
  await page.goto('/trace/sessions')
  await page.evaluate(() => localStorage.removeItem('regin.focus'))
  await post(page, turnSpans(traceId, traceId.slice(0, 8)))

  let resumed = false
  let maps = 0
  await page.route(`**/api/sessions/${traceId}/map*`, async (route) => {
    maps += 1
    const res = await route.fetch()
    const live = { ended_at: null, ended_reason: '', bridge_reachable: true, sdk_owned: true, resumable: false }
    await route.fulfill({ response: res, json: { ...(await res.json()), ...(resumed ? live : REAPED) } })
  })
  await page.route('**/api/agent-runs', async (route) => {
    resumed = true
    await route.fulfill({ json: { launched: true, trace_id: 'sdk-resumed01' } })
  })
  await page.goto(`/trace/sessions/${traceId}?view=conversation`)

  await expect(bar(page)).toBeVisible()
  await resumeButton(page).click()

  await expect(composer(page)).toBeVisible()
  await expect(bar(page)).toHaveCount(0)
  await expect(page).toHaveURL(new RegExp(`/trace/sessions/${traceId}`))

  // And the tail is live again, not just the composer: an ended session runs a
  // bounded catch-up and no recurring poll, so without the re-arm the operator
  // would have a composer whose replies never appeared. LIVE_POLL_MS is 4000.
  const afterSwap = maps
  await expect.poll(() => maps, { timeout: 15000 }).toBeGreaterThan(afterSwap)
})

test('a refused resume says why and stays on the page', async ({ page }) => {
  const traceId = await seed(page, REAPED)
  await page.route('**/api/agent-runs', route => route.fulfill({
    json: { launched: false, detail: 'that session is live in a terminal' },
  }))

  await resumeButton(page).click()

  await expect(bar(page)).toContainText('that session is live in a terminal')
  await expect(page).toHaveURL(new RegExp(`/trace/sessions/${traceId}`))
  // A refusal re-enables the button; only a launch that started keeps it busy.
  await expect(resumeButton(page)).toBeEnabled()
})

test('a session another app owns names it and offers no Resume', async ({ page }) => {
  // WorkBuddy's desktop app owns its sessions and regin cannot launch it, so
  // "no longer running — Resume" was both unfounded and a dead button.
  await seed(page, { ...REAPED, ended_at: null, ended_reason: '',
                     resumable: false, driven_by: 'WorkBuddy' })

  const external = page.getByTestId('external-session-bar')
  await expect(external).toBeVisible()
  await expect(external).toContainText('Recorded from WorkBuddy')
  await expect(external).not.toContainText('no longer running')
  await expect(bar(page)).toHaveCount(0)
  await expect(composer(page)).toHaveCount(0)
})
