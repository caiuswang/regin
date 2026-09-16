/**
 * `/trace/sessions/<sdk-id>` renames itself onto the canonical child id.
 *
 * A run launched from the trace detail page can only be navigated to under the
 * run's own `sdk-…` id — the child has not named itself yet, so the FIRST
 * `/map` reports that same id as canonical. Everything else keys the session
 * on the child's id, including the endpoints that do NOT alias-resolve
 * (`/tool-rollup`, `/turn-usage`, `/agent-messages`, `/workflow-runs`), so the
 * page must rewrite the URL the moment a later poll names the child — the same
 * move `/live` makes (`LiveSessionView`'s canonicalising watcher).
 *
 * That "later poll" is the whole point: a canonical id delivered only on the
 * first response would exercise the reload/bookmark path, not the launch one.
 * The stub below withholds it from the first `/map` for exactly that reason.
 *
 * Browser-side contract ONLY: alias resolution itself is pinned in Python
 * (lib/trace/alias.py). `canonical_trace_id` here is a premise.
 */
import { test, expect } from './auth-fixture.js'
import { randomUUID } from 'node:crypto'

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

async function postSession(page, marker) {
  const traceId = randomUUID()
  const sfx = traceId.slice(0, 8)
  const now = new Date().toISOString()
  await page.request.post('/api/session-spans', {
    data: [
      { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null,
        name: 'prompt', start_time: now,
        attributes: { text: marker, is_test: true } },
      { trace_id: traceId, span_id: `resp-${sfx}`, parent_id: null,
        name: 'assistant_response', start_time: now,
        attributes: { text: `${marker} answered`, is_test: true } },
    ],
  })
  return traceId
}

const sdkId = () => `sdk-${randomUUID().replace(/-/g, '').slice(0, 12)}`

// Serve `childId`'s real data under `sdkId`, as the server does for a
// regin-launched run: the requested id echoed as `trace_id`, the child named
// as canonical — but only from the SECOND `/map` on, which is when the child
// has registered its alias. `extra` rides along with the canonical id.
async function stubSdkAlias(page, sdk, childId, extra = {}) {
  let maps = 0
  await page.route(`**/api/sessions/${sdk}/**`, async (route) => {
    const url = route.request().url().replaceAll(sdk, childId)
    const resp = await route.fetch({ url })
    const text = await resp.text()
    let json
    try { json = JSON.parse(text) } catch { return route.fulfill({ status: 200, body: text }) }
    const isMap = route.request().url().includes('/map')
    const named = !isMap || ++maps > 1
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      json: {
        ...json,
        trace_id: sdk,
        canonical_trace_id: named ? childId : sdk,
        ...(named ? extra : {}),
      },
    })
  })
}

test('a run launched onto its sdk- id renames onto the child once the poll names it',
  async ({ page }) => {
    const child = await postSession(page, 'CANONICAL_CHILD_MARKER')
    const sdk = sdkId()
    await stubSdkAlias(page, sdk, child)

    await page.goto(`/trace/sessions/${sdk}`)
    // The first /map still names the sdk id, so the page must stay put on it…
    await expect(page).toHaveURL(new RegExp(`/trace/sessions/${sdk}$`))
    // …and move only when a later poll reports the child.
    await expect(page).toHaveURL(new RegExp(`/trace/sessions/${child}$`), { timeout: 30_000 })
  })

test('the rename keeps the query string, so a deep link survives it', async ({ page }) => {
  const child = await postSession(page, 'CANONICAL_QUERY_MARKER')
  const sdk = sdkId()
  await stubSdkAlias(page, sdk, child)

  await page.goto(`/trace/sessions/${sdk}?view=conversation`)

  await expect(page).toHaveURL(
    new RegExp(`/trace/sessions/${child}\\?view=conversation$`), { timeout: 30_000 })
})

// Both watchers read the same summary and can fire in one flush, where
// `route.params.id` has not moved yet — so both guards pass and the landing
// page would fall to declaration order. The successor is the one that must
// win: it names the conversation that is actually running.
test('a successor naming a DIFFERENT session outranks the rename', async ({ page }) => {
  const child = await postSession(page, 'CANONICAL_HEIR_MARKER')
  const heir = await postSession(page, 'CANONICAL_HEIR_SUCCESSOR_MARKER')
  const sdk = sdkId()
  await stubSdkAlias(page, sdk, child, { successor_trace_id: heir })

  await page.goto(`/trace/sessions/${sdk}`)

  await expect(page).toHaveURL(new RegExp(`/trace/sessions/${heir}$`), { timeout: 30_000 })
  // Long enough for several more poll ticks: the rename must not drag the
  // page back off the heir onto the canonical id it stood down for.
  await page.waitForTimeout(10_000)
  await expect(page).toHaveURL(new RegExp(`/trace/sessions/${heir}$`))
})

test('an ordinary session is never renamed', async ({ page }) => {
  const plain = await postSession(page, 'ORDINARY_SESSION_MARKER')

  await page.goto(`/trace/sessions/${plain}`)
  await expect(page.getByText('ORDINARY_SESSION_MARKER').first()).toBeVisible({ timeout: 15_000 })
  await page.waitForTimeout(15_000)

  await expect(page).toHaveURL(new RegExp(`/trace/sessions/${plain}$`))
})
