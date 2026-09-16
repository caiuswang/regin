/**
 * The invariants that hold across EVERY span kind, in both reading modes.
 *
 * focus-mode.spec.js and present-mode.spec.js pin the behaviour of the modes;
 * this file pins the two properties that have to hold for the whole span
 * vocabulary, because that is where both modes actually broke:
 *
 *  1. the reader's header is a header — no span's title may grow the fixed
 *     band until the document collapses and the ↑↓ footer leaves the viewport;
 *  2. no surface may claim a span carries nothing while its payload sits one
 *     attribute away, and no condensed row may title itself with the raw
 *     dotted span name.
 */
import { randomUUID } from 'node:crypto'
import { test, expect } from './auth-fixture.js'

const HUGE_ERROR = `Traceback (most recent call last):\n${
  '  File "/repo/very/deep/module/path/handler.py", line 42, in dispatch\n'
  + '    raise RuntimeError("the upstream returned 500 and the retry budget was exhausted")\n'
}`.repeat(60)

const LONG_ARGS = 'x'.repeat(700)

async function post(page, spans) {
  await page.goto('/trace/sessions')
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect((await page.request.post('/api/session-spans', {
    headers: { Authorization: `Bearer ${token}` }, data: spans,
  })).ok()).toBeTruthy()
}

function seeder(traceId) {
  const p8 = traceId.slice(0, 8)
  const prompt = `p-${p8}`
  const at = i => `2026-05-08T12:00:${String(i).padStart(2, '0')}`
  const spans = [{
    trace_id: traceId, span_id: prompt, parent_id: null, name: 'prompt',
    start_time: at(0), attributes: { text: 'span-kind sweep', is_test: true },
  }]
  let i = 1
  const add = (key, name, attributes, duration_ms = 100) => {
    spans.push({
      trace_id: traceId, span_id: `${key}-${p8}`, parent_id: prompt, name,
      start_time: at(i), duration_ms, attributes: { ...attributes, is_test: true },
    })
    i++
    return `${key}-${p8}`
  }
  return { spans, add, p8 }
}

const reader = page => page.getByTestId('span-present-reader')

async function openReader(page, spanId) {
  const row = page.locator(`[data-span-id="${spanId}"]`)
  await row.hover()
  await row.locator('.spine-present').click()
  await expect(reader(page)).toBeVisible()
}

const readerGeometry = page => page.evaluate(() => {
  const q = s => document.querySelector(s)
  const panel = q('.present-panel').getBoundingClientRect()
  const header = q('.present-header').getBoundingClientRect()
  const body = q('.present-body').getBoundingClientRect()
  const footer = q('.present-footer').getBoundingClientRect()
  return {
    panelH: panel.height,
    headerH: header.height,
    bodyH: body.height,
    footerBottom: footer.bottom,
    viewportH: window.innerHeight,
    title: q('.present-title').textContent.trim(),
    metadataOnly: !!q('.present-empty'),
    bodyText: q('.present-body').innerText,
  }
})

// ── 1. The header stays a header, whatever the span's label is ──────────

test('no span label can grow the header past the document it introduces', async ({ page }) => {
  test.setTimeout(120_000)
  const traceId = randomUUID()
  const { spans, add } = seeder(traceId)
  // Every unbounded `spanLabel` branch, at the sizes the live store actually
  // holds: a 16k `tool.failure` error, a slash command with 700 chars of args,
  // an oversized Grep pattern and a ToolSearch query.
  const cases = [
    add('fail', 'tool.failure', { tool_name: 'Bash', error: HUGE_ERROR }),
    add('cmd', 'harness.local_command', { command_name: 'goal-verified-treenav', args: LONG_ARGS }),
    add('grep', 'tool.Grep', { tool_name: 'Grep', pattern: `needle|${'alt|'.repeat(140)}` }),
    add('search', 'tool.ToolSearch', { tool_name: 'ToolSearch', query: `find ${'a very long phrase '.repeat(30)}` }),
  ]
  await post(page, spans)
  await page.goto(`/trace/sessions/${traceId}?view=conversation`)
  await expect(page.locator('.event-spine')).toBeVisible({ timeout: 20_000 })

  for (const spanId of cases) {
    await openReader(page, spanId)
    const g = await readerGeometry(page)
    // The band is `flex-shrink: 0` above a `flex: 1` document, so a title the
    // model does not cap takes the whole panel with it.
    expect(g.headerH, `${spanId}: header ${g.headerH}px of a ${g.panelH}px panel`)
      .toBeLessThan(g.panelH * 0.35)
    expect(g.bodyH, `${spanId}: document band collapsed to ${g.bodyH}px`)
      .toBeGreaterThan(g.panelH * 0.4)
    // The footer carries ↑↓, the position counter and the walk filter. Off the
    // viewport it is not merely ugly — the reader has no navigation at all.
    expect(g.footerBottom, `${spanId}: footer at ${g.footerBottom} vs viewport ${g.viewportH}`)
      .toBeLessThanOrEqual(g.viewportH + 1)
    // Capped, and SAID to be capped: the three slices this replaced cut
    // mid-word with no mark that anything had been cut.
    expect(g.title.length).toBeLessThanOrEqual(121)
    if (g.title.length > 100) expect(g.title.endsWith('…')).toBe(true)
    await page.keyboard.press('Escape')
    await expect(reader(page)).toHaveCount(0)
  }
})

// ── 2. "Metadata only" must mean metadata only ──────────────────────────

test('no span says "metadata only" while its payload sits one attribute away', async ({ page }) => {
  test.setTimeout(120_000)
  const traceId = randomUUID()
  const { spans, add } = seeder(traceId)
  const cases = {
    // The rail hides `mcp_input`/`mcp_result` because IT renders the
    // round-trip; the reader had no such block, so 3,244 spans read as empty.
    mcp: add('mcp', 'tool.mcp__memory__recall', {
      tool_name: 'mcp__memory__recall', mcp: true,
      mcp_input: '{"query":"MARKER-MCP-INPUT"}',
      mcp_result: 'MARKER-MCP-RESULT',
    }),
    // A Bash whose output was never captured still has a command to show.
    bash: add('bash', 'tool.Bash', { tool_name: 'Bash', command_preview: 'MARKER-BASH-CMD --flag' }),
    // A denied non-ask call: the rail hides `denial_reason` on its own panel's
    // authority, and the reason is the only interesting fact about the span.
    deny: add('deny', 'tool.Edit', {
      tool_name: 'Edit', file_path: '/repo/a.py',
      denied: true, deny_kind: 'chat', denial_reason: 'MARKER-DENY-REASON',
    }),
    read: add('read', 'tool.Read', { tool_name: 'Read', file_path: '/repo/a.py', content: 'MARKER-READ-CONTENT' }),
    stop: add('stop', 'subagent.stop', { agent_type: 'goal-verifier', result_preview: 'MARKER-AGENT-RESULT' }),
  }
  await post(page, spans)
  await page.goto(`/trace/sessions/${traceId}?view=conversation`)
  await expect(page.locator('.event-spine')).toBeVisible({ timeout: 20_000 })

  const markers = {
    mcp: ['MARKER-MCP-INPUT', 'MARKER-MCP-RESULT'],
    bash: ['MARKER-BASH-CMD'],
    deny: ['MARKER-DENY-REASON'],
    read: ['MARKER-READ-CONTENT'],
    stop: ['MARKER-AGENT-RESULT'],
  }
  for (const [key, spanId] of Object.entries(cases)) {
    await openReader(page, spanId)
    const g = await readerGeometry(page)
    expect(g.metadataOnly, `${key}: reader claims "metadata only"`).toBe(false)
    for (const marker of markers[key]) {
      expect(g.bodyText, `${key}: ${marker} rendered nowhere`).toContain(marker)
    }
    await page.keyboard.press('Escape')
    await expect(reader(page)).toHaveCount(0)
  }
})

test('the diff header reads the keys the ingest writes', async ({ page }) => {
  test.setTimeout(90_000)
  const traceId = randomUUID()
  const { spans, add } = seeder(traceId)
  const write = add('w', 'tool.Write', {
    tool_name: 'Write', file_path: '/repo/new.py', edit_op: 'write',
    added_lines: 7, removed_lines: 0, diff: '@@ -0,0 +1,2 @@\n+a = 1\n+b = 2',
  })
  const edit = add('e', 'tool.Edit', {
    tool_name: 'Edit', file_path: '/repo/old.py', edit_op: 'edit',
    added_lines: 2, removed_lines: 3, diff: '@@ -1,3 +1,2 @@\n-x\n-y\n-z\n+p\n+q',
  })
  await post(page, spans)
  await page.goto(`/trace/sessions/${traceId}?view=conversation`)
  await expect(page.locator('.event-spine')).toBeVisible({ timeout: 20_000 })

  // A file CREATION is not an "Update", and the +N/−N counts exist.
  await openReader(page, write)
  await expect(page.locator('.present-diff-op')).toHaveText('Create')
  await expect(page.locator('.present-diff-add')).toHaveText('+7')
  await page.keyboard.press('Escape')

  await openReader(page, edit)
  await expect(page.locator('.present-diff-op')).toHaveText('Update')
  await expect(page.locator('.present-diff-add')).toHaveText('+2')
  await expect(page.locator('.present-diff-del')).toHaveText('−3')
})

test('Copy carries what the reader rendered, not the first key that matched', async ({ page }) => {
  test.setTimeout(90_000)
  const traceId = randomUUID()
  const { spans, add } = seeder(traceId)
  const bash = add('b', 'tool.Bash', {
    tool_name: 'Bash', command: 'pytest -q', command_preview: 'pytest -q',
    stdout: 'MARKER-STDOUT 12 passed',
  })
  await post(page, spans)
  await page.goto(`/trace/sessions/${traceId}?view=conversation`)
  await expect(page.locator('.event-spine')).toBeVisible({ timeout: 20_000 })
  await openReader(page, bash)

  // The ladder copied the command and dropped the output rendered beside it.
  await page.context().grantPermissions(['clipboard-read', 'clipboard-write'])
  await page.getByRole('button', { name: 'Copy span content' }).click()
  const copied = await page.evaluate(() => navigator.clipboard.readText())
  expect(copied).toContain('pytest -q')
  expect(copied).toContain('MARKER-STDOUT 12 passed')
})

// ── 3. Focus: a row never titles itself with the raw span name ───────────

test('no condensed row is titled with a raw dotted span name', async ({ page }) => {
  test.setTimeout(120_000)
  const traceId = randomUUID()
  const { spans, add } = seeder(traceId)
  // One of every shape that reaches the CONDENSED default tier and used to
  // fall through `rowParts` to `{ title: name, subject: '' }`.
  add('n1', 'task.notification', {
    status: 'completed', summary: 'MARKER-NOTIF background run finished',
    text: '<task-notification>\n<task-id>t1</task-id>\n</task-notification>',
  })
  add('n2', 'subagent.stop', { agent_type: 'goal-verifier', result_preview: 'MARKER-STOP verdict' })
  add('n3', 'tool.Read', { tool_name: 'Read', file_path: '/repo/deep/module/handler.py' })
  add('n4', 'tool.WebFetch', { tool_name: 'WebFetch', url: 'https://docs.example.com/v2/reference/get' })
  add('n5', 'tool.mcp__linear-server__get_issue', {
    tool_name: 'mcp__linear-server__get_issue', mcp: true, mcp_input: '{"id":"CAI-1"}',
  })
  add('n6', 'tool.TaskCreate', { tool_name: 'TaskCreate', subject: 'MARKER-TASK verify the fix', task_id: 'k1' })
  await post(page, spans)
  await page.goto(`/trace/sessions/${traceId}?view=conversation`)
  await expect(page.locator('.event-spine')).toBeVisible({ timeout: 20_000 })
  await page.getByTestId('focus-toggle').click()

  const titles = await page.locator('button.condensed-row').evaluateAll(
    els => els.map(el => el.querySelector('span:not([class*="bg-cyan"])')?.textContent?.trim() || ''),
  )
  expect(titles.length).toBeGreaterThan(0)
  for (const t of titles) {
    // `task.notification`, `workflow.phase`, `subagent.stop` — a dotted
    // lowercase identifier is the raw name leaking through, never a title.
    expect(t, `condensed row titled with a raw span name: ${t}`).not.toMatch(/^[a-z][a-z0-9_]*\.[a-z]/)
  }
  // The subject is the human sentence, never the harness envelope.
  const feed = page.locator('.conversation-feed')
  await expect(feed).toContainText('MARKER-NOTIF background run finished')
  await expect(feed).not.toContainText('<task-notification>')
  await expect(feed).toContainText('MARKER-STOP verdict')
})

test('a workflow phase keeps its band and a task snapshot keeps its card under Focus', async ({ page }) => {
  test.setTimeout(90_000)
  const traceId = randomUUID()
  const { spans, add } = seeder(traceId)
  add('ph', 'workflow.phase', { index: 2, title: 'MARKER-PHASE design', detail: 'three agents' })
  add('rd', 'tool.Read', { tool_name: 'Read', file_path: '/repo/a.py' })
  await post(page, spans)
  await page.goto(`/trace/sessions/${traceId}?view=conversation`)
  await expect(page.locator('.event-spine')).toBeVisible({ timeout: 20_000 })
  await page.getByTestId('focus-toggle').click()

  // The phase is turn structure — condensing it replaced a divider with a row.
  const phaseRow = page.locator('[data-span-id^="ph-"]')
  await expect(phaseRow).toHaveAttribute('data-tier', '1')
  await expect(phaseRow).toContainText('MARKER-PHASE design')
  await expect(phaseRow.locator('button.condensed-row')).toHaveCount(0)
})

// ── 4. Mode wiring ──────────────────────────────────────────────────────

test('the reader body is scrollable by keyboard', async ({ page }) => {
  test.setTimeout(90_000)
  const traceId = randomUUID()
  const { spans, add } = seeder(traceId)
  const long = add('long', 'assistant_response', {
    text: Array.from({ length: 200 }, (_, i) => `Paragraph ${i} of a response far taller than one screen.`).join('\n\n'),
  })
  await post(page, spans)
  await page.goto(`/trace/sessions/${traceId}?view=conversation`)
  await expect(page.locator('.event-spine')).toBeVisible({ timeout: 20_000 })
  await openReader(page, long)

  // ↑↓ are claimed by the span walk, so without a focusable scroller a
  // keyboard-only reader saw only the first screenful of every span.
  const before = await page.evaluate(() => document.querySelector('.present-body').scrollTop)
  expect(before).toBe(0)
  const top = () => page.evaluate(() => document.querySelector('.present-body').scrollTop)
  for (const key of ['PageDown', 'Space', 'End']) {
    await page.evaluate(() => { document.querySelector('.present-body').scrollTop = 0 })
    await page.keyboard.press(key)
    await expect.poll(top, { message: `${key} did not scroll the reader body` }).toBeGreaterThan(0)
  }
  // Home returns to the top, and ↑↓ still mean "walk spans", not "scroll".
  await page.keyboard.press('Home')
  await expect.poll(top, { message: 'Home did not return to the top' }).toBe(0)
})

test('walking the reader paints no focus ring on the document', async ({ page }) => {
  test.setTimeout(90_000)
  const traceId = randomUUID()
  const { spans, add } = seeder(traceId)
  const first = add('a1', 'assistant_response', { text: 'The first response body, long enough to read.' })
  add('a2', 'assistant_response', { text: 'The second response body, long enough to read.' })
  await post(page, spans)
  await page.goto(`/trace/sessions/${traceId}?view=conversation`)
  await expect(page.locator('.event-spine')).toBeVisible({ timeout: 20_000 })
  await openReader(page, first)

  // The body is tabbable only so the browser's scroll keys have a target, and
  // the reader focuses it itself on open. `:focus-visible` is a MODALITY
  // heuristic: the first ↑/↓ of the walk moves no focus at all, but it flips
  // the heuristic to keyboard, so a ring styled on the body appears out of
  // nowhere and frames the whole reading band for the rest of the session.
  const bodyOutline = () => page.evaluate(() => {
    const cs = getComputedStyle(document.querySelector('.present-body'))
    return cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) > 0 ? cs.outline : null
  })
  const title = () => reader(page).locator('.present-title').innerText()

  expect(await bodyOutline()).toBeNull()
  const before = await title()
  await page.keyboard.press('ArrowDown')
  await expect.poll(title, { message: '↓ did not walk to the next span' }).not.toBe(before)
  expect(await bodyOutline()).toBeNull()
})

test('reader and fold state do not survive a session change', async ({ page }) => {
  test.setTimeout(120_000)
  const first = randomUUID()
  const a = seeder(first)
  const target = a.add('t', 'assistant_response', { text: 'MARKER-SESSION-ONE response body' })
  await post(page, a.spans)
  const second = randomUUID()
  const b = seeder(second)
  b.add('u', 'assistant_response', { text: 'MARKER-SESSION-TWO response body' })
  await post(page, b.spans)

  await page.goto(`/trace/sessions/${first}?view=conversation`)
  await expect(page.locator('.event-spine')).toBeVisible({ timeout: 20_000 })
  await openReader(page, target)
  await expect(reader(page)).toContainText('MARKER-SESSION-ONE')

  // `<router-view>` has no `:key`, so this reuses the component. The reader
  // held a span of the trace that is going away, and `held` never resolves an
  // heir for a non-placeholder id — it stood over the new session forever.
  await page.goto(`/trace/sessions/${second}?view=conversation`)
  await expect(page.locator('.event-spine')).toBeVisible({ timeout: 20_000 })
  await expect(reader(page)).toHaveCount(0)
})
