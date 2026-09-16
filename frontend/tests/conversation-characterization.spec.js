/**
 * Characterization of SessionConversationView's rendered surface.
 *
 * Written to pin the component AS IT IS before it is split into child
 * components: every assertion here describes observed behaviour, not desired
 * behaviour. Assertions stay on visible text, roles, testids, counts and order
 * so an extraction that preserves the rendering passes unchanged.
 *
 * Header-count-vs-rendered-rows agreement is NOT re-asserted here —
 * `conversation-event-count.spec.js` owns that invariant. This file pins the
 * order and the per-row identity instead.
 */
import { randomUUID } from 'node:crypto'
import { test, expect } from './auth-fixture.js'

const PROMPT_TAIL = 'PROMPT_TAIL_MARKER'
// 20 lines: past promptPreviewText's 16-line cap, so the collapsed card shows
// a preview and the tail only appears once the body is expanded.
const LONG_PROMPT = [
  'FIRST_TURN_PROMPT',
  ...Array.from({ length: 18 }, (_, i) => `line ${i + 1} of the first prompt`),
  PROMPT_TAIL,
].join('\n')

async function authHeaders(page) {
  await page.goto('/trace/sessions')
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect(token).toBeTruthy()
  return { Authorization: `Bearer ${token}` }
}

async function postSpans(page, headers, spans) {
  expect((await page.request.post('/api/session-spans', { headers, data: spans })).ok())
    .toBeTruthy()
}

async function postTurns(page, headers, turns) {
  expect((await page.request.post('/api/turn-usage', { headers, data: turns })).ok())
    .toBeTruthy()
}

async function openConversation(page, traceId) {
  await page.goto(`/trace/sessions/${traceId}?view=conversation`)
}

const promptCard = (page, spanId) => page.locator(`[data-span-id="${spanId}"]`)
// The turn header's size/disclosure label: `N events · ▾`, or a bare glyph
// when the turn has no rows the spine would render.
const sizeLabel = (card) => card.getByText(/^(\d+ events? · )?[▴▾]$/)

// Two turns: the first collapses (chips), the second is auto-expanded (spine).
async function seedTwoTurns(page, { withTurnUsage = false } = {}) {
  const traceId = randomUUID()
  const sfx = traceId.slice(0, 8)
  const id = (n) => `${n}-${sfx}`
  const p1 = id('p1')
  const p2 = id('p2')
  const rows = [id('e1'), id('e2'), id('e3'), id('e4'), id('e5')]
  const spans = [
    { trace_id: traceId, span_id: p1, parent_id: null, name: 'prompt',
      start_time: '2026-05-08T10:00:00', attributes: { text: LONG_PROMPT, is_test: true } },
    { trace_id: traceId, span_id: id('a1'), parent_id: p1, name: 'tool.Read',
      start_time: '2026-05-08T10:00:01',
      attributes: { tool_name: 'Read', file_path: '/repo/alpha.py', is_test: true } },
    { trace_id: traceId, span_id: id('a2'), parent_id: p1, name: 'tool.Read',
      start_time: '2026-05-08T10:00:02',
      attributes: { tool_name: 'Read', file_path: '/repo/beta.py', is_test: true } },
    { trace_id: traceId, span_id: id('a3'), parent_id: p1, name: 'tool.Bash',
      start_time: '2026-05-08T10:00:03',
      attributes: { tool_name: 'Bash', command: 'echo chips', command_preview: 'echo chips', is_test: true } },
    { trace_id: traceId, span_id: id('a4'), parent_id: p1, name: 'skill.read',
      start_time: '2026-05-08T10:00:04', attributes: { skill_id: 'doc-hygiene', is_test: true } },
    { trace_id: traceId, span_id: id('a5'), parent_id: p1, name: 'rule.check',
      start_time: '2026-05-08T10:00:05',
      attributes: { file_path: '/repo/alpha.py', applicable_rules: [
        { id: 'no-raw-sqlite', violated: false, summary: 'raw sqlite3 outside the ORM' },
      ], is_test: true } },

    { trace_id: traceId, span_id: p2, parent_id: null, name: 'prompt',
      start_time: '2026-05-08T10:10:00', attributes: { text: 'SECOND_TURN_PROMPT', is_test: true } },
    { trace_id: traceId, span_id: rows[0], parent_id: p2, name: 'assistant.thinking',
      start_time: '2026-05-08T10:10:01', duration_ms: 1200,
      attributes: { thinking_text: 'ROW1_THINKING', is_test: true } },
    { trace_id: traceId, span_id: rows[1], parent_id: p2, name: 'tool.Read',
      start_time: '2026-05-08T10:10:02',
      attributes: { tool_name: 'Read', file_path: '/repo/row2_gamma.py', is_test: true } },
    { trace_id: traceId, span_id: rows[2], parent_id: p2, name: 'tool.Bash',
      start_time: '2026-05-08T10:10:03',
      attributes: { tool_name: 'Bash', command: 'echo ROW3_BASH', command_preview: 'echo ROW3_BASH', is_test: true } },
    { trace_id: traceId, span_id: rows[3], parent_id: p2, name: 'tool.Edit',
      start_time: '2026-05-08T10:10:04',
      attributes: { tool_name: 'Edit', file_path: '/repo/row4_delta.py', additions: 1, deletions: 1,
        diff: '@@ -1 +1 @@\n-old = 1\n+new = 2', is_test: true } },
    { trace_id: traceId, span_id: rows[4], parent_id: p2, name: 'assistant_response',
      start_time: '2026-05-08T10:10:05', attributes: { text: 'ROW5_RESPONSE', is_test: true } },
  ]
  const headers = await authHeaders(page)
  await postSpans(page, headers, spans)
  if (withTurnUsage) {
    await postTurns(page, headers, [
      { trace_id: traceId, turn_uuid: id('tu1'), turn_index: 1,
        timestamp: '2026-05-08T10:10:02', input_tokens: 1000,
        cache_creation_tokens: 500, output_tokens: 200, context_used_tokens: 30000 },
      { trace_id: traceId, turn_uuid: id('tu2'), turn_index: 2,
        timestamp: '2026-05-08T10:10:06', input_tokens: 2000,
        cache_creation_tokens: 0, output_tokens: 300, context_used_tokens: 40000 },
    ])
  }
  await openConversation(page, traceId)
  await expect(page.locator('.event-spine')).toBeVisible({ timeout: 20_000 })
  return { traceId, p1, p2, rows }
}

test.describe('user prompt card', () => {
  test('label, avatar, clock and per-turn event count', async ({ page }) => {
    const { p1, p2 } = await seedTwoTurns(page)

    await expect(page.getByTestId('conversation-prompt-label')).toHaveText([
      'Prompt · Turn 1', 'Prompt · Turn 2',
    ])
    await expect(promptCard(page, p1).getByText('You', { exact: true })).toBeVisible()
    await expect(promptCard(page, p1)).toContainText('10:00:00')
    await expect(promptCard(page, p2)).toContainText('10:10:00')

    // The collapsed turn still advertises its size; the open one shows ▴.
    await expect(sizeLabel(promptCard(page, p1))).toHaveText('5 events · ▾')
    await expect(sizeLabel(promptCard(page, p2))).toHaveText('5 events · ▴')
  })

  test('the count is singular at 1 and omitted entirely at 0', async ({ page }) => {
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    const p1 = `p1-${sfx}`
    const p2 = `p2-${sfx}`
    const headers = await authHeaders(page)
    await postSpans(page, headers, [
      { trace_id: traceId, span_id: p1, parent_id: null, name: 'prompt',
        start_time: '2026-05-08T10:00:00', attributes: { text: 'one event turn', is_test: true } },
      { trace_id: traceId, span_id: `s1-${sfx}`, parent_id: p1, name: 'tool.Read',
        start_time: '2026-05-08T10:00:01',
        attributes: { tool_name: 'Read', file_path: '/repo/only.py', is_test: true } },
      { trace_id: traceId, span_id: p2, parent_id: null, name: 'prompt',
        start_time: '2026-05-08T10:10:00', attributes: { text: 'zero event turn', is_test: true } },
      // No branch in the card chain → renders nothing and counts for nothing.
      { trace_id: traceId, span_id: `s2-${sfx}`, parent_id: p2, name: 'cwd.changed',
        start_time: '2026-05-08T10:10:01', attributes: { cwd: '/repo', is_test: true } },
    ])
    await openConversation(page, traceId)
    await expect(promptCard(page, p2)).toBeVisible({ timeout: 20_000 })

    await expect(sizeLabel(promptCard(page, p1))).toHaveText('1 event · ▾')
    await expect(sizeLabel(promptCard(page, p2))).toHaveText('▴')
    await expect(page.locator('.event-spine')).toHaveCount(0)
  })

  test('clicking the card toggles the turn between chips and spine', async ({ page }) => {
    const { p2, rows } = await seedTwoTurns(page)
    const card = promptCard(page, p2)
    const label = card.getByTestId('conversation-prompt-label')

    await expect(page.locator('.event-spine-row')).toHaveCount(rows.length)

    await label.click()
    await expect(sizeLabel(card)).toHaveText('5 events · ▾')
    await expect(page.locator('.event-spine')).toHaveCount(0)
    // Collapsed, the turn shows its chip strip and its own reopen trigger.
    await expect(page.getByRole('button', { name: 'show detail ▾' })).toHaveCount(2)

    await label.click()
    await expect(sizeLabel(card)).toHaveText('5 events · ▴')
    await expect(page.locator('.event-spine-row')).toHaveCount(rows.length)
  })

  test('“Show full prompt” expands the body without toggling the turn', async ({ page }) => {
    const { p1 } = await seedTwoTurns(page)
    const card = promptCard(page, p1)

    await expect(card).toContainText('FIRST_TURN_PROMPT')
    await expect(card).not.toContainText(PROMPT_TAIL)

    await card.getByRole('button', { name: 'Show full prompt' }).click()
    await expect(card).toContainText(PROMPT_TAIL)
    // The turn itself is untouched — still collapsed, still counting 5.
    await expect(sizeLabel(card)).toHaveText('5 events · ▾')

    await card.getByRole('button', { name: 'Collapse' }).click()
    await expect(card).not.toContainText(PROMPT_TAIL)
    await expect(sizeLabel(card)).toHaveText('5 events · ▾')
  })

  test('Copy hands the prompt text to the clipboard and acknowledges in the button', async ({ page }) => {
    // Stubbing writeText keeps the assertion on the wiring rather than on
    // browser clipboard permissions.
    await page.addInitScript(() => {
      window.__copied = []
      Object.defineProperty(navigator, 'clipboard', {
        configurable: true,
        value: { writeText: async (text) => { window.__copied.push(text) } },
      })
    })
    const { p2 } = await seedTwoTurns(page)
    const card = promptCard(page, p2)

    await card.getByRole('button', { name: 'Copy' }).click()
    await expect(card.getByRole('button', { name: 'Copied' })).toBeVisible()
    expect(await page.evaluate(() => window.__copied)).toEqual(['SECOND_TURN_PROMPT'])
  })

  test('the pin control toggles aria-pressed without folding the turn', async ({ page }) => {
    const { p1 } = await seedTwoTurns(page)
    const card = promptCard(page, p1)
    const pin = card.getByTitle('Pin to view (hold position across updates)')

    await expect(pin).toHaveAttribute('aria-pressed', 'false')
    await pin.click()
    const pinned = card.getByTitle('Unpin from view')
    await expect(pinned).toHaveAttribute('aria-pressed', 'true')
    await expect(sizeLabel(card)).toHaveText('5 events · ▾')

    await pinned.click()
    await expect(card.getByTitle('Pin to view (hold position across updates)'))
      .toHaveAttribute('aria-pressed', 'false')
  })
})

test('a collapsed turn renders one chip per tool family, in first-seen order', async ({ page }) => {
  await seedTwoTurns(page)
  // The chip strip is the row that carries the collapsed turn's reopen trigger.
  const strip = page.getByRole('button', { name: 'show detail ▾' }).locator('..')
  const chips = strip.locator(':scope > span')

  await expect(chips).toHaveCount(4)
  await expect(chips).toHaveText(['Read×2', 'Shell', 'doc-hygiene', 'rule'])
})

test('the expanded turn body renders its events in chronological order, one row each', async ({ page }) => {
  const { rows } = await seedTwoTurns(page)
  const spineRows = page.locator('.event-spine-row')

  await expect(spineRows).toHaveCount(rows.length)
  expect(await spineRows.evaluateAll(
    (els) => els.map((el) => el.getAttribute('data-span-id')))).toEqual(rows)

  // Each row is the card for its own span, not a neighbour's.
  await expect(spineRows.nth(0)).toContainText('ROW1_THINKING')
  await expect(spineRows.nth(1)).toContainText('row2_gamma.py')
  await expect(spineRows.nth(2)).toContainText('echo ROW3_BASH')
  await expect(spineRows.nth(3)).toContainText('row4_delta.py')
  await expect(spineRows.nth(4)).toContainText('ROW5_RESPONSE')
})

test.describe('turn footer', () => {
  test('rolls up the turn-usage figures and drills down per API turn', async ({ page }) => {
    const { p2 } = await seedTwoTurns(page, { withTurnUsage: true })

    // Only the turn that owns the usage rows gets the filled bar.
    await expect(page.getByText('Turn usage')).toHaveCount(1)
    const footer = page.getByText('Turn usage').locator('..').locator('..')
    await expect(footer).toContainText('2 turns')
    await expect(footer).toContainText('in 3.5k')
    await expect(footer).toContainText('out 500')

    await footer.getByRole('button', { name: /2 turns/ }).click()
    await expect(footer).toContainText('#1')
    await expect(footer).toContainText('↑1.5k')
    await expect(footer).toContainText('↓200')
    await expect(footer).toContainText('#2')
    await expect(footer).toContainText('↑2k')
    await expect(footer).toContainText('↓300')

    // The footer also carries the open turn's collapse trigger.
    await footer.getByRole('button', { name: 'hide detail ▴' }).click()
    await expect(sizeLabel(promptCard(page, p2))).toHaveText('5 events · ▾')
  })
})

test.describe('compaction boundary divider', () => {
  async function seedCompaction(page) {
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    const summary = `${'compacted transcript summary. '.repeat(10)}SUMMARY_TAIL_MARKER`
    const headers = await authHeaders(page)
    await postSpans(page, headers, [
      { trace_id: traceId, span_id: `p-${sfx}`, parent_id: null, name: 'prompt',
        start_time: '2026-05-08T10:00:00', attributes: { text: 'compaction demo', is_test: true } },
      { trace_id: traceId, span_id: `pre-${sfx}`, parent_id: null, name: 'compact.pre',
        start_time: '2026-05-08T10:05:00', attributes: { trigger: 'auto', is_test: true } },
      { trace_id: traceId, span_id: `post-${sfx}`, parent_id: null, name: 'compact.post',
        start_time: '2026-05-08T10:05:30',
        attributes: { trigger: 'auto', custom_instructions: 'keep the acceptance checklist',
          summary, summary_chars: 4321, is_test: true } },
    ])
    await postTurns(page, headers, [
      { trace_id: traceId, turn_uuid: `tu1-${sfx}`, turn_index: 1,
        timestamp: '2026-05-08T10:04:00', context_used_tokens: 180000 },
      { trace_id: traceId, turn_uuid: `tu2-${sfx}`, turn_index: 2,
        timestamp: '2026-05-08T10:06:00', context_used_tokens: 42000 },
    ])
    await openConversation(page, traceId)
    return { traceId, summary }
  }

  const divider = (page, text) =>
    page.locator('[role="button"]').filter({ hasText: text })

  test('the pre/post pills carry trigger, reclaim and clock', async ({ page }) => {
    await seedCompaction(page)
    const pre = divider(page, '▼ context compacting')
    const post = divider(page, '▲ context compacted')

    await expect(pre).toBeVisible({ timeout: 20_000 })
    await expect(pre).toContainText('auto')
    await expect(pre).toContainText('10:05:00')
    await expect(pre).not.toContainText('freed')

    await expect(post).toContainText('auto')
    await expect(post).toContainText('freed ~138k')
    await expect(post).toContainText('10:05:30')
  })

  test('custom instructions and the summary disclosure', async ({ page }) => {
    const { summary } = await seedCompaction(page)
    const post = divider(page, '▲ context compacted')
    await expect(post).toBeVisible({ timeout: 20_000 })

    await expect(post).toContainText('“keep the acceptance checklist”')
    await expect(post).toContainText('summary:')
    await expect(post).toContainText(`${summary.slice(0, 180)}…`)
    await expect(post).toContainText('(4,321 chars)')
    await expect(post).not.toContainText('SUMMARY_TAIL_MARKER')

    await post.getByRole('button', { name: 'show full ▾' }).click()
    await expect(post).toContainText('SUMMARY_TAIL_MARKER')
    await expect(post).toContainText('(4,321 chars)')

    await post.getByRole('button', { name: 'collapse ▴' }).click()
    await expect(post).not.toContainText('SUMMARY_TAIL_MARKER')
  })
})

test('the rewind boundary divider counts what it discarded', async ({ page }) => {
  const traceId = randomUUID()
  const sfx = traceId.slice(0, 8)
  const headers = await authHeaders(page)
  await postSpans(page, headers, [
    { trace_id: traceId, span_id: `p-${sfx}`, parent_id: null, name: 'prompt',
      start_time: '2026-05-08T10:00:00', attributes: { text: 'rewind demo', is_test: true } },
    { trace_id: traceId, span_id: `rw-${sfx}`, parent_id: null, name: 'rewind',
      start_time: '2026-05-08T10:05:00',
      attributes: { abandoned_prompt_count: 2, abandoned_span_count: 3,
        rolled_back_count: 1, is_test: true } },
  ])
  await openConversation(page, traceId)

  const card = page.locator('[role="button"]').filter({ hasText: '↩ rewound' })
  await expect(card).toBeVisible({ timeout: 20_000 })
  await expect(card).toContainText('2 prompts discarded')
  await expect(card).toContainText('1 file rolled back')
  await expect(card).toContainText('10:05:00')
  await expect(card.getByRole('button', { name: 'show rolled-back files ▾' })).toBeVisible()

  await card.getByRole('button', { name: 'show discarded turns ▾' }).click()
  await expect(card.getByRole('button', { name: 'hide discarded turns ▴' })).toBeVisible()
})

test('a standalone root row shows its dot colour, clock and label', async ({ page }) => {
  const traceId = randomUUID()
  const sfx = traceId.slice(0, 8)
  const label = 'STANDALONE_MARKER background task finished'
  const headers = await authHeaders(page)
  await postSpans(page, headers, [
    // Same start_time as the prompt, posted first: the server windows the map
    // at the earliest turn anchor (a strictly-earlier root would be paged out
    // entirely), and `_graft_orphans_under_prompt` walks in (start_time, id)
    // order — so this one is seen before the prompt and survives as a root,
    // which is the legacy shape the standalone branch exists for.
    { trace_id: traceId, span_id: `bg-${sfx}`, parent_id: null, name: 'task.notification',
      start_time: '2026-05-08T10:00:00', attributes: { summary: label, is_test: true } },
    { trace_id: traceId, span_id: `p-${sfx}`, parent_id: null, name: 'prompt',
      start_time: '2026-05-08T10:00:00', attributes: { text: 'standalone demo', is_test: true } },
  ])
  await openConversation(page, traceId)

  const row = page.locator('div').filter({ hasText: label }).last()
  await expect(row).toBeVisible({ timeout: 20_000 })
  await expect(row).toContainText('10:00:00')
  await expect(row).toContainText(label)
  await expect(row.locator('span').first()).toHaveClass(/bg-amber-500/)
})

test.describe('empty states', () => {
  // The scoped states render as a full-feed takeover below the xl split floor;
  // at ≥xl the same states live in the companion pane instead.
  test.use({ viewport: { width: 1152, height: 900 } })

  const AGENT_FILE = 'src/AGENT_INTERNAL_MARKER.js'

  async function seedScoped(page, { emptyAgent = false } = {}) {
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    const agId = emptyAgent ? `ag-empty-${sfx}` : `ag-run-${sfx}`
    const startId = `substart-${sfx}`
    const spans = [
      { trace_id: traceId, span_id: `p-${sfx}`, parent_id: null, name: 'prompt',
        start_time: '2026-05-08T10:00:00', attributes: { text: 'scope demo prompt', is_test: true } },
      { trace_id: traceId, span_id: startId, parent_id: `p-${sfx}`, name: 'subagent.start',
        start_time: '2026-05-08T10:00:01',
        attributes: { agent_type: 'explorer', agent_id: agId, is_test: true } },
      { trace_id: traceId, span_id: `substop-${sfx}`, parent_id: `p-${sfx}`, name: 'subagent.stop',
        start_time: '2026-05-08T10:00:09',
        attributes: { agent_type: 'explorer', agent_id: agId, result_preview: 'done', is_test: true } },
    ]
    if (!emptyAgent) {
      spans.push({ trace_id: traceId, span_id: `int-${sfx}`, parent_id: startId, name: 'tool.Read',
        start_time: '2026-05-08T10:00:05',
        attributes: { tool_name: 'Read', file_path: AGENT_FILE, agent_id: agId, is_test: true } })
    }
    const headers = await authHeaders(page)
    await postSpans(page, headers, spans)
    return { traceId, agId }
  }

  test('a finished agent with no captured spans reads as terminally empty', async ({ page }) => {
    const { traceId, agId } = await seedScoped(page, { emptyAgent: true })
    await page.goto(`/trace/sessions/${traceId}?view=conversation&agent=${agId}`)

    await expect(page.getByTestId('trace-scope-empty'))
      .toHaveText('no spans captured for this agent', { timeout: 20_000 })
    await expect(page.getByTestId('trace-scope-loading')).toHaveCount(0)
    await expect(page.getByTestId('trace-scope-unreachable')).toHaveCount(0)
  })

  test('a subtree fetch in flight shows the spinner, never a false empty', async ({ page }) => {
    const { traceId, agId } = await seedScoped(page)
    let release
    const gate = new Promise((resolve) => { release = resolve })
    // Both traces: a scoped agent's spans are served by its own
    // `sa-<agent_id>` trace, so the fetch to hold is that one's.
    await page.route('**/api/sessions/*/spans/**/children*', async (route) => {
      await gate
      await route.continue()
    })

    await page.goto(`/trace/sessions/${traceId}?view=conversation&agent=${agId}`)
    const loading = page.getByTestId('trace-scope-loading')
    await expect(loading).toBeVisible({ timeout: 20_000 })
    await expect(loading).toContainText('Loading agent spans…')
    await expect(page.getByTestId('trace-scope-empty')).toHaveCount(0)
    await expect(page.getByTestId('trace-scope-unreachable')).toHaveCount(0)

    release()
    await expect(page.getByText('AGENT_INTERNAL_MARKER').first()).toBeVisible({ timeout: 20_000 })
    await expect(loading).toHaveCount(0)
  })

  test('an agent whose spans never load reads as unreachable', async ({ page }) => {
    const { traceId, agId } = await seedScoped(page)
    // The roster still counts the agent's span server-side, but no load ever
    // delivers it — the "load earlier history" state.
    const withhold = async (route) => {
      const res = await route.fetch()
      const body = await res.json()
      if (Array.isArray(body.spans)) {
        body.spans = body.spans.filter((s) => s.attributes?.agent_id !== agId)
      }
      await route.fulfill({ response: res, json: body })
    }
    await page.route('**/map**', withhold)
    await page.route('**/children**', withhold)
    // The agent's own trace is the other way its spans could arrive — serve it
    // empty so nothing reaches the scope from there either.
    await page.route(`**/api/sessions/sa-${agId}/**`, async (route) => {
      await route.fulfill({ json: { spans: [] } })
    })

    await page.goto(`/trace/sessions/${traceId}?view=conversation&agent=${agId}`)
    await expect(page.getByTestId('trace-scope-unreachable'))
      .toHaveText('agent spans not loaded — load earlier history to view', { timeout: 20_000 })
    await expect(page.getByTestId('trace-scope-empty')).toHaveCount(0)
    await expect(page.getByTestId('trace-scope-loading')).toHaveCount(0)
  })

  test('an unscoped feed with no entries says so in plain words', async ({ page }) => {
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    const headers = await authHeaders(page)
    await postSpans(page, headers, [
      { trace_id: traceId, span_id: `p-${sfx}`, parent_id: null, name: 'prompt',
        start_time: '2026-05-08T10:00:00', attributes: { text: 'empty feed demo', is_test: true } },
    ])
    // The session exists and loads; the feed just receives no spans.
    const strip = async (route) => {
      const res = await route.fetch()
      const body = await res.json()
      if (Array.isArray(body.spans)) body.spans = []
      await route.fulfill({ response: res, json: body })
    }
    await page.route('**/map**', strip)
    await page.route('**/children**', strip)

    await openConversation(page, traceId)
    await expect(page.getByText('No events recorded for this session.'))
      .toBeVisible({ timeout: 20_000 })
  })
})
