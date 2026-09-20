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
import { BASELINE_TRACE } from './helpers/fixtures.js'

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

// The other half of "answered in the terminal": PostToolUse lands the resolved
// twin, which is what takes the park itself down. The card's dismissal alone
// never did — since a dismissal is also how the operator SILENCES a live park,
// and a silenced park must keep flagging its row.
function retirePark(traceId) {
  const script = `
import sys
sys.path.insert(0, ".")
from lib.orm.engine import get_connection
conn = get_connection()
cur = conn.execute(
    "UPDATE session_spans SET status_code = 'OK' "
    " WHERE trace_id = ? AND status_code = 'PENDING' "
    "   AND name = 'permission.request'", (${JSON.stringify(traceId)},))
conn.commit()
print(cur.rowcount)
conn.close()
`
  return Number(execFileSync(PY, ['-c', script], { cwd: ROOT, encoding: 'utf8' })
    .trim().split('\n').pop())
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

// The banner pages through EVERY parked decision, so a blocker another spec
// left waiting would occupy page 1 and shift this test's own card behind it.
// Start each blocker test owning the banner.
//
// Dismissing the whole feed is only safe because the suite owns its database
// (`e2e-env.js`). Against the dev stack this cleared the operator's real parked
// decisions — it was the most destructive thing the suite did.
function clearBlockers() {
  const script = `
import sys
sys.path.insert(0, ".")
from lib.agent_messages import blockers, store
from lib.orm.engine import get_connection
for b in blockers.live_blockers(include_dismissed=True):
    # A derived card has no row and so no id — nothing to dismiss.
    if b["id"] is not None:
        store.dismiss(b["id"])
# The PARK, not just its card: dismissing only silences a live park now, so a
# leftover one would go on flagging its session row for every later test.
conn = get_connection()
conn.execute("UPDATE session_spans SET status_code = 'OK' "
             " WHERE status_code = 'PENDING' AND name = 'permission.request'")
conn.commit()
conn.close()
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

    // Explicit timeout: with no unread messages the badge does not exist, and
    // an untimed textContent() auto-waits for it until the test times out. On a
    // developer's real DB there was always something unread to hide this.
    const before = await page.locator('.sb-badge').first().textContent({ timeout: 1000 }).catch(() => null)
    ids.push(record({
      type: 'progress', title, body: 'Step 3 of 7 done.',
      traceId: `e2e-${randomUUID()}`,
    }))

    // The badge is the only surface a count-only message is allowed to touch.
    await expect.poll(async () => {
      const now = await page.locator('.sb-badge').first().textContent({ timeout: 1000 }).catch(() => null)
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

// The banner is a one-line FLAG now — dot, title, session, clock, pager, and
// three controls. The question, its options and the send button live in the
// pop-out that `Answer` opens (role=dialog, teleported to <body>, so it is
// outside the banner's own `role="alert"` subtree). Tests that need the
// decision itself open it; tests that only need "a decision is waiting" assert
// on the banner.
async function openDecision(page) {
  await page.locator('.blocker-banner').getByRole('button', { name: /^Answer/ }).click()
  const popout = page.getByRole('dialog')
  await expect(popout).toBeVisible()
  return popout
}

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

    const banner = page.locator('.blocker-banner')
    await expect(banner).toBeVisible({ timeout: 10_000 })
    // The header carries the card's title, not its body — the question is one
    // click away so the banner cannot grow with whatever the agent wrote.
    await expect(banner).toContainText('The agent is asking you a question')
    await expect(banner.getByText(QUESTION)).toHaveCount(0)

    const popout = await openDecision(page)
    await expect(popout.getByText(QUESTION)).toBeVisible()
    // Options come out of the body as separate rows, not one blob of text.
    await expect(popout.getByText('Escape only, then warn')).toBeVisible()
    await expect(popout.getByText('Detect + report only')).toBeVisible()
    await expect(popout.getByRole('button', { name: /Open live session/ })).toBeVisible()

    // "Fold" collapses, it does not close: the agent is still parked, so the
    // collapsed strip has to keep saying so. It takes the pop-out with it —
    // a modal left over a banner that is gone has nothing behind it.
    await popout.getByRole('button', { name: 'Fold', exact: true }).click()
    await expect(page.getByRole('dialog')).toHaveCount(0)
    await expect(page.getByText('1 decision waiting')).toBeVisible()
    await expect(page.getByText(QUESTION)).toHaveCount(0)

    // The strip's Answer reopens the decision, not merely the banner: the
    // banner can no longer answer anything on its own.
    await page.getByRole('button', { name: 'Answer', exact: true }).click()
    await expect(page.getByRole('dialog').getByText(QUESTION)).toBeVisible()
    expect(failures).toEqual([])
    await cleanup(page, ids)
  })

  // The header used to be a nowrap row, which clipped Dismiss out of reach
  // somewhere around 900px — the width where the title, the session ref and
  // the three controls first stop fitting on one line.
  test('the action cluster wraps intact rather than clipping Dismiss',
    async ({ page }) => {
      const ids = []
      clearBlockers()
      await page.setViewportSize({ width: 900, height: 800 })
      await page.goto('/trace/sessions')
      await streamReady(page)

      ids.push(record({
        type: 'blocker', title: 'The agent is asking you a question', body: BODY,
        traceId: `e2e-${randomUUID()}`, key: 'permission-pending',
      }))

      const banner = page.locator('.blocker-banner')
      await expect(banner).toBeVisible({ timeout: 10_000 })

      const box = await banner.boundingBox()
      // Every control, not just the last one: they share a flex:none group, so
      // a regression that breaks the group apart shows up on whichever wraps.
      for (const name of [/^Answer/, /^Fold$/, /^Dismiss/]) {
        const btn = banner.getByRole('button', { name })
        await expect(btn).toBeVisible()
        const at = await btn.boundingBox()
        expect(at.x).toBeGreaterThanOrEqual(box.x)
        expect(at.x + at.width).toBeLessThanOrEqual(box.x + box.width + 1)
      }
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
    const banner = page.locator('.blocker-banner')
    await expect(banner).toBeVisible({ timeout: 10_000 })

    // Dismiss stays on the banner itself: it is the one out that needs no
    // reading of the question, so it must not require opening the pop-out.
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
    await expect(page.locator('.blocker-banner')).toBeVisible({ timeout: 10_000 })
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
    await expect(page.locator('.blocker-banner')).toBeVisible()

    // Answered in the terminal: the hook dismisses the keyed card out of
    // process, and both surfaces must settle with no interaction here.
    // Park first, card second — PostToolUse's own order, and the one the
    // feed's re-read depends on: a card retired ahead of its park is re-read
    // as still waiting.
    expect(retirePark(traceId)).toBe(1)
    expect(resolveKeyed(traceId, 'permission-pending')).toBe(1)
    await expect(page.locator('.blocker-banner')).toHaveCount(0, { timeout: 10_000 })
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

    await expect(page.locator('.blocker-banner')).toBeVisible({ timeout: 10_000 })
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
    await expect(page.locator('.blocker-banner')).toBeVisible()
    await expect((await openDecision(page)).getByText(QUESTION)).toBeVisible()
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
      await expect(page.locator('.blocker-banner')).toBeVisible({ timeout: 10_000 })
      await expect(page.locator('.srow--awaiting')).toHaveCount(1)

      const status = await page.evaluate(async (messageId) => {
        const r = await fetch(`/api/agent-messages/${messageId}/dismiss`, {
          method: 'POST',
          headers: { Authorization: `Bearer ${localStorage.getItem('regin_auth_token')}` },
        })
        return r.status
      }, id)
      expect(status).toBe(200)

      await expect(page.locator('.blocker-banner')).toHaveCount(0, { timeout: 10_000 })
      // The agent was never un-parked, so nothing may say it resumed — and the
      // row must go on flagging it. Dismissing is "stop interrupting me", not
      // "this is answered"; a park never expires, so a dismissal that also
      // cleared this marker left no surface anywhere to answer from.
      await expect(page.locator('.srow--awaiting')).toHaveCount(1)
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
    await expect(page.locator('.blocker-banner')).toBeVisible({ timeout: 10_000 })
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
    await expect(page.locator('.blocker-banner')).toBeVisible()
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
    await expect(page.locator('.blocker-banner')).toBeVisible({ timeout: 10_000 })

    await page.reload()
    await expect(page.locator('.blocker-banner')).toBeVisible({ timeout: 10_000 })

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
    await expect(page.locator('.blocker-banner')).toBeVisible({ timeout: 10_000 })
    await expect((await openDecision(page)).getByText(question)).toBeVisible()
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

    const banner = page.locator('.blocker-banner')
    await expect(banner.getByText('Decision 1 of 3')).toBeVisible({ timeout: 10_000 })

    // The pop-out carries the pager too, so the operator can read through the
    // queue without closing and reopening it once per decision.
    const popout = await openDecision(page)
    await expect(popout.getByText(`Decision 0 of ${tag}`)).toBeVisible()
    await popout.getByRole('button', { name: 'Next decision' }).click()
    await expect(popout.getByText(`Decision 1 of ${tag}`)).toBeVisible()
    await expect(popout).toBeVisible()
    await popout.getByRole('button', { name: 'Close without answering' }).click()

    await expect(banner.getByText('Decision 2 of 3')).toBeVisible()
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

    await expect(page.locator('.blocker-banner')).toBeVisible({ timeout: 10_000 })
    const popout = await openDecision(page)
    await expect(popout.getByText('Pick a lane')).toBeVisible()

    // Picking is not sending. The banner is reachable from every page in the
    // app, so a one-tap answer meant a mis-click could answer a stopped agent
    // with nothing to undo it — Send is the confirmation.
    const send = popout.getByRole('button', { name: /Send decision/ })
    await expect(send).toBeDisabled()
    await popout.getByRole('radio', { name: /Right lane/ }).click()
    await expect(send).toBeEnabled()
    await send.click()

    await expect.poll(() => sent.length).toBe(1)
    expect(sent[0].url).toContain('/api/sessions/stub-trace/bridge-answer')
    expect(sent[0].body).toMatchObject({ option_index: 1, label: 'Right lane' })
    await expect(page.getByRole('alert')).toHaveCount(0)
    // The pop-out goes with the card it was answering.
    await expect(page.getByRole('dialog')).toHaveCount(0)
  })

  test('a refused send leaves the agent parked and says so', async ({ page }) => {
    const sent = await stubFeed(page, { delivered: false })
    await page.goto('/patterns')

    await expect(page.locator('.blocker-banner')).toBeVisible({ timeout: 10_000 })
    const popout = await openDecision(page)
    await expect(popout.getByText('Pick a lane')).toBeVisible()
    await popout.getByRole('radio', { name: /Left lane/ }).click()
    await popout.getByRole('button', { name: /Send decision/ }).click()

    await expect.poll(() => sent.length).toBe(1)
    // The agent never got the answer, so the surface must not claim it did.
    await expect(page.getByText(/Not delivered/)).toBeVisible()
    await expect(popout.getByText('Pick a lane')).toBeVisible()
    await expect(page.locator('.blocker-banner')).toBeVisible()
  })
})

test.describe('paging does not carry one card state onto another', () => {
  // The pop-out stays mounted while the pager swaps the card under it, so its
  // local send state has to be scoped to the card. A refused send used to
  // leave "Not delivered" rendered under the NEXT agent's question — an error
  // attributed to a decision it was never about.
  const TWO = {
    blockers: [0, 1].map(i => ({
      id: 999010 + i, version: 1, trace_id: `stub-pair-${i}`,
      msg_key: 'permission-pending', msg_type: 'blocker', title: 'Question',
      body: '', session_title: `pair-${i}`, kind: 'question',
      created_at: new Date().toISOString(), span_id: `toolu_pair_${i}`,
      question: `Question number ${i}`, header: '', multi_select: false,
      options: [{ index: 0, label: `Option ${i}A`, description: '' }],
      bridge_reachable: true, sdk_owned: false, answerable: 'question',
    })),
  }

  test('a refused send does not leave its error on the next decision',
    async ({ page }) => {
      await page.route('**/api/agent-messages/blockers', route => route.fulfill({
        status: 200, contentType: 'application/json', body: JSON.stringify(TWO),
      }))
      await page.route('**/api/sessions/*/bridge-answer', route => route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ delivered: false, detail: 'pane gone' }),
      }))
      await page.goto('/patterns')

      await expect(page.locator('.blocker-banner')).toBeVisible({ timeout: 10_000 })
      const popout = await openDecision(page)
      await expect(popout.getByText('Question number 0')).toBeVisible()
      await popout.getByRole('radio', { name: /Option 0A/ }).click()
      await popout.getByRole('button', { name: /Send decision/ }).click()
      await expect(popout.getByText(/Not delivered/)).toBeVisible()

      await popout.getByRole('button', { name: 'Next decision' }).click()
      await expect(popout.getByText('Question number 1')).toBeVisible()
      // The failure belonged to card 0, and card 0 is not on screen.
      await expect(popout.getByText(/Not delivered/)).toHaveCount(0)
      // …and the selection did not travel either, so Send is armed for nothing.
      await expect(popout.getByRole('button', { name: /Send decision/ })).toBeDisabled()
    })
})

test.describe('a superseded prompt does not inherit the last pick', () => {
  // A re-prompt supersedes IN PLACE: same (trace_id, msg_key), new `version`,
  // new question, new options. A selection is only valid for the exact prompt
  // it was made against, so it must not survive — a carried-over index would
  // leave Send armed against a different list, one click from mis-answering.
  const card = version => ({
    blockers: [{
      id: 999020, version, trace_id: 'stub-sup', msg_key: 'permission-pending',
      msg_type: 'blocker', title: 'Question', body: '', session_title: 'sup',
      created_at: new Date().toISOString(), kind: 'question',
      span_id: 'toolu_sup', header: '', multi_select: false,
      question: version === 1 ? 'First question' : 'Second question',
      options: [{ index: 0, label: `Option ${version}A`, description: '' }],
      bridge_reachable: true, sdk_owned: false, answerable: 'question',
    }],
  })

  test('the pick and any error reset when the version advances', async ({ page }) => {
    clearBlockers()
    let version = 1
    await page.route('**/api/agent-messages/blockers', route => route.fulfill({
      status: 200, contentType: 'application/json', body: JSON.stringify(card(version)),
    }))
    await page.goto('/trace/sessions')
    await streamReady(page)

    await expect(page.locator('.blocker-banner')).toBeVisible({ timeout: 10_000 })
    const popout = await openDecision(page)
    await expect(popout.getByText('First question')).toBeVisible()
    await popout.getByRole('radio', { name: /Option 1A/ }).click()
    await expect(popout.getByRole('button', { name: /Send decision/ })).toBeEnabled()

    // A real tier-1 frame is what makes the client re-read the feed; the stub
    // answers that read with the superseded card.
    version = 2
    const id = record({ type: 'blocker', title: 'Question', body: 'reprompt',
      traceId: `e2e-${randomUUID()}`, key: 'permission-pending' })

    await expect(popout.getByText('Second question')).toBeVisible({ timeout: 10_000 })
    // The pick belonged to version 1 and its options are gone with it.
    await expect(popout.getByRole('button', { name: /Send decision/ })).toBeDisabled()
    await cleanup(page, [id])
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

    await expect(page.locator('.blocker-banner')).toBeVisible({ timeout: 10_000 })
    const popout = await openDecision(page)
    await expect(popout.getByText('Unreachable prompt')).toBeVisible()
    await expect(popout.getByText('Prose one')).toBeVisible()
    // Shown, not clickable: every option here has `index: null`. The radio
    // list is the `question` shape only — this one keeps the chips, and with
    // them there is nothing for Send to send, so it must not be offered.
    await expect(popout.getByRole('button', { name: 'Prose one' })).toHaveCount(0)
    await expect(popout.getByRole('radio')).toHaveCount(0)
    await expect(popout.getByRole('button', { name: /Send decision/ })).toHaveCount(0)
    await expect(popout.getByRole('button', { name: /Open live session/ })).toBeVisible()
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

    const banner = page.locator('.blocker-banner')
    await expect(banner.getByText('Decision 1 of 2')).toBeVisible({ timeout: 10_000 })

    // Answer only the permission prompt, the way the terminal would.
    expect(resolveKeyed(traceId, 'permission-pending')).toBe(1)

    await expect(banner.getByText(/Decision 1 of 2/)).toHaveCount(0, { timeout: 10_000 })
    // The plan is still waiting — the agent is still stopped.
    const popout = await openDecision(page)
    await expect(popout.getByText(planQ)).toBeVisible()
    await expect(popout.getByText(permQ)).toHaveCount(0)
    await popout.getByRole('button', { name: 'Close without answering' }).click()
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
    await expect(page.locator('.blocker-banner')).toBeVisible({ timeout: 15_000 })
    const popout = await openDecision(page)
    await expect(popout.getByRole('radio', { name: 'Only after retry' })).toBeVisible()
    expect(calls).toBeGreaterThan(1)
  })
})

test.describe('an ask with several questions', () => {
  // The defect: an AskUserQuestion can carry more than one question behind a
  // single Submit, but the card only ever rendered `questions[0]`. The
  // operator picked for tab 1, Send fired one answer, and the agent stayed
  // parked on the rest with nothing on this surface to reach them.
  const MULTI = {
    blockers: [{
      id: 999005, version: 1, trace_id: 'stub-multi',
      msg_key: 'permission-pending', msg_type: 'blocker', title: 'Question',
      body: '', session_title: 'multi', kind: 'question',
      created_at: new Date().toISOString(), span_id: 'toolu_multi',
      question: 'Where should the corpus come from?', header: '',
      multi_select: false,
      options: [
        { index: 0, label: 'From the repo', description: '', preview: '' },
        { index: 1, label: 'From a folder', description: '', preview: '' },
      ],
      questions: [
        {
          question: 'Where should the corpus come from?', header: 'Corpus',
          multiSelect: false,
          options: [
            { index: 0, label: 'From the repo', description: '', preview: '' },
            { index: 1, label: 'From a folder', description: '', preview: 'a | b' },
          ],
        },
        {
          question: 'What is the ground truth?', header: 'Truth',
          multiSelect: false,
          options: [
            { index: 0, label: 'Hand-labelled', description: '', preview: '' },
            { index: 1, label: 'Harper output', description: '', preview: '' },
          ],
        },
      ],
      bridge_reachable: true, sdk_owned: false, answerable: 'question',
    }],
  }

  async function stubMulti(page, { delivered = true } = {}) {
    const sent = []
    await page.route('**/api/agent-messages/blockers', route => route.fulfill({
      status: 200, contentType: 'application/json', body: JSON.stringify(MULTI),
    }))
    await page.route('**/api/sessions/*/bridge-answer', async (route) => {
      sent.push(route.request().postDataJSON())
      await route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ delivered, detail: delivered ? '' : 'pane gone' }),
      })
    })
    return sent
  }

  test('every question must be answered before one Submit carries them all',
    async ({ page }) => {
      const sent = await stubMulti(page)
      await page.goto('/patterns')
      await expect(page.locator('.blocker-banner')).toBeVisible({ timeout: 10_000 })
      const popout = await openDecision(page)

      await expect(popout.getByTestId('blocker-step')).toContainText('Question 1 of 2')
      const send = popout.getByRole('button', { name: /Send 2 answers/ })
      await expect(send).toBeDisabled()

      // Answering tab 1 is not answering the ask: the TUI is tabbed behind a
      // single Submit, so a send here would submit a half-filled form.
      await popout.getByRole('radio', { name: /From a folder/ }).click()
      await expect(send).toBeDisabled()

      await popout.getByTestId('blocker-next').click()
      await expect(popout.getByTestId('blocker-step')).toContainText('Question 2 of 2')
      await expect(popout.getByText('What is the ground truth?')).toBeVisible()
      await popout.getByRole('radio', { name: /Harper output/ }).click()
      await expect(send).toBeEnabled()
      await send.click()

      // ONE post carrying both, in question order, each guarded by the text of
      // the tab it belongs to — never one POST per question.
      await expect.poll(() => sent.length).toBe(1)
      expect(sent[0].answers).toEqual([
        {
          option_index: 1, label: 'From a folder',
          confirm_text: 'Where should the corpus come from?',
        },
        {
          option_index: 1, label: 'Harper output',
          confirm_text: 'What is the ground truth?',
        },
      ])
      await expect(page.getByRole('dialog')).toHaveCount(0)
    })

  // The defect: `/agent-messages/blockers` is re-read on a 60s reconcile and on
  // every route entry, and the response replaces each row object wholesale.
  // The answer card watched the QUESTIONS computed — whose identity changes on
  // every such replace even when the server returned the same card — and reset
  // the step to 0, dropping the operator back to question 1 mid-ask.
  test('a /blockers reconcile keeps the question step and the staged draft',
    async ({ page }) => {
      let reads = 0
      await page.route('**/api/agent-messages/blockers', (route) => {
        reads += 1
        return route.fulfill({
          status: 200, contentType: 'application/json',
          body: JSON.stringify(MULTI),
        })
      })
      await page.goto('/patterns')
      await expect(page.locator('.blocker-banner')).toBeVisible({ timeout: 10_000 })
      const popout = await openDecision(page)

      await popout.getByRole('radio', { name: /From a folder/ }).click()
      await popout.getByTestId('blocker-next').click()
      await expect(popout.getByTestId('blocker-step')).toContainText('Question 2 of 2')
      await popout.getByTestId('blocker-stage-free').click()
      await popout.getByTestId('blocker-free-input').fill('neither, use both')

      // A real route entry, which is what AppLayout re-reads the parked set on.
      // Driven through history rather than a click because the modal's scrim
      // covers the nav, and through popstate rather than page.goto because a
      // document reload is a different scenario with its own honest state loss.
      const before = reads
      await page.evaluate(() => {
        window.history.pushState({}, '', '/sessions')
        window.dispatchEvent(new PopStateEvent('popstate', { state: window.history.state }))
      })
      await expect.poll(() => reads).toBeGreaterThan(before)

      await expect(popout.getByTestId('blocker-step')).toContainText('Question 2 of 2')
      await expect(popout.getByText('What is the ground truth?')).toBeVisible()
      await expect(popout.getByTestId('blocker-free-input'))
        .toHaveValue('neither, use both')
      await expect(popout.getByTestId('blocker-summary')).toContainText('From a folder')
    })

  test('a pick survives stepping back to revise it', async ({ page }) => {
    await stubMulti(page)
    await page.goto('/patterns')
    await expect(page.locator('.blocker-banner')).toBeVisible({ timeout: 10_000 })
    const popout = await openDecision(page)

    await popout.getByRole('radio', { name: /From the repo/ }).click()
    await popout.getByTestId('blocker-next').click()
    await popout.getByTestId('blocker-prev').click()
    await expect(popout.getByRole('radio', { name: /From the repo/ }))
      .toHaveAttribute('aria-checked', 'true')
    // The summary reads off the same payload the POST will carry.
    await expect(popout.getByTestId('blocker-summary')).toContainText('From the repo')
  })

  test('an option preview is readable without leaving for /live',
    async ({ page }) => {
      await stubMulti(page)
      await page.goto('/patterns')
      await expect(page.locator('.blocker-banner')).toBeVisible({ timeout: 10_000 })
      const popout = await openDecision(page)

      const preview = popout.getByTestId('blocker-preview')
      await expect(preview).toHaveCount(1) // only the option that carries one
      await expect(popout.getByText('a | b')).toBeHidden()
      await preview.getByText('Preview').click()
      await expect(popout.getByText('a | b')).toBeVisible()
    })

  test('a typed answer is delivered as the TUI free-text entry',
    async ({ page }) => {
      const sent = await stubMulti(page)
      await page.goto('/patterns')
      await expect(page.locator('.blocker-banner')).toBeVisible({ timeout: 10_000 })
      const popout = await openDecision(page)

      await popout.getByTestId('blocker-stage-free').click()
      await popout.getByTestId('blocker-free-input').fill('neither — use the wiki')
      await popout.getByTestId('blocker-next').click()
      await popout.getByRole('radio', { name: /Hand-labelled/ }).click()
      await popout.getByRole('button', { name: /Send 2 answers/ }).click()

      await expect.poll(() => sent.length).toBe(1)
      // "Type something." sits directly after the listed options, so index 2
      // of a 2-option question IS that entry.
      expect(sent[0].answers[0]).toMatchObject({
        option_index: 2, text: 'neither — use the wiki',
      })
    })
})

test.describe('a multi-select ask', () => {
  test('stages as checkboxes and delivers every ticked index',
    async ({ page }) => {
      await page.route('**/api/agent-messages/blockers', route => route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({
          blockers: [{
            id: 999006, version: 1, trace_id: 'stub-multisel',
            msg_key: 'permission-pending', msg_type: 'blocker', title: 'Question',
            body: '', session_title: 'multisel', kind: 'question',
            created_at: new Date().toISOString(), span_id: 'toolu_multisel',
            question: 'Which of these apply?', header: '', multi_select: true,
            options: [
              { index: 0, label: 'One', description: '' },
              { index: 1, label: 'Two', description: '' },
              { index: 2, label: 'Three', description: '' },
            ],
            questions: [{
              question: 'Which of these apply?', header: '', multiSelect: true,
              options: [
                { index: 0, label: 'One', description: '' },
                { index: 1, label: 'Two', description: '' },
                { index: 2, label: 'Three', description: '' },
              ],
            }],
            bridge_reachable: true, sdk_owned: false, answerable: 'question',
          }],
        }),
      }))
      await page.goto('/patterns')
      await expect(page.locator('.blocker-banner')).toBeVisible({ timeout: 10_000 })
      const popout = await openDecision(page)

      const posts = []
      await page.route('**/api/sessions/stub-multisel/bridge-answer', route => {
        posts.push(JSON.parse(route.request().postData() || '{}'))
        return route.fulfill({
          status: 200, contentType: 'application/json',
          body: JSON.stringify({ delivered: true, detail: 'submitted' }),
        })
      })

      // Checkboxes, not radios — and ticking a second option keeps the first.
      const boxes = popout.getByRole('checkbox')
      await expect(boxes).toHaveCount(3)
      await boxes.nth(0).click()
      await boxes.nth(2).click()
      await expect(boxes.nth(0)).toHaveAttribute('aria-checked', 'true')
      await expect(boxes.nth(2)).toHaveAttribute('aria-checked', 'true')
      await expect(boxes.nth(1)).toHaveAttribute('aria-checked', 'false')

      await popout.getByRole('button', { name: /Send/ }).click()

      // The `answers` array shape, because only that walk presses the TUI's
      // mandatory final Submit; option_count locates the advance row.
      await expect.poll(() => posts.length).toBe(1)
      expect(posts[0].answers[0].option_index).toEqual([0, 2])
      expect(posts[0].answers[0].option_count).toBe(3)
    })

  test('offers no one-tap option button that bypasses the staged checkboxes',
    async ({ page }) => {
      // The regression: BlockerActions rendered its one-tap options beside
      // BlockerAnswer's checkboxes. One tap posted the flat single-pick shape,
      // which on a multi-select widget only TICKS a box — the walk never
      // reaches Submit, so the agent stayed parked while `delivered: true`
      // retired the card. The operator saw the card vanish, no boxes staged,
      // and the option selected in the terminal.
      await page.route('**/api/agent-messages/blockers', route => route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({
          blockers: [{
            id: 999008, version: 1, trace_id: 'stub-multisel3',
            msg_key: 'permission-pending', msg_type: 'blocker', title: 'Question',
            body: '', session_title: 'multisel3', kind: 'question',
            created_at: new Date().toISOString(), span_id: 'toolu_multisel3',
            question: 'Which of these apply?', header: '', multi_select: true,
            options: [
              { index: 0, label: 'One', description: '' },
              { index: 1, label: 'Two', description: '' },
            ],
            questions: [{
              question: 'Which of these apply?', header: '', multiSelect: true,
              options: [
                { index: 0, label: 'One', description: '' },
                { index: 1, label: 'Two', description: '' },
              ],
            }],
            bridge_reachable: true, sdk_owned: false, answerable: 'question',
          }],
        }),
      }))
      const posts = []
      await page.route('**/bridge-answer', route => {
        posts.push(JSON.parse(route.request().postData() || '{}'))
        return route.fulfill({
          status: 200, contentType: 'application/json',
          body: JSON.stringify({ delivered: true, detail: 'ok' }),
        })
      })
      await page.goto('/patterns')
      await expect(page.locator('.blocker-banner')).toBeVisible({ timeout: 10_000 })
      const popout = await openDecision(page)

      // The checkboxes are the only way to answer; no bypassing pick button.
      await expect(popout.getByRole('checkbox')).toHaveCount(2)
      await expect(page.locator('.blocker-opt-btn')).toHaveCount(0)

      // And what does go out is the array shape, never a flat single pick.
      await popout.getByRole('checkbox').first().click()
      await popout.getByRole('button', { name: /Send/ }).click()
      await expect.poll(() => posts.length).toBe(1)
      expect(posts[0].answers).toBeTruthy()
      expect(posts[0].option_index).toBeUndefined()
    })

  test('unticking the last box leaves the ask unanswered', async ({ page }) => {
      await page.route('**/api/agent-messages/blockers', route => route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({
          blockers: [{
            id: 999007, version: 1, trace_id: 'stub-multisel2',
            msg_key: 'permission-pending', msg_type: 'blocker', title: 'Question',
            body: '', session_title: 'multisel2', kind: 'question',
            created_at: new Date().toISOString(), span_id: 'toolu_multisel2',
            question: 'Which of these apply?', header: '', multi_select: true,
            options: [{ index: 0, label: 'One', description: '' }],
            questions: [{
              question: 'Which of these apply?', header: '', multiSelect: true,
              options: [{ index: 0, label: 'One', description: '' }],
            }],
            bridge_reachable: true, sdk_owned: false, answerable: 'question',
          }],
        }),
      }))
      await page.goto('/patterns')
      await expect(page.locator('.blocker-banner')).toBeVisible({ timeout: 10_000 })
      const popout = await openDecision(page)

      const box = popout.getByRole('checkbox').first()
      await box.click()
      await expect(popout.getByRole('button', { name: /Send/ })).toBeEnabled()
      await box.click()
      await expect(box).toHaveAttribute('aria-checked', 'false')
      await expect(popout.getByRole('button', { name: /Send/ })).toBeDisabled()
    })
})


// A frozen viewport (`meta.fixedViewport`) has no scroll to give back, so
// whatever the in-flow banner pushes down is gone rather than merely lower:
// the view root is sized to the whole scroller, and its last rows — the
// composer, the follow-up — used to spill past a clipped edge.
test.describe('a banner over a frozen viewport', () => {
  test('the view yields the banner its band instead of spilling past the clip', async ({ page }) => {
    const ids = []
    clearBlockers()
    // Above the bounded threshold. Below it the shell scrolls as usual and
    // there is nothing to clip, so the bug cannot reproduce.
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto(`/trace/sessions/${BASELINE_TRACE}`)
    await streamReady(page)

    ids.push(record({
      type: 'blocker', title: 'The agent is asking you a question',
      body: 'Pick one\n\u2022 Yes\n\u2022 No',
      traceId: `e2e-${randomUUID()}`, key: 'permission-pending',
    }))

    const banner = page.locator('.blocker-banner')
    await expect(banner).toBeVisible({ timeout: 10_000 })
    const shell = page.locator('.trace-shell-bounded')
    await expect(shell).toBeVisible()

    const scrollerBox = await page.locator('.content-scroll').boundingBox()
    const shellBox = await shell.boundingBox()
    const bannerBox = await banner.boundingBox()

    // The banner is in flow ABOVE the view — without this the assertion below
    // would pass on a banner that simply floated over the page.
    expect(shellBox.y).toBeGreaterThanOrEqual(bannerBox.y + bannerBox.height)
    expect(shellBox.y + shellBox.height).toBeLessThanOrEqual(scrollerBox.y + scrollerBox.height + 1)

    await cleanup(page, ids)
  })
})
