/**
 * Presentation mode over a span the live poll RETIRES underneath it.
 *
 * The serve-time merge drops rows the client is already showing, and the
 * reported bug is the worst case of it: `_drop_stale_blockers`
 * (lib/trace/merge.py) retires every still-PENDING blocker of the turn the
 * moment a NEWER prompt anchor lands — so sending a follow-up from the reader
 * is itself what retires the span the reader is open on. `reloadLiveTail`
 * prunes it from `session.spans` a poll later, and the reader — which used to
 * resolve its open span by id against that list — dismissed itself mid-read.
 *
 * Both retirements here are driven the way production drives them: by writing
 * the span that supersedes the placeholder and letting the merge do the rest.
 * Nothing is faked into the response, so a change to the merge's retirement
 * rules moves these tests instead of leaving them passing against a fixture.
 */
import { createHash, randomUUID } from 'node:crypto'
import { test, expect } from './auth-fixture.js'

const at = i => `2026-05-08T12:00:${String(i).padStart(2, '0')}`
const TOOL_USE_ID = 'toolu_01retiredspan99'
const RUNNING = 'streaming build output while the call is still open'
const RESOLVED = 'the call finished and this is what it returned'

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

async function seed(page) {
  const traceId = randomUUID()
  const p8 = traceId.slice(0, 8)
  const prompt = `p-${p8}`
  const post = async (spans) => {
    const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
    expect((await page.request.post('/api/session-spans', {
      headers: { Authorization: `Bearer ${token}` }, data: spans,
    })).ok()).toBeTruthy()
  }

  await page.goto('/trace/sessions')
  await post([
    { trace_id: traceId, span_id: prompt, parent_id: null, name: 'prompt',
      start_time: at(0), attributes: { text: 'Run the build.', is_test: true } },
    // A blocking call is served under its reserved `pending-<tool_use_id[:13]>`
    // id while it runs (lib/trace/pending_spans.py) — the shape the reader is
    // most likely to be open on in a live session.
    { trace_id: traceId, span_id: `pending-${TOOL_USE_ID.slice(0, 13)}`,
      parent_id: prompt, name: 'tool.Bash', start_time: at(1), status_code: 'PENDING',
      attributes: { tool_name: 'Bash', command: 'npm run build', stdout: RUNNING, is_test: true } },
  ])

  // The session must read as live or the poll stops and nothing is ever
  // pruned; a real bridge pane is not something an E2E run can stand up.
  await page.route(`**/api/sessions/${traceId}/map*`, async (route) => {
    const res = await route.fetch()
    const body = await res.json()
    body.ended_at = null
    body.bridge_reachable = true
    await route.fulfill({ response: res, json: body })
  })

  await page.goto(`/trace/sessions/${traceId}?view=conversation`)
  await expect(page.locator('.event-spine')).toBeVisible({ timeout: 20_000 })
  return { traceId, p8, prompt, post }
}

const reader = (page) => page.getByTestId('span-present-reader')

async function openOnPending(page) {
  await page.locator('.event-spine-row').filter({ hasText: 'Shell' }).first()
    .locator('.present-open').click()
  await expect(reader(page)).toBeVisible()
  await expect(reader(page)).toContainText(RUNNING)
}

test('a follow-up retires the blocker being read without closing the reader', async ({ page }) => {
  test.setTimeout(120_000)
  const { traceId, p8, post } = await seed(page)
  await openOnPending(page)

  // What Reply does: a newer prompt anchor lands, and every PENDING blocker of
  // the prior turn is retired with it.
  await post([{ trace_id: traceId, span_id: `p2-${p8}`, parent_id: null, name: 'prompt',
    start_time: at(5), attributes: { text: 'Also bump the timeout.', is_test: true } }])

  // The assertion is that nothing happens, so wait on the EVENT it denies —
  // the prune actually landing, which the feed shows by losing the row — and
  // not on a clock.
  await expect(page.locator('.event-spine-row').filter({ hasText: 'Shell' }))
    .toHaveCount(0, { timeout: 60_000 })

  // Still open, still showing the span that was being read.
  await expect(reader(page)).toBeVisible()
  await expect(reader(page)).toContainText(RUNNING)
  // …and a real reader, not a husk: Escape still closes it.
  await page.keyboard.press('Escape')
  await expect(reader(page)).toHaveCount(0)
})

test('a retired pending call re-binds to its resolved twin, keeping the walk', async ({ page }) => {
  test.setTimeout(120_000)
  const { traceId, p8, prompt, post } = await seed(page)
  await openOnPending(page)

  // The call resolves: the merge retires `pending-<tu>` in favour of the row
  // carrying that `tool_use_id`, which is the successor the reader re-binds to.
  await post([{ trace_id: traceId, span_id: `done-${p8}`, parent_id: prompt,
    name: 'tool.Bash', start_time: at(2), duration_ms: 4100, status_code: 'OK',
    attributes: {
      tool_name: 'Bash', command: 'npm run build', stdout: RESOLVED,
      tool_use_id: TOOL_USE_ID, is_test: true,
    } }])

  await expect(reader(page)).toContainText(RESOLVED, { timeout: 60_000 })
  await expect(reader(page)).not.toContainText(RUNNING)
  // Re-bound onto the walk, not stranded off it: the position counter reads a
  // real slot, so ↑/↓ still work from here.
  await expect(reader(page).locator('.present-count')).not.toHaveText(/^0\b/)
  await expect(reader(page)).toContainText('npm run build')
})

/**
 * The prompt half of the same retirement: a live prompt is served under
 * `promptlive-<sha1(session + text[:512])>` until the transcript scan lands its
 * real `prompt-<uuid>` anchor, and the merge then drops the placeholder. The
 * reader's handoff key for that pair is the TEXT (the server hashes it; the
 * client cannot recompute the digest), which is why the id-prefix successor
 * lookup used to find nothing here and strand the reader on a dead placeholder.
 */
const FOLLOWUP = 'Now also regenerate the design manifest and report the delta.'

const promptliveId = (traceId, text) => (
  `promptlive-${createHash('sha1').update(`${traceId}\x00${text.trim().slice(0, 512)}`)
    .digest('hex').slice(0, 13)}`
)

async function seedLivePrompt(page) {
  const { traceId, p8, post } = await seed(page)
  // The agent answered the first turn, so the placeholder has a predecessor on
  // the walk — the slot the reader must be able to resume from.
  await post([{ trace_id: traceId, span_id: `resp-${p8}`, parent_id: `p-${p8}`,
    name: 'assistant_response', start_time: at(3), status_code: 'OK',
    attributes: { text: 'Build is green.', is_test: true } }])
  await post([{ trace_id: traceId, span_id: promptliveId(traceId, FOLLOWUP),
    parent_id: null, name: 'prompt', start_time: at(6), status_code: 'PENDING',
    attributes: { text: FOLLOWUP, chars: FOLLOWUP.length, live_placeholder: true, is_test: true } }])
  const card = page.locator(`[data-span-id="${promptliveId(traceId, FOLLOWUP)}"]`)
  await expect(card).toBeVisible({ timeout: 60_000 })
  await card.locator('.present-open').click()
  await expect(reader(page)).toBeVisible()
  await expect(reader(page)).toContainText(FOLLOWUP)
  return { traceId, p8, post }
}

test('a retired promptlive- re-binds to its promoted anchor, keeping the walk', async ({ page }) => {
  test.setTimeout(120_000)
  const { traceId, p8, post } = await seedLivePrompt(page)

  // The transcript scan lands the real anchor for the same text; the merge
  // retires the placeholder on that alone.
  await post([{ trace_id: traceId, span_id: `prompt-${p8}-live`, parent_id: null,
    name: 'prompt', start_time: at(6), status_code: 'OK',
    attributes: { text: FOLLOWUP, entry_uuid: `eu-${p8}`, is_test: true } }])

  // Wait on the EVENT, not a clock: the placeholder card leaving the feed is
  // the retirement actually landing. Without it the rest asserts nothing.
  await expect(page.locator(`[data-span-id="${promptliveId(traceId, FOLLOWUP)}"]`))
    .toHaveCount(0, { timeout: 60_000 })

  // Still open on the same document — and back on the walk, so the counter
  // names a real slot and both steps work.
  await expect(reader(page)).toBeVisible()
  await expect(reader(page)).toContainText(FOLLOWUP)
  // A real slot, not a stranded reader: without the re-bind the counter has no
  // position to name and renders nothing at all, so this must be a POSITIVE
  // match — `not.toContainText` on an absent element passes vacuously.
  await expect(reader(page).locator('.present-count')).toHaveText(/^\d+ \/ \d+$/, { timeout: 60_000 })
  // Both steps live again, which is what "back on the walk" has to mean.
  await expect(reader(page).locator('.present-step').first()).toBeEnabled()
})

test('a retired span with no heir still walks: ArrowDown reaches what landed after', async ({ page }) => {
  test.setTimeout(120_000)
  const { traceId, p8, post } = await seed(page)
  await openOnPending(page)

  // `_drop_stale_blockers` retires the open blocker when a newer prompt lands,
  // and that placeholder names no successor — the held copy stands. The walk
  // must still resume from where it sat, or the only way to the newer spans is
  // to close the reader and re-open it (the reported defect).
  await post([{ trace_id: traceId, span_id: `p2-${p8}`, parent_id: null, name: 'prompt',
    start_time: at(5), attributes: { text: 'Also bump the timeout.', is_test: true } }])
  await expect(page.locator('.event-spine-row').filter({ hasText: 'Shell' }))
    .toHaveCount(0, { timeout: 60_000 })

  // Off the walk, and the footer says so rather than reading a 0 slot.
  await expect(reader(page).locator('.present-count')).toContainText('off-list')
  // ↓ from a span that left the walk lands on what now follows its slot.
  await expect(reader(page).locator('.present-step').last()).toBeEnabled()
  await page.keyboard.press('ArrowDown')
  await expect(reader(page)).toContainText('Also bump the timeout.')
  await expect(reader(page).locator('.present-count')).not.toContainText('off-list')
  // …and back the other way, onto the turn the reader started in.
  await page.keyboard.press('ArrowUp')
  await expect(reader(page)).toContainText('Run the build.')
})
