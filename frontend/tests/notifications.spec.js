/**
 * Notification management: tiering, live SSE delivery, the blocker banner,
 * suppression, and badge urgency.
 *
 * Messages are written with `lib.agent_messages.store.record_message` in a
 * SEPARATE python process — deliberately, because that is the real ingest
 * path: a PostToolUse hook writes the row in its own interpreter and pings the
 * dashboard over the loopback trigger, which fans the row out to every open
 * stream. Posting through an API route from inside the browser would exercise
 * the in-process branch only and prove nothing about the path that actually
 * carries an agent's message.
 *
 * The rows must be real (`is_test=True` rows are excluded from the badge and
 * are never pushed to a surface — that is asserted below), so every test
 * dismisses what it created.
 */
import { execFileSync } from 'node:child_process'
import { randomUUID } from 'node:crypto'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, test } from './auth-fixture.js'

// Serial, and not by accident: the stream is a broadcast to every open
// tab, so a message one test records is delivered to every other test's
// page too. Run them concurrently and a sibling's blocker replaces the one
// under assertion — a property of the feature, not a flake to paper over.
test.describe.configure({ mode: 'serial' })

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..')
const PY = resolve(ROOT, '.venv/bin/python')

// Written from the repo root so `lib.` imports resolve and the live DB is the
// one `regin serve` is holding open.
function record({ type, title, body, traceId, key = null, isTest = false }) {
  const script = `
import json, sys
sys.path.insert(0, ".")
from lib.agent_messages import store
row = store.record_message(
    trace_id=${JSON.stringify(traceId)}, body=${JSON.stringify(body)},
    msg_type=${JSON.stringify(type)}, title=${JSON.stringify(title)},
    msg_key=${key === null ? 'None' : JSON.stringify(key)},
    is_test=${isTest ? 'True' : 'False'}, dispatch_webhook=False)
print(json.dumps({"id": row["id"]}))
`
  const out = execFileSync(PY, ['-c', script], { cwd: ROOT, encoding: 'utf8' })
  return JSON.parse(out.trim().split('\n').pop()).id
}

// The terminal-side resolve: `events.resolve` dismisses the keyed card in the
// hook's own process, which reaches an open banner only through the loopback
// trigger. Doing it over the HTTP API instead would take the in-process branch.
function resolveKeyed(traceId, key) {
  const script = `
import sys
sys.path.insert(0, ".")
from lib.agent_messages import store
print(store.dismiss_keyed(${JSON.stringify(traceId)}, ${JSON.stringify(key)}))
`
  const out = execFileSync(PY, ['-c', script], { cwd: ROOT, encoding: 'utf8' })
  return Number(out.trim().split('\n').pop())
}

function cleanup(page, ids) {
  return Promise.all(ids.map(id => page.request.post(`/api/agent-messages/${id}/dismiss`)))
}

// The stream is opened after the ticket round-trip, so a message recorded the
// instant the page loads can land before anyone is listening.
async function streamReady(page) {
  await page.waitForFunction(() => !!localStorage.getItem('regin_auth_token'))
  await page.waitForTimeout(900)
}

test.describe('notification tiers', () => {
  test('a tier-2 message toasts without a reload', async ({ page }) => {
    const ids = []
    const title = `Toast me ${randomUUID().slice(0, 8)}`
    await page.goto('/trace/sessions')
    await streamReady(page)

    ids.push(record({
      type: 'warning', title, body: 'Context at 89% of the window.',
      traceId: `e2e-${randomUUID()}`,
    }))

    // No reload, no poll: the only way this text can appear is the stream.
    await expect(page.getByText(title)).toBeVisible({ timeout: 10_000 })
    await expect(page.getByRole('button', { name: 'Open in trace' })).toBeVisible()
    await cleanup(page, ids)
  })

  test('a tier-3 message moves the badge but never pops', async ({ page }) => {
    const ids = []
    const title = `Quiet ${randomUUID().slice(0, 8)}`
    await page.goto('/trace/sessions')
    await streamReady(page)

    const before = await page.locator('.sb-badge').first().textContent().catch(() => null)
    ids.push(record({
      type: 'progress', title, body: 'Step 3 of 7 done.',
      traceId: `e2e-${randomUUID()}`,
    }))

    // The badge is the only surface a count-only message is allowed to touch.
    await expect.poll(async () => {
      const now = await page.locator('.sb-badge').first().textContent().catch(() => null)
      return now !== before
    }, { timeout: 10_000 }).toBe(true)
    await expect(page.getByText(title)).toHaveCount(0)
    await cleanup(page, ids)
  })

  test('a test row reaches no surface at all', async ({ page }) => {
    const ids = []
    const title = `Synthetic ${randomUUID().slice(0, 8)}`
    await page.goto('/trace/sessions')
    await streamReady(page)

    ids.push(record({
      type: 'warning', title, body: 'Should stay invisible.',
      traceId: `e2e-${randomUUID()}`, isTest: true,
    }))
    await page.waitForTimeout(2_000)
    await expect(page.getByText(title)).toHaveCount(0)
    await cleanup(page, ids)
  })
})

test.describe('blocker banner', () => {
  // Unique per run so a row left behind by an earlier failure can never be
  // mistaken for the one this test just raised.
  const QUESTION = `How far should Close/Delete go? ${randomUUID().slice(0, 8)}`
  const BODY = `${QUESTION}\n• Escape only, then warn\n• Escape, then type /exit\n• Detect + report only`

  test('interrupts, parses its options, and snoozes to a strip', async ({ page }) => {
    const ids = []
    await page.goto('/trace/sessions')
    await streamReady(page)

    ids.push(record({
      type: 'blocker', title: 'The agent is asking you a question', body: BODY,
      traceId: `e2e-${randomUUID()}`, key: 'permission-pending',
    }))

    const banner = page.getByRole('alert').filter({ hasText: QUESTION })
    await expect(banner).toBeVisible({ timeout: 10_000 })

    // Options come out of the body as separate rows, not one blob of text.
    await expect(banner.getByText('Escape only, then warn')).toBeVisible()
    await expect(banner.getByText('Detect + report only')).toBeVisible()
    await expect(banner.getByRole('button', { name: /Answer in live session/ })).toBeVisible()

    // "Later" is a snooze, not a close: the agent is still parked, so the
    // collapsed strip has to keep saying so.
    await banner.getByRole('button', { name: /Later/ }).click()
    await expect(page.getByText('1 decision waiting')).toBeVisible()
    await expect(page.getByText(QUESTION)).toHaveCount(0)

    await page.getByRole('button', { name: 'Answer', exact: true }).click()
    await expect(page.getByText(QUESTION)).toBeVisible()
    await cleanup(page, ids)
  })

  test('the badge turns red while an agent is parked', async ({ page }) => {
    const ids = []
    await page.goto('/trace/sessions')
    await streamReady(page)

    ids.push(record({
      type: 'blocker', title: 'Question', body: BODY,
      traceId: `e2e-${randomUUID()}`, key: 'permission-pending',
    }))
    await expect(page.getByText(QUESTION)).toBeVisible({ timeout: 10_000 })
    // Colour comes from the counts frame, not a per-tab guess, so it agrees
    // with what the server thinks is unread.
    await expect(page.locator('.sb-badge-danger')).toHaveCount(1)
    await cleanup(page, ids)
  })

  // A blocker whose banner you dismissed still has to be findable, which is
  // what the flag on the session row is for. Needs a session that actually
  // exists in the list, so one is posted first.
  test('flags the parked row, then marks it resumed', async ({ page }) => {
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    const now = new Date().toISOString()
    const res = await page.request.post('/api/session-spans', {
      data: [{
        trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null,
        name: 'prompt', start_time: now,
        attributes: { text: `NOTIF_FIXTURE_${sfx}`, is_test: true },
      }],
    })
    expect(res.ok()).toBeTruthy()

    // The list defaults to `kind: 'real'`, which hides the synthetic session
    // this test just posted. The filter is persisted in localStorage.
    await page.addInitScript(() => localStorage.setItem('regin_sessions_kind', 'all'))
    await page.goto('/trace/sessions')
    await streamReady(page)
    // Two matches by design: the view renders a desktop row and a phone card,
    // and CSS picks. `.srow*` below is the desktop one.
    await expect(page.getByText(`NOTIF_FIXTURE_${sfx}`).first())
      .toBeVisible({ timeout: 10_000 })

    record({ type: 'blocker', title: 'Question', body: BODY, traceId,
      key: 'permission-pending' })

    const row = page.locator('.srow--awaiting')
    await expect(row).toHaveCount(1, { timeout: 10_000 })
    await expect(row.getByText(/awaiting decision/)).toBeVisible()

    // Answered in the terminal: the hook dismisses the keyed card out of
    // process, and both surfaces must settle with no interaction here.
    expect(resolveKeyed(traceId, 'permission-pending')).toBe(1)
    await expect(page.getByText(QUESTION)).toHaveCount(0, { timeout: 10_000 })
    await expect(page.locator('.srow--awaiting')).toHaveCount(0)
    await expect(page.locator('.srow__resumed')).toHaveCount(1)
  })
})

test.describe('reading is not answering', () => {
  const QUESTION = `Still parked? ${randomUUID().slice(0, 8)}`

  // The regression this guards: `mark all read` broadcast `{all: true}`, the
  // client retired everything it matched, and a blocker whose agent was still
  // stopped vanished from every tab — permanently, since the row was now read
  // and could not be re-hydrated. `useLiveDecisions.js` records the same trap
  // being fixed once before on the inbox's own decision surfaces.
  test('mark-all-read clears toasts but leaves a live blocker up', async ({ page }) => {
    const toastTitle = `Readable ${randomUUID().slice(0, 8)}`
    await page.goto('/trace/sessions')
    await streamReady(page)

    record({ type: 'blocker', title: 'Question', body: QUESTION,
      traceId: `e2e-${randomUUID()}`, key: 'permission-pending' })
    record({ type: 'warning', title: toastTitle, body: 'Folds away on read.',
      traceId: `e2e-${randomUUID()}` })

    await expect(page.getByText(QUESTION)).toBeVisible({ timeout: 10_000 })
    await expect(page.getByText(toastTitle)).toBeVisible()

    const res = await page.evaluate(async () => {
      const r = await fetch('/api/agent-messages/read-all', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${localStorage.getItem('regin_auth_token')}`,
        },
        body: '{}',
      })
      return r.status
    })
    expect(res).toBe(200)

    // The acknowledged toast goes; the parked agent's question stays.
    await expect(page.getByText(toastTitle)).toHaveCount(0, { timeout: 10_000 })
    await expect(page.getByText(QUESTION)).toBeVisible()
    // …and nothing claims the session resumed, because nothing did.
    await expect(page.locator('.srow__resumed')).toHaveCount(0)
  })
})

test.describe('hiding a blocker card is not answering it', () => {
  // The inbox's Dismiss button is the only escape from a banner whose session
  // died mid-prompt, so it must close the surface — but it says nothing about
  // the agent, so it must NOT paint the row "resumed" the way a real resolve
  // does. Same trap as mark-all-read, different button.
  test('dismiss closes the banner without claiming the session resumed',
    async ({ page }) => {
      const question = `Dismissible ${randomUUID().slice(0, 8)}`
      const traceId = randomUUID()
      const sfx = traceId.slice(0, 8)
      await page.request.post('/api/session-spans', {
        data: [{
          trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null,
          name: 'prompt', start_time: new Date().toISOString(),
          attributes: { text: `NOTIF_DISMISS_${sfx}`, is_test: true },
        }],
      })
      await page.addInitScript(() => localStorage.setItem('regin_sessions_kind', 'all'))
      await page.goto('/trace/sessions')
      await streamReady(page)

      const id = record({ type: 'blocker', title: 'Question', body: question,
        traceId, key: 'permission-pending' })
      await expect(page.getByText(question)).toBeVisible({ timeout: 10_000 })
      await expect(page.locator('.srow--awaiting')).toHaveCount(1)

      const status = await page.evaluate(async (messageId) => {
        const r = await fetch(`/api/agent-messages/${messageId}/dismiss`, {
          method: 'POST',
          headers: { Authorization: `Bearer ${localStorage.getItem('regin_auth_token')}` },
        })
        return r.status
      }, id)
      expect(status).toBe(200)

      await expect(page.getByText(question)).toHaveCount(0, { timeout: 10_000 })
      await expect(page.locator('.srow--awaiting')).toHaveCount(0)
      // The agent was never un-parked, so nothing may say it resumed.
      await expect(page.locator('.srow__resumed')).toHaveCount(0)
    })
})

test.describe('unknown retire reasons fail closed', () => {
  // The loopback trigger re-broadcasts its body verbatim, so the client is the
  // only place that decides what a `resolved` frame is allowed to do. A frame
  // from a producer this build does not understand must retire NOTHING —
  // guessing is how a stopped agent's banner goes missing.
  test('a reason-less resolve retires neither toast nor banner', async ({ page }) => {
    const question = `Unknown reason ${randomUUID().slice(0, 8)}`
    const toastTitle = `Survivor ${randomUUID().slice(0, 8)}`
    const traceId = `e2e-${randomUUID()}`
    await page.goto('/trace/sessions')
    await streamReady(page)

    record({ type: 'blocker', title: 'Question', body: question, traceId,
      key: 'permission-pending' })
    record({ type: 'warning', title: toastTitle, body: 'Should survive.',
      traceId: `e2e-${randomUUID()}` })
    await expect(page.getByText(question)).toBeVisible({ timeout: 10_000 })
    await expect(page.getByText(toastTitle)).toBeVisible()

    // Straight at the loopback trigger — no reason, and a reason this build
    // has never heard of. Neither may retire anything.
    for (const resolved of [{ all: true }, { all: true, reason: 'whatever' }]) {
      // The body is handed over as a JSON *string* — a JS object literal
      // inlined into python source would emit `true`, which python cannot read.
      const body = JSON.stringify(JSON.stringify({ resolved }))
      execFileSync(PY, ['-c', `
import sys, urllib.request
sys.path.insert(0, ".")
from lib.settings import settings
req = urllib.request.Request(
    f"http://127.0.0.1:{settings.web_port}/api/internal/notify",
    data=${body}.encode(),
    method="POST", headers={"Content-Type": "application/json"})
urllib.request.urlopen(req, timeout=2).read()
`], { cwd: ROOT })
    }
    await page.waitForTimeout(2_500)
    await expect(page.getByText(question)).toBeVisible()
    await expect(page.getByText(toastTitle)).toBeVisible()
  })
})

test.describe('suppression', () => {
  test('the inbox does not pop over itself', async ({ page }) => {
    const ids = []
    const title = `Suppressed ${randomUUID().slice(0, 8)}`
    await page.goto('/inbox')
    await streamReady(page)

    ids.push(record({
      type: 'warning', title, body: 'Nothing should pop here.',
      traceId: `e2e-${randomUUID()}`,
    }))

    // The row itself may appear in the feed; what must NOT appear is a toast.
    await page.waitForTimeout(2_500)
    await expect(page.locator('.toast')).toHaveCount(0)
    await cleanup(page, ids)
  })
})
