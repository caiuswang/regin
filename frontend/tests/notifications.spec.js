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
//
// A decision-keyed record also seeds the PENDING park span (and a live
// sessions row) behind it: the blocker feed derives presence from the parked
// state, so a card row alone is exactly the stray the feed exists to ignore.
function record({ type, title, body, traceId, key = null, isTest = false }) {
  const script = `
import json, sys, uuid
sys.path.insert(0, ".")
from lib.agent_messages import store
if ${key === null ? 'False' : 'True'}:
    # Park FIRST, notify second — the same ordering invariant the real
    # writers hold, or the frame's feed re-read races an absent park.
    from datetime import datetime
    from lib.orm.engine import get_connection
    now = datetime.now().isoformat()
    conn = get_connection()
    conn.execute(
        "INSERT INTO sessions (trace_id, started_at, last_seen, status) "
        "VALUES (?, ?, ?, 'active') "
        "ON CONFLICT(trace_id) DO UPDATE SET last_seen = excluded.last_seen",
        (${JSON.stringify(traceId)}, now, now))
    conn.execute(
        "INSERT OR REPLACE INTO session_spans "
        "(trace_id, span_id, name, kind, start_time, status_code, attributes) "
        "VALUES (?, ?, 'permission.request', 'internal', ?, 'PENDING', ?)",
        (${JSON.stringify(traceId)}, "permreq-e2e-%s" % uuid.uuid4().hex[:8],
         now, json.dumps({"is_test": True})))
    conn.commit()
    conn.close()
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

// Through the page, not `page.request`: the API is Bearer-authed from
// localStorage, which an APIRequestContext does not carry — so the old form
// 401'd silently, left every row it "cleaned" parked in the live DB, and only
// became visible once the banner started showing all of them at once.
function cleanup(page, ids) {
  return page.evaluate(async (messageIds) => {
    const token = localStorage.getItem('regin_auth_token')
    for (const id of messageIds) {
      const r = await fetch(`/api/agent-messages/${id}/dismiss`, {
        method: 'POST', headers: { Authorization: `Bearer ${token}` },
      })
      if (!r.ok) throw new Error(`dismiss ${id} → ${r.status}`)
    }
  }, ids)
}

// The banner now pages through EVERY parked decision, and the feed is the live
// DB — a blocker a previous run (or a real session on this machine) left
// waiting would occupy page 1 and shift this test's own card behind it. Start
// each blocker test owning the banner.
function clearBlockers() {
  const script = `
import sys
sys.path.insert(0, ".")
from lib.agent_messages import blockers, store
for b in blockers.live_blockers():
    # A derived card with no row (a genuine park on this machine) has no id
    # and cannot be dismissed from here — leave it; the serial suite owns
    # the banner only against rows earlier runs left behind.
    if b["id"] is not None:
        store.dismiss(b["id"])
print("ok")
`
  execFileSync(PY, ['-c', script], { cwd: ROOT, encoding: 'utf8' })
}

// Naive LOCAL time, the format every real span writer stamps. `toISOString()`
// emits UTC-with-Z, which string-sorts hours away from the local-naive rows —
// a session stamped with it sinks below a page of real sessions and the list
// assertions go blind to it.
function localStamp() {
  const tzOffsetMs = new Date().getTimezoneOffset() * 60_000
  return new Date(Date.now() - tzOffsetMs).toISOString().slice(0, -1)
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

  test('interrupts, parses its options, and folds to a strip', async ({ page }) => {
    const ids = []
    // The banner raises an optimistic row off the SSE frame and then enriches
    // it from /blockers. A throw anywhere in that path leaves the un-enriched
    // row on screen — title instead of question, no options — which every
    // other assertion here is too coarse to notice.
    const failures = []
    page.on('pageerror', e => failures.push(String(e)))
    page.on('console', m => { if (m.type() === 'error') failures.push(m.text()) })
    clearBlockers()
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
    await expect(banner.getByRole('button', { name: /Open live session/ })).toBeVisible()

    // "Fold" collapses, it does not close: the agent is still parked, so the
    // collapsed strip has to keep saying so.
    await banner.getByRole('button', { name: 'Fold', exact: true }).click()
    await expect(page.getByText('1 decision waiting')).toBeVisible()
    await expect(page.getByText(QUESTION)).toHaveCount(0)

    await page.getByRole('button', { name: 'Answer', exact: true }).click()
    await expect(page.getByText(QUESTION)).toBeVisible()
    expect(failures).toEqual([])
    await cleanup(page, ids)
  })

  // Dismiss is not fold: no strip may remain, and a reload must not
  // resurrect the card — `dismissed_at` is server truth, not tab state.
  test('dismiss retires the decision for good', async ({ page }) => {
    const ids = []
    clearBlockers()
    await page.goto('/trace/sessions')
    await streamReady(page)

    ids.push(record({
      type: 'blocker', title: 'The agent is asking you a question', body: BODY,
      traceId: `e2e-${randomUUID()}`, key: 'permission-pending',
    }))
    const banner = page.getByRole('alert').filter({ hasText: QUESTION })
    await expect(banner).toBeVisible({ timeout: 10_000 })

    await banner.getByRole('button', { name: /Dismiss/ }).click()
    await expect(page.getByRole('alert')).toHaveCount(0)
    await expect(page.getByText('1 decision waiting')).toHaveCount(0)

    await page.reload()
    await streamReady(page)
    await expect(page.getByText(QUESTION)).toHaveCount(0)
    await cleanup(page, ids)
  })

  test('the badge turns red while an agent is parked', async ({ page }) => {
    const ids = []
    clearBlockers()
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
    clearBlockers()
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    const now = localStamp()
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
    clearBlockers()
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
      clearBlockers()
      const question = `Dismissible ${randomUUID().slice(0, 8)}`
      const traceId = randomUUID()
      const sfx = traceId.slice(0, 8)
      await page.request.post('/api/session-spans', {
        data: [{
          trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null,
          name: 'prompt', start_time: localStamp(),
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
    clearBlockers()
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

// ── The defects this feature shipped with ────────────────────────────

test.describe('a blocker survives a reload', () => {
  // The bug: `hydrate()` fetched `/api/agent-messages?unread_only=true`, a
  // route that does not exist. The 404 was swallowed, so hydration never
  // returned a row and the banner existed only for as long as the tab that
  // received the SSE frame. Reload, and a stopped agent had no surface at all.
  test('a reload re-raises the banner, and reading it does not', async ({ page }) => {
    clearBlockers()
    const question = `Survives reload ${randomUUID().slice(0, 8)}`
    await page.goto('/trace/sessions')
    await streamReady(page)

    const id = record({ type: 'blocker', title: 'Question', body: question,
      traceId: `e2e-${randomUUID()}`, key: 'permission-pending' })
    await expect(page.getByText(question)).toBeVisible({ timeout: 10_000 })

    await page.reload()
    await expect(page.getByText(question)).toBeVisible({ timeout: 10_000 })

    // Read is not answered: the feed behind the banner is gated on dismissal,
    // never on `read_at`, so acknowledging it must not lose it across a reload.
    await page.evaluate(async () => {
      await fetch('/api/agent-messages/read-all', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${localStorage.getItem('regin_auth_token')}`,
        },
        body: JSON.stringify({}),
      })
    })
    await page.reload()
    await expect(page.getByText(question)).toBeVisible({ timeout: 10_000 })
    await cleanup(page, [id])
  })

  // The bug: the row highlight was driven by the one blocker the banner held,
  // which only ever arrived over the stream — so a list opened AFTER the agent
  // parked showed nothing, and the operator had no way to find it.
  test('a list opened after the agent parked still flags the row', async ({ page }) => {
    clearBlockers()
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    await page.request.post('/api/session-spans', {
      data: [{
        trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null,
        name: 'prompt', start_time: localStamp(),
        attributes: { text: `NOTIF_LATE_${sfx}`, is_test: true },
      }],
    })
    // Recorded with no page open at all — nothing can have received a frame.
    const id = record({ type: 'blocker', title: 'Question', body: `Late ${sfx}`,
      traceId, key: 'permission-pending' })

    await page.addInitScript(() => localStorage.setItem('regin_sessions_kind', 'all'))
    await page.goto('/trace/sessions')
    await expect(page.locator('.srow--awaiting')).toHaveCount(1, { timeout: 10_000 })
    await cleanup(page, [id])
  })
})

test.describe('several agents parked at once', () => {
  // The bug: one slot held one blocker, so agent 3's question silently
  // replaced agents 1-2's with no sign the others were ever there.
  test('pages through every waiting decision', async ({ page }) => {
    clearBlockers()
    const ids = []
    const tag = randomUUID().slice(0, 8)
    await page.goto('/trace/sessions')
    await streamReady(page)

    for (let i = 0; i < 3; i += 1) {
      ids.push(record({
        type: 'blocker', title: 'Question', body: `Decision ${i} of ${tag}`,
        traceId: `e2e-${randomUUID()}`, key: 'permission-pending',
      }))
    }

    const banner = page.getByRole('alert')
    await expect(banner.getByText('Decision 1 of 3')).toBeVisible({ timeout: 10_000 })
    await expect(page.getByText(`Decision 0 of ${tag}`)).toBeVisible()

    await banner.getByRole('button', { name: 'Next decision' }).click()
    await expect(banner.getByText('Decision 2 of 3')).toBeVisible()
    await expect(page.getByText(`Decision 1 of ${tag}`)).toBeVisible()

    // Wraps rather than dead-ending, so a three-item pager needs no bounds UI.
    await banner.getByRole('button', { name: 'Next decision' }).click()
    await banner.getByRole('button', { name: 'Next decision' }).click()
    await expect(banner.getByText('Decision 1 of 3')).toBeVisible()

    // Folded, the strip has to account for all of them, not just the one on
    // screen — it is the only thing left saying anything is waiting.
    await banner.getByRole('button', { name: 'Fold', exact: true }).click()
    await expect(page.getByText('3 decisions waiting')).toBeVisible()

    await cleanup(page, ids)
    await expect(page.getByRole('alert')).toHaveCount(0, { timeout: 10_000 })
  })
})

test.describe('answering from anywhere', () => {
  // The complaint: the banner could only ever say "go to /live and answer
  // there". It now drives the SAME endpoint the /live sheet does, from
  // whatever page the operator is on.
  //
  // The feed is stubbed rather than seeded: whether a session is answerable
  // depends on a live tmux pane or an SDK-owned process, neither of which a
  // test can conjure. What is unproven — and asserted here — is the CLIENT
  // contract: that a click sends the span's own `option_index` to the right
  // session, and that the banner retires only on a delivered answer.
  const FEED = {
    blockers: [{
      id: 999001, version: 1, trace_id: 'stub-trace', msg_key: 'permission-pending',
      msg_type: 'blocker', title: 'Question', body: '', session_title: 'stubbed',
      created_at: new Date().toISOString(), kind: 'question', span_id: 'toolu_stub',
      question: 'Pick a lane', header: '', multi_select: false,
      options: [
        { index: 0, label: 'Left lane', description: '' },
        { index: 1, label: 'Right lane', description: '' },
      ],
      bridge_reachable: true, sdk_owned: false, answerable: 'question',
    }],
  }

  async function stubFeed(page, { delivered }) {
    const sent = []
    await page.route('**/api/agent-messages/blockers', route => route.fulfill({
      status: 200, contentType: 'application/json', body: JSON.stringify(FEED),
    }))
    await page.route('**/api/sessions/*/bridge-answer', async (route) => {
      sent.push({ url: route.request().url(), body: route.request().postDataJSON() })
      await route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ delivered, detail: delivered ? '' : 'pane gone' }),
      })
    })
    return sent
  }

  test('a click sends the span option index and retires the banner', async ({ page }) => {
    const sent = await stubFeed(page, { delivered: true })
    // Deliberately NOT /live or /trace — the whole point is reach.
    await page.goto('/patterns')

    const banner = page.getByRole('alert')
    await expect(banner.getByText('Pick a lane')).toBeVisible({ timeout: 10_000 })
    await banner.getByRole('button', { name: 'Right lane' }).click()

    await expect.poll(() => sent.length).toBe(1)
    expect(sent[0].url).toContain('/api/sessions/stub-trace/bridge-answer')
    expect(sent[0].body).toMatchObject({ option_index: 1, label: 'Right lane' })
    await expect(page.getByRole('alert')).toHaveCount(0)
  })

  test('a refused send leaves the agent parked and says so', async ({ page }) => {
    const sent = await stubFeed(page, { delivered: false })
    await page.goto('/patterns')

    const banner = page.getByRole('alert')
    await expect(banner.getByText('Pick a lane')).toBeVisible({ timeout: 10_000 })
    await banner.getByRole('button', { name: 'Left lane' }).click()

    await expect.poll(() => sent.length).toBe(1)
    // The agent never got the answer, so the surface must not claim it did.
    await expect(page.getByText(/Not delivered/)).toBeVisible()
    await expect(banner.getByText('Pick a lane')).toBeVisible()
  })
})

test.describe('a blocker the operator cannot answer', () => {
  // The read-only shape: options recovered from the card's prose carry no
  // index, so there is nothing for a click to select. They render as context.
  const PROSE_FEED = {
    blockers: [{
      id: 999002, version: 1, trace_id: 'stub-ro', msg_key: 'permission-pending',
      msg_type: 'blocker', title: 'Question', body: '', session_title: 'ro',
      created_at: new Date().toISOString(), kind: 'tool', span_id: null,
      question: 'Unreachable prompt', header: '', multi_select: false,
      options: [
        { index: null, label: 'Prose one', description: '' },
        { index: null, label: 'Prose two', description: '' },
      ],
      bridge_reachable: false, sdk_owned: false, answerable: null,
    }],
  }

  test('shows the options as context, with no duplicate-key warning', async ({ page }) => {
    const warnings = []
    page.on('console', m => { if (m.type() === 'warning') warnings.push(m.text()) })
    await page.route('**/api/agent-messages/blockers', route => route.fulfill({
      status: 200, contentType: 'application/json', body: JSON.stringify(PROSE_FEED),
    }))
    await page.goto('/patterns')

    const banner = page.getByRole('alert')
    await expect(banner.getByText('Unreachable prompt')).toBeVisible({ timeout: 10_000 })
    await expect(banner.getByText('Prose one')).toBeVisible()
    // Shown, not clickable: every option here has `index: null`.
    await expect(banner.getByRole('button', { name: 'Prose one' })).toHaveCount(0)
    await expect(banner.getByRole('button', { name: /Open live session/ })).toBeVisible()
    expect(warnings.filter(w => /Duplicate keys/i.test(w))).toEqual([])
  })
})

test.describe('one session, two parked prompts', () => {
  // A session can hold a live `permission-pending` AND a live `plan-pending`
  // at once — separate emit paths, and neither resolves the other. Retiring
  // by trace_id dropped BOTH, so answering the permission prompt silently
  // took away the plan banner for an agent that was still stopped.
  test('answering one leaves the other up, and claims no resume', async ({ page }) => {
    clearBlockers()
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    await page.request.post('/api/session-spans', {
      data: [{
        trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null,
        name: 'prompt', start_time: localStamp(),
        attributes: { text: `NOTIF_TWO_${sfx}`, is_test: true },
      }],
    })
    await page.addInitScript(() => localStorage.setItem('regin_sessions_kind', 'all'))
    await page.goto('/trace/sessions')
    await streamReady(page)

    const permQ = `Permission ${sfx}`
    const planQ = `Plan ${sfx}`
    const permId = record({ type: 'blocker', title: 'Question', body: permQ,
      traceId, key: 'permission-pending' })
    record({ type: 'blocker', title: 'Plan', body: planQ,
      traceId, key: 'plan-pending' })

    const banner = page.getByRole('alert')
    await expect(banner.getByText('Decision 1 of 2')).toBeVisible({ timeout: 10_000 })

    // Answer only the permission prompt, the way the terminal would.
    expect(resolveKeyed(traceId, 'permission-pending')).toBe(1)

    await expect(page.getByText(permQ)).toHaveCount(0, { timeout: 10_000 })
    // The plan is still waiting — the agent is still stopped.
    await expect(page.getByText(planQ)).toBeVisible()
    await expect(banner.getByText(/Decision 1 of 2/)).toHaveCount(0)
    // …so the row must still say "awaiting", never "resumed".
    await expect(page.locator('.srow--awaiting')).toHaveCount(1)
    await expect(page.locator('.srow__resumed')).toHaveCount(0)

    await cleanup(page, [permId])
    await page.evaluate(async () => {
      const token = localStorage.getItem('regin_auth_token')
      const r = await fetch('/api/agent-messages/inbox?limit=200', {
        headers: { Authorization: `Bearer ${token}` },
      })
      const { messages } = await r.json()
      for (const m of messages.filter(x => x.msg_key === 'plan-pending')) {
        await fetch(`/api/agent-messages/${m.id}/dismiss`, {
          method: 'POST', headers: { Authorization: `Bearer ${token}` },
        })
      }
    })
  })
})

test.describe('a failed enrichment does not strand the card', () => {
  // A row raised off an SSE frame carries the inbox card but NOT the parked
  // span, so it renders with no options and no answer channel until
  // `/blockers` lands. While the stream is healthy nothing else re-reads, so
  // one failed GET used to leave a live decision stuck in the read-only
  // branch until the operator happened to navigate.
  test('the options arrive after a transient /blockers failure', async ({ page }) => {
    let calls = 0
    await page.route('**/api/agent-messages/blockers', async (route) => {
      calls += 1
      if (calls === 1) return route.abort('failed')
      return route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({
          blockers: [{
            id: 999004, version: 1, trace_id: 'stub-retry',
            msg_key: 'permission-pending', msg_type: 'blocker', title: 'Question',
            body: '', session_title: 'retry', kind: 'question',
            created_at: new Date().toISOString(), span_id: 'toolu_retry',
            question: 'Enriched at last', header: '', multi_select: false,
            options: [{ index: 0, label: 'Only after retry', description: '' }],
            bridge_reachable: true, sdk_owned: false, answerable: 'question',
          }],
        }),
      })
    })
    await page.goto('/patterns')

    // The first GET failed; the retry is what produces this.
    await expect(page.getByRole('alert').getByRole('button', { name: 'Only after retry' }))
      .toBeVisible({ timeout: 15_000 })
    expect(calls).toBeGreaterThan(1)
  })
})
