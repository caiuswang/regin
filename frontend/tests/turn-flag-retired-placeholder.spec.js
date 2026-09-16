/**
 * The Activity strip's turn flags (and the vitals "Turns" cell) must not
 * double-count one prompt while its live placeholder hands off to its anchor.
 *
 * `reloadLiveTail` splits the tree at the fresh page's lowest DB id: anything
 * below it is kept as "older, immutable" history. A retired placeholder is
 * written BEFORE the row that supersedes it, so it carries a LOWER id and
 * slips into that half, where nothing prunes it — one prompt then paints two
 * flags (⚐1 ⚐2) and a Turns count of 2. It bites whenever the superseded row
 * is the oldest root in the window, which is every prompt of a regin-launched
 * SDK run: both writers announce it (lib/trace/alias.py), so one submission
 * lands as sdk placeholder → hook placeholder → transcript anchor, each
 * retiring the last.
 *
 * Seeded with `is_test=true` so the trace stays out of the sessions list.
 */
import { test, expect } from './auth-fixture.js'
import { randomUUID, createHash } from 'node:crypto'

// Mirror of lib/trace/pending_spans.prompt_placeholder_id — the id the backend
// retires once the anchor for the same text arrives.
function promptPlaceholderId(traceId, text) {
  const key = `${traceId || ''}\x00${(text || '').trim().slice(0, 512)}`
  const digest = createHash('sha1').update(key, 'utf8').digest('hex').slice(0, 13)
  return `promptlive-${digest}`
}

const TEXT = 'the only prompt of this session'

async function post(page, spans) {
  const res = await page.request.post('/api/session-spans', { data: spans })
  expect(res.ok()).toBeTruthy()
}

function turnsCellValue(page) {
  return page.evaluate(() => {
    const strip = document.querySelector('[data-testid="trace-vitals-strip"]')
    const cell = [...(strip?.children || [])]
      .find(c => c.querySelector('dt')?.textContent.trim() === 'Turns')
    return cell?.querySelector('dd')?.textContent.trim() ?? null
  })
}

test('a retired prompt placeholder does not leave a phantom turn flag', async ({ page }) => {
  const traceId = randomUUID()
  const phId = promptPlaceholderId(traceId, TEXT)
  const anchorId = `prompt-${traceId.slice(24)}-anchor`

  // The in-flight prompt is the session's ONLY prompt, so it is also the
  // oldest root of the served window — the shape that puts the superseded row
  // on the "older" side of the split. The tool span runs BEFORE it: it is
  // there to give the trace a non-zero duration (no flags render without one),
  // and a prompt placeholder with a later span behind it is swept out of the
  // tree entirely by merge.py's `_drop_stale_blockers`.
  await post(page, [
    {
      trace_id: traceId, span_id: phId, parent_id: null, name: 'prompt',
      start_time: '2026-05-18T08:05:00.000000', status_code: 'PENDING',
      attributes: { text: TEXT, live_placeholder: true, is_test: true },
    },
    {
      trace_id: traceId, span_id: `bash-${traceId.slice(0, 8)}`, parent_id: null,
      name: 'tool.Bash',
      start_time: '2026-05-18T08:04:00.000000',
      end_time: '2026-05-18T08:04:30.000000',
      attributes: { command: 'echo hi', is_test: true },
    },
  ])

  // Gate the final assertion on the anchor actually reaching the client:
  // without it, "1 flag" would also pass on a poll that never fired.
  let anchorFetched = false
  page.on('response', async (resp) => {
    if (resp.request().method() !== 'GET') return
    if (!resp.url().includes(`/sessions/${traceId}/map`)) return
    try {
      const body = await resp.text()
      if (body.includes(anchorId)) anchorFetched = true
    } catch { /* body unavailable — ignore */ }
  })

  await page.goto(`/trace/sessions/${traceId}`)
  await expect(page.getByTestId('overview-strip-bar').first()).toBeVisible({ timeout: 10_000 })

  // Instant feedback: the placeholder is one turn.
  await expect.poll(() => page.getByTestId('turn-flag').count(), { timeout: 10_000 }).toBe(1)
  expect(await turnsCellValue(page)).toBe('1')

  // The real anchor lands → the backend retires the placeholder.
  await post(page, [
    {
      trace_id: traceId, span_id: anchorId, parent_id: null, name: 'prompt',
      start_time: '2026-05-18T08:05:00.000000', end_time: '2026-05-18T08:05:05.000000',
      status_code: 'OK', attributes: { text: TEXT, is_test: true },
    },
  ])
  await expect.poll(() => anchorFetched, { timeout: 15_000 }).toBeTruthy()

  // Still ONE turn. Before the fix this climbed to 2 and stayed there.
  await expect.poll(() => page.getByTestId('turn-flag').count(), { timeout: 5_000 }).toBe(1)
  await page.waitForTimeout(5_000)
  expect(await page.getByTestId('turn-flag').count()).toBe(1)
  expect(await turnsCellValue(page)).toBe('1')
})
