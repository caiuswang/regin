/**
 * Named SSE frames are addressed, not broadcast.
 *
 * The defect this covers: `trace_spans` is raised once per accepted span
 * batch, and it used to reach every open stream in the deployment — so a tab
 * reading session A woke, re-read and re-rendered every time an unrelated
 * session B ingested. The frame now carries `topic=trace:<canonical id>` and
 * only the streams that declared that trace receive it.
 *
 * Asserted at the browser boundary rather than against the hub, because the
 * declaration is the half that lives in the client: the leader tab posts the
 * union of every tab's interests against the stream id it opened with, and a
 * unit test cannot show that the id the view declares is the id the ingest
 * addresses.
 *
 * Two browser CONTEXTS, not two pages: Web Locks and BroadcastChannel are
 * per-browser, so two pages in one context share a single stream and one
 * union of interests — exactly the arrangement that cannot demonstrate
 * separation.
 */
import { randomUUID } from 'node:crypto'
import { expect, test } from '@playwright/test'
import { TEST_USER } from '../e2e-env.js'
import { API_BASE } from './helpers/api-base.js'
import { BASELINE_TRACE, HEAVY_TRACE } from './helpers/fixtures.js'

// Record every `trace_spans` frame the page's own EventSource surfaces. The
// app's reaction (a /map re-read) is not usable evidence: the live poll issues
// the same request on its own cadence.
// What a plain-http LAN origin looks like: `crypto.randomUUID` is gated to
// secure contexts, `getRandomValues` is not. Playwright always serves over
// localhost — a secure context — so without this the suite cannot see a
// module-scope `randomUUID()` that blanks the app on a phone.
const NO_RANDOM_UUID = () => {
  Object.defineProperty(crypto, 'randomUUID',
    { value: undefined, configurable: true })
}

const SPY = () => {
  window.__traceFrames = []
  const Real = window.EventSource
  window.EventSource = class extends Real {
    addEventListener(name, fn, opts) {
      if (name !== 'trace_spans') return super.addEventListener(name, fn, opts)
      return super.addEventListener(name, (event) => {
        window.__traceFrames.push(event.data)
        fn(event)
      }, opts)
    }
  }
}

async function openTrace(browser, traceId, { insecure = false } = {}) {
  const context = await browser.newContext()
  const login = await context.request.post(`${API_BASE}/api/auth/login`,
    { data: TEST_USER })
  const { token, user } = await login.json()
  await context.addInitScript(({ t, u }) => {
    localStorage.setItem('regin_auth_token', t)
    localStorage.setItem('regin_auth_user', JSON.stringify(u))
  }, { t: token, u: user })
  await context.addInitScript(SPY)
  if (insecure) await context.addInitScript(NO_RANDOM_UUID)
  const page = await context.newPage()
  const declared = []
  page.on('request', (req) => {
    if (req.method() === 'POST'
        && req.url().includes('/api/notifications/stream/topics')) {
      declared.push(req.postDataJSON())
    }
  })
  await page.goto(`/trace/sessions/${traceId}`)
  await page.waitForSelector('.session-trace-view, .trace-view, main', {
    timeout: 15000,
  })
  return { context, page, declared }
}

async function ingest(traceId) {
  const resp = await fetch(`${API_BASE}/api/session-spans`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify([{
      trace_id: traceId,
      span_id: `sse-topic-${randomUUID()}`,
      name: 'tool.Bash',
      // Naive, like every seeded span: the envelope widening compares this
      // against the session's existing bounds and will not mix tz-awareness.
      start_time: new Date().toISOString().replace('Z', ''),
    }]),
  })
  expect(`${resp.status} ${await resp.text()}`).toContain('"ok":true')
}

test('the open trace is declared as a topic on the stream', async ({ browser }) => {
  const { context, declared } = await openTrace(browser, BASELINE_TRACE)
  try {
    await expect.poll(() => declared.length, { timeout: 15000 })
      .toBeGreaterThan(0)
    const last = declared[declared.length - 1]
    expect(last.stream).toBeTruthy()
    expect(last.topics).toContain(`trace:${BASELINE_TRACE}`)
    // The declaration is a whitelist, not a hint: nothing else may ride it.
    expect(last.topics.every((t) => t.startsWith('trace:'))).toBe(true)
  } finally {
    await context.close()
  }
})

test('an ingest wakes only the tab reading that session', async ({ browser }) => {
  const watched = await openTrace(browser, BASELINE_TRACE)
  const elsewhere = await openTrace(browser, HEAVY_TRACE)
  try {
    await expect.poll(() => watched.declared.length, { timeout: 15000 })
      .toBeGreaterThan(0)
    await expect.poll(() => elsewhere.declared.length, { timeout: 15000 })
      .toBeGreaterThan(0)

    await ingest(BASELINE_TRACE)

    await expect.poll(
      () => watched.page.evaluate(() => window.__traceFrames.length),
      { timeout: 15000 }).toBeGreaterThan(0)
    expect(await elsewhere.page.evaluate(() => window.__traceFrames.length))
      .toBe(0)
  } finally {
    await watched.context.close()
    await elsewhere.context.close()
  }
})

test('navigating to another session redeclares the topic', async ({ browser }) => {
  // The trace route is keyed by path, so session A -> session B remounts the
  // view: B's setup runs before A's unmount hook, and a declaration made from
  // setup is wiped by A's teardown moments later. The view would then sit on a
  // stream that names nobody, with its live poll already retired.
  const { context, page, declared } = await openTrace(browser, BASELINE_TRACE)
  try {
    await expect.poll(() => declared.length, { timeout: 15000 })
      .toBeGreaterThan(0)
    // In-app routing, not `page.goto`: a full load remounts the whole app and
    // would never exercise the interleave this test exists for.
    await page.evaluate((id) => {
      const app = document.querySelector('#app').__vue_app__
      return app.config.globalProperties.$router.push(`/trace/sessions/${id}`)
    }, HEAVY_TRACE)
    await expect.poll(
      () => declared[declared.length - 1]?.topics ?? [], { timeout: 15000 })
      .toContain(`trace:${HEAVY_TRACE}`)

    await ingest(HEAVY_TRACE)
    await expect.poll(
      () => page.evaluate(() => window.__traceFrames.length),
      { timeout: 15000 }).toBeGreaterThan(0)
  } finally {
    await context.close()
  }
})

test('the stream identifies itself without a secure context', async ({ browser }) => {
  const { context, page, declared } = await openTrace(
    browser, BASELINE_TRACE, { insecure: true })
  try {
    expect(await page.evaluate(() => crypto.randomUUID)).toBeUndefined()
    await expect.poll(() => declared.length, { timeout: 15000 })
      .toBeGreaterThan(0)
    const last = declared[declared.length - 1]
    expect(last.stream).toMatch(/^[0-9a-f]{32}$/)
    expect(last.topics).toContain(`trace:${BASELINE_TRACE}`)
  } finally {
    await context.close()
  }
})
