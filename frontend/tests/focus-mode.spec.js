/**
 * Focus mode: the conversation feed's reading-density control.
 *
 * The invariants worth a test are the ones that quietly break: the tier a span
 * renders at, that a hand-opened body survives a Focus flip, and that
 * expanding the fold restores spans to their own place on the spine rather
 * than piling them at the end of the turn. The header's `shown · folded`
 * arithmetic is pinned next door in conversation-event-count.spec.js.
 */
import { randomUUID } from 'node:crypto'
import { test, expect } from './auth-fixture.js'

async function seedTurn(page) {
  const traceId = randomUUID()
  const p8 = traceId.slice(0, 8)
  const prompt = `p-${p8}`
  const at = i => `2026-05-08T12:00:${String(i).padStart(2, '0')}`
  const spans = [{ trace_id: traceId, span_id: prompt, parent_id: null, name: 'prompt',
    start_time: at(0), attributes: { text: 'focus mode behaviour', is_test: true } }]
  let i = 1
  const add = (name, attributes, duration_ms) => {
    spans.push({ trace_id: traceId, span_id: `s${i}-${p8}`, parent_id: prompt, name,
      start_time: at(i), duration_ms, attributes: { ...attributes, is_test: true } })
    i++
  }
  add('assistant.thinking', { thinking_text: 'weighing the fold' }, 11_000)
  add('tool.Read', { tool_name: 'Read', file_path: '/repo/dispatch.js' }, 120)
  add('rule.check', { rule_id: 'mirrors-card-chain', file_path: '/repo/dispatch.js' }, 40)
  add('tool.Bash', { tool_name: 'Bash', command: 'pnpm vitest run src/conversation',
    command_preview: 'pnpm vitest run src/conversation', stdout: 'MARKER 126 passed' }, 4200)
  // The only other span shape that reaches Tier 2 WITH a condensed body — the
  // Read/Grep/Glob/MCP rows carry no output at all, so without this the
  // hand-opened-body invariant has nothing left to exercise.
  // Real shape, not a synthetic one: a `task.notification` carries the raw
  // harness envelope in `text` AND the human sentence in `summary`. Seeding
  // only `text` is what let the condensed row open the envelope unnoticed.
  add('task.notification', { status: 'killed', summary: 'MARKER background run stopped',
    text: '<task-notification>\n<task-id>t1</task-id>\n</task-notification>' }, 30)
  add('tool.Edit', { tool_name: 'Edit', file_path: '/repo/a.py', diff: '+ x\n- y' }, 210)

  await page.goto('/trace/sessions')
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect((await page.request.post('/api/session-spans', {
    headers: { Authorization: `Bearer ${token}` }, data: spans,
  })).ok()).toBeTruthy()
  await page.goto(`/trace/sessions/${traceId}?view=conversation`)
  await expect(page.locator('.event-spine')).toBeVisible({ timeout: 20_000 })
  return traceId
}

const rowFor = (page, tier) => page.locator(`.event-spine-row[data-tier="${tier}"]`)

test('tiers, fold restore-in-place, and a hand-opened body that survives the flip', async ({ page }) => {
  test.setTimeout(60_000)
  await seedTurn(page)

  // Focus off: every dispatched span is a full card, nothing is tiered down.
  await expect(page.getByTestId('fold-strip')).toHaveCount(0)
  await expect(rowFor(page, 2)).toHaveCount(0)

  await page.getByTestId('focus-toggle').click()

  // thinking + rule.check fold; Read + Bash + the notification condense; Edit
  // stays a full card.
  const strip = page.getByTestId('fold-strip')
  await expect(strip).toContainText('background events')
  await expect(strip).not.toContainText(/\d/)
  await expect(rowFor(page, 2)).toHaveCount(3)
  await expect(rowFor(page, 1)).toHaveCount(1)
  await expect(rowFor(page, 3)).toHaveCount(0)

  // Bash is tiered down but NOT re-rendered: its card already rests as a
  // one-liner, and only the card has somewhere to put the dark terminal panel,
  // the copy buttons and the truncation badges. Focus swapping in the generic
  // condensed row threw all of that away for no density gain.
  const bash = rowFor(page, 2).filter({ hasText: 'Shell' })
  await expect(bash).toContainText('pnpm vitest run src/conversation')
  await expect(bash.locator('button.condensed-row')).toHaveCount(0)
  await expect(page.getByText('MARKER 126 passed')).toHaveCount(0)
  await bash.click()
  await expect(page.getByText('MARKER 126 passed')).toBeVisible()
  // The real card, not a <pre>: the terminal panel labels its sections.
  await expect(bash.locator('.code-surface')).toBeVisible()
  await expect(bash).toContainText('command')
  await expect(bash).toContainText('stdout')

  // Its open state lives in `folding`, shared with Focus-off, so the flip
  // leaves it open exactly as a hand-opened condensed body does.
  await page.getByTestId('focus-toggle').click()
  await page.getByTestId('focus-toggle').click()
  await expect(page.getByText('MARKER 126 passed')).toBeVisible()

  // A condensed row never titles itself with the raw span name, and never
  // opens the harness envelope: the row reads the verb + the human sentence,
  // and its caret opens that sentence, not the `<task-notification>` XML.
  const note = rowFor(page, 2).filter({ hasText: 'Notification' })
  await expect(note).toHaveCount(1)
  await expect(note).not.toContainText('task.notification')
  await expect(note).toContainText('MARKER background run stopped')

  // Manual outranks tier, for the rows that DO condense.
  await note.locator('button.condensed-row').click()
  const noteBody = note.locator('pre')
  await expect(noteBody).toBeVisible()
  await expect(noteBody).not.toContainText('<task-notification>')
  await page.getByTestId('focus-toggle').click()
  await page.getByTestId('focus-toggle').click()
  await expect(note.locator('pre')).toBeVisible()

  // Expanding the strip restores the folded spans to their own positions —
  // thinking before the Read it preceded, not appended after the turn.
  await strip.click()
  await expect(rowFor(page, 3)).toHaveCount(2)
  // Once the strip is open those rows are on screen, so the header must stop
  // calling them set aside.
  await expect(page.getByTestId('turn-set-aside')).toHaveText('all spans shown')
  // The strip is an anchor, not a container: it holds the position of the
  // turn's FIRST folded span, and the revealed spans keep their own places.
  const order = await page.evaluate(() => [...document.querySelectorAll('.event-spine-row')]
    .map(el => el.getAttribute('data-tier') ?? 'strip'))
  expect(order).toEqual(['strip', '3', '2', '3', '2', '2', '1'])
})

test('the mode persists across a reload', async ({ page }) => {
  test.setTimeout(60_000)
  const traceId = await seedTurn(page)
  await page.getByTestId('focus-toggle').click()
  await expect(page.getByTestId('focus-toggle')).toHaveAttribute('aria-pressed', 'true')

  await page.goto(`/trace/sessions/${traceId}?view=conversation`)
  await expect(page.locator('.event-spine')).toBeVisible({ timeout: 20_000 })
  await expect(page.getByTestId('focus-toggle')).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByTestId('fold-strip')).toBeVisible()
})

test('C and F toggle their own modes, and are inert while typing', async ({ page }) => {
  test.setTimeout(60_000)
  await seedTurn(page)
  const condensed = page.getByTestId('focus-toggle')
  const focus = page.getByTestId('latest-toggle')
  const full = page.getByTestId('detail-full')
  await expect(full).toHaveAttribute('aria-pressed', 'true')

  // Each key toggles against Full, so the same keystroke undoes itself.
  await page.keyboard.press('c')
  await expect(condensed).toHaveAttribute('aria-pressed', 'true')
  await page.keyboard.press('c')
  await expect(full).toHaveAttribute('aria-pressed', 'true')

  await page.keyboard.press('f')
  await expect(focus).toHaveAttribute('aria-pressed', 'true')
  // The modes are exclusive: one enum, never two booleans.
  await expect(condensed).toHaveAttribute('aria-pressed', 'false')
  await page.keyboard.press('c')
  await expect(condensed).toHaveAttribute('aria-pressed', 'true')
  await expect(focus).toHaveAttribute('aria-pressed', 'false')
  await page.keyboard.press('c')

  // A shortcut that fires from inside a text field eats the character and
  // flips a mode the reader never asked for.
  const search = page.getByPlaceholder(/search/i).first()
  if (await search.count()) {
    await search.fill('')
    await search.press('f')
    await expect(full).toHaveAttribute('aria-pressed', 'true')
    await expect(search).toHaveValue('f')
  }
})

/**
 * Regressions from the verify round. Each of these was a dead branch that the
 * green suite happily stepped over, because nothing seeded the data shape that
 * would have exercised it.
 */
test('a violating rule.check surfaces as the strip\'s warning badge', async ({ page }) => {
  test.setTimeout(60_000)
  const traceId = randomUUID()
  const p8 = traceId.slice(0, 8)
  const prompt = `p-${p8}`
  const at = i => `2026-05-08T13:00:${String(i).padStart(2, '0')}`
  // A rule.check records a hit in `attributes.status`; its `status_code` stays
  // 'OK' even then, so a status-code predicate can never see it.
  const spans = [
    { trace_id: traceId, span_id: prompt, parent_id: null, name: 'prompt',
      start_time: at(0), attributes: { text: 'violation badge', is_test: true } },
    { trace_id: traceId, span_id: `v1-${p8}`, parent_id: prompt, name: 'rule.check',
      start_time: at(1), status_code: 'OK',
      attributes: { status: 'violation', violating_rule_count: 1, rule_id: 'no-raw-button',
        file_path: '/repo/a.vue', is_test: true } },
    { trace_id: traceId, span_id: `v2-${p8}`, parent_id: prompt, name: 'rule.check',
      start_time: at(2), status_code: 'OK',
      attributes: { status: 'ok', file_path: '/repo/b.vue', is_test: true } },
    { trace_id: traceId, span_id: `v3-${p8}`, parent_id: prompt, name: 'tool.Read',
      start_time: at(3), attributes: { tool_name: 'Read', file_path: '/repo/a.vue', is_test: true } },
  ]
  await page.goto('/trace/sessions')
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect((await page.request.post('/api/session-spans', {
    headers: { Authorization: `Bearer ${token}` }, data: spans,
  })).ok()).toBeTruthy()
  await page.goto(`/trace/sessions/${traceId}?view=conversation`)
  await expect(page.locator('.event-spine')).toBeVisible({ timeout: 20_000 })
  await page.getByTestId('focus-toggle').click()

  const strip = page.getByTestId('fold-strip')
  await expect(strip).toContainText('background events')
  // One of the two folded checks found something; the other did not. The mark
  // is bare — the tally lives in its tooltip, because a numeral on the strip
  // would be describing spans the reader cannot see.
  await expect(strip).toContainText('⚠')
  await expect(strip.locator('[title$="in this turn"]')).toHaveAttribute(
    'title', '1 rule violation in this turn')
})

/**
 * `tierOf` is pure, and its sharpest edges (ordering against the dispatch
 * guard, the status field it reads) are invisible through the feed — every
 * row-list caller pre-filters by `rendersInConversation`, so a mis-tiered
 * chrome span never reaches a row to be wrong in. The repo has no unit
 * runner, so import the module straight off the vite dev server Playwright is
 * already pointed at and exercise it directly.
 */
test('tierOf: chrome outranks the folded-name list, and status_code drives promotion', async ({ page }) => {
  await page.goto('/trace/sessions')
  const r = await page.evaluate(async () => {
    const m = await import('/src/components/conversation/dispatch.js')
    const t = (name, extra = {}) => m.tierOf({ name, attributes: {}, ...extra })
    return {
      cwdChanged: t('cwd.changed'),
      sessionTitle: t('session.title'),
      taskReminder: t('harness.task_reminder'),
      thinking: t('assistant.thinking'),
      ruleCheck: t('rule.check'),
      ruleCheckErr: t('rule.check', { status_code: 'ERROR' }),
      ruleCheckViolation: t('rule.check', { attributes: { status: 'violation' } }),
      toolSearchOk: t('tool.ToolSearch'),
      toolSearchErr: t('tool.ToolSearch', { status_code: 'ERROR' }),
      edit: t('tool.Edit'),
      read: t('tool.Read'),
      TIER: { CHROME: m.TIER_CHROME, FULL: m.TIER_FULL, COND: m.TIER_CONDENSED, FOLD: m.TIER_FOLDED },
    }
  })
  // Folded-name spans the card chain cannot render are CHROME, not tier 3.
  expect(r.cwdChanged).toBe(r.TIER.CHROME)
  expect(r.sessionTitle).toBe(r.TIER.CHROME)
  expect(r.taskReminder).toBe(r.TIER.CHROME)
  // Folded names that DO have a branch stay tier 3.
  expect(r.thinking).toBe(r.TIER.FOLD)
  expect(r.ruleCheck).toBe(r.TIER.FOLD)
  // rule.check never promotes, however it failed.
  expect(r.ruleCheckErr).toBe(r.TIER.FOLD)
  expect(r.ruleCheckViolation).toBe(r.TIER.FOLD)
  // Every other folded name promotes on the occurrence that failed, including
  // when the failure lives only in the status column.
  expect(r.toolSearchOk).toBe(r.TIER.FOLD)
  expect(r.toolSearchErr).toBe(r.TIER.COND)
  expect(r.edit).toBe(r.TIER.FULL)
  expect(r.read).toBe(r.TIER.COND)
})

// `keptByFocus` is rung 1 of the Focus ladder — "is this span useful?" — and
// also what a COLLAPSED turn's chips preview. It is not the spine: the spine
// shows one card. What it must get right is which spans are candidates at all.
test('keptByFocus: the agent\'s outcome is not one of its internals', async ({ page }) => {
  await page.goto('/trace/sessions')
  const r = await page.evaluate(async () => {
    const m = await import('/src/components/conversation/dispatch.js')
    const k = (name, attributes = {}, inAgent = false) => {
      const span = { name, attributes }
      return m.keptByFocus({ span, inAgent, tier: m.tierOf(span) })
    }
    return {
      ownEdit: k('tool.Edit'),
      reply: k('assistant_response', { text: 'hi' }),
      ownBash: k('tool.Bash', { command: 'ls', stdout: 'ok' }),
      agentStep: k('tool.Bash', { command: 'ls', stdout: 'ok' }, true),
      agentFailure: k('tool.Bash', { command: 'ls', stderr: 'exit 1' }, true),
      // Filed under the agent by `useSpanTree._pushAgent` so it draws inside
      // that rail — but it is the agent's ANSWER, not one of its steps.
      agentResult: k('workflow.agent_result', { result_preview: 'done' }, true),
      // A violating rule.check stays folded: the violation is not lost, it
      // rides the strip as a ⚠ badge. Keeping it would put a row on the spine
      // that `isKeptRow` still reads as hidden.
      ruleViolation: k('rule.check', { status: 'violation', stderr: '3 errors' }),
      recap: k('harness.recap', { text: 'x' }),
    }
  })
  expect(r.ownEdit).toBe(true)
  expect(r.reply).toBe(true)
  expect(r.ownBash).toBe(false)
  expect(r.agentStep).toBe(false)
  expect(r.agentFailure).toBe(true)
  expect(r.agentResult).toBe(true)
  expect(r.ruleViolation).toBe(false)
  expect(r.recap).toBe(false)
})

// Focus is at most TWO cards per turn: the headline and the turn's newest
// work. The headline is a four-rung ladder — the turn's useful spans, then its
// own non-folded work, then any non-folded row, then anything — and every rung
// takes its LAST member, so "newest" stays true inside "useful". Each case
// below would be answered differently by a bare positional pick.
test('Focus picks the newest USEFUL span, descending the ladder only as needed', async ({ page }) => {
  await page.goto('/trace/sessions')
  const r = await page.evaluate(async () => {
    const s = await import('/src/composables/useDetailSpine.js')
    const S = (name, attributes = {}, id = name) => ({ span_id: id, name, attributes })
    // The whole id list, not just the last one: with two cards in play,
    // asserting a single position cannot tell a missing headline from a
    // missing newest-work card.
    const spine = (rows) => s.buildSpine('latest', 'T', rows, () => false)
      .rows.filter((x) => !x.strip).map((x) => x.span.span_id)
    return {
      // Rung 1: the reply is the outcome even though a recap came after it.
      replyBeatsTrailingRecap: spine([
        { span: S('assistant_response', { text: 'done' }, 'reply') },
        { span: S('harness.recap', {}, 'recap') },
      ]),
      // Rung 1 again: an edit outranks a later shell run for the HEADLINE —
      // and the run is what the second card exists to show.
      outcomeBeatsLaterBash: spine([
        { span: S('tool.Edit', { file_path: '/a.js' }, 'edit') },
        { span: S('tool.Bash', { command: 'ls', stdout: 'ok' }, 'bash') },
      ]),
      // Rung 2: nothing useful, so the turn's own work wins over a recap.
      ownWorkBeatsRecap: spine([
        { span: S('tool.Bash', { command: 'ls', stdout: 'ok' }, 'bash') },
        { span: S('harness.recap', {}, 'recap') },
      ]),
      // Rung 3: the turn's own row beats a positionally later agent step.
      ownWorkBeatsAgentStep: spine([
        { span: S('tool.Bash', { command: 'ls', stdout: 'ok' }, 'agentbash'), inAgent: true },
        { span: S('tool.Read', { file_path: '/a.js' }, 'ownread') },
      ]),
      // The newest work is the agent the turn just launched, and its card is
      // already on the spine — so there is no second card to add, and the
      // parent's EARLIER shell run stays behind the strip. Scanning back past
      // the agent to find it would answer "newest" with an older span.
      newestWorkIsNeverAnOlderSpan: spine([
        { span: S('tool.Bash', { command: 'ls', stdout: 'ok' }, 'ownbash') },
        { span: S('subagent.start', { agent_type: 'Explore' }, 'agent') },
      ]),
      // Rung 4, the floor: something beats nothing.
      allFoldedStillShowsOne: spine([
        { span: S('assistant.thinking', {}, 'think') },
        { span: S('harness.recap', {}, 'recap') },
      ]),
    }
  })
  expect(r.replyBeatsTrailingRecap).toEqual(['reply'])
  expect(r.outcomeBeatsLaterBash).toEqual(['edit', 'bash'])
  expect(r.ownWorkBeatsRecap).toEqual(['bash'])
  expect(r.ownWorkBeatsAgentStep).toEqual(['ownread'])
  expect(r.newestWorkIsNeverAnOlderSpan).toEqual(['agent'])
  expect(r.allFoldedStillShowsOne).toEqual(['recap'])
})

test('keepsOwnCard mirrors BashCard\'s branch, so a bodiless Bash still condenses', async ({ page }) => {
  await page.goto('/trace/sessions')
  const r = await page.evaluate(async () => {
    const m = await import('/src/components/conversation/dispatch.js')
    const k = (name, attributes = {}) => m.keepsOwnCard({ name, attributes })
    return {
      command: k('tool.Bash', { command: 'ls' }),
      stdout: k('tool.Bash', { stdout: 'ok' }),
      stderr: k('tool.Bash', { stderr: 'boom' }),
      interrupted: k('tool.Bash', { interrupted: true }),
      bare: k('tool.Bash', { tool_name: 'Bash' }),
      read: k('tool.Read', { file_path: '/a' }),
      thinking: k('assistant.thinking', { thinking_text: 'x' }),
      nothing: m.keepsOwnCard(null),
    }
  })
  expect(r.command).toBe(true)
  expect(r.stdout).toBe(true)
  expect(r.stderr).toBe(true)
  expect(r.interrupted).toBe(true)
  // The guard has to mirror the card chain, not just the name: a `tool.Bash`
  // carrying none of these falls through to the BODYLESS InlineToolRow, and
  // exempting it there would trade the condensed row's output for nothing.
  expect(r.bare).toBe(false)
  expect(r.read).toBe(false)
  expect(r.thinking).toBe(false)
  expect(r.nothing).toBe(false)
})

test('isFailureShaped: exemptions beat the failure probe', async ({ page }) => {
  await page.goto('/trace/sessions')
  const r = await page.evaluate(async () => {
    const m = await import('/src/components/conversation/dispatch.js')
    const f = (attributes, extra = {}) => m.isFailureShaped({ name: 'x', attributes, ...extra })
    return {
      noTypeErrors: f({ stderr: '\u2713 no type errors' }),
      zeroFailures: f({ stderr: '0 failures' }),
      zeroViolations: f({ stderr: 'zero violations' }),
      exitZero: f({ stderr: 'exit 0' }),
      mixed: f({ stderr: 'no errors, 3 failures' }),
      tenErrors: f({ stderr: '10 errors' }),
      exitThree: f({ stderr: 'exit 3' }),
      cross: f({ stderr: '\u2717 failed' }),
      statusCode: f({}, { status_code: 'ERROR' }),
      denied: f({ denied: true }),
      emptyList: f({ violations: [] }),
      clean: f({ stderr: 'all good' }),
    }
  })
  expect(r.noTypeErrors).toBe(false)
  expect(r.zeroFailures).toBe(false)
  expect(r.zeroViolations).toBe(false)
  expect(r.exitZero).toBe(false)
  expect(r.mixed).toBe(true)
  expect(r.tenErrors).toBe(true)
  expect(r.exitThree).toBe(true)
  expect(r.cross).toBe(true)
  expect(r.statusCode).toBe(true)
  expect(r.denied).toBe(true)
  // An empty list must NOT read as a failure.
  expect(r.emptyList).toBe(false)
  expect(r.clean).toBe(false)
})

// CAI-217. The probe used to read `stdout`, the rendered body, `text`,
// `content` and `result_preview` as well \u2014 so any span whose output DISCUSSED
// a failure was marked as one. Over the live store that is 2,886 non-error
// spans: 1,681 `tool.Bash` that exited 0 and printed a test log, 708
// `assistant_response` where the agent wrote "24 errors (unchanged)", and 477
// `tool.Read` of a file that happens to contain `\u2717`. Each of them was kept
// by `keptByFocus` unconditionally, so a mid-turn grep outranked the turn's
// own answer.
//
// `stderr` and `error` are the only fields that exist BECAUSE something went
// wrong; every other field is content, and content is not status.
test('isFailureShaped reads the span\'s status, not prose about failure', async ({ page }) => {
  await page.goto('/trace/sessions')
  const r = await page.evaluate(async () => {
    const m = await import('/src/components/conversation/dispatch.js')
    const f = (attributes, extra = {}) => m.isFailureShaped({ name: 'x', attributes, ...extra })
    return {
      // A shell run that exited 0 and printed a failing test log did not fail:
      // a non-zero exit becomes its own `tool.failure` span.
      bashLog: f({ tool_name: 'Bash', command: 'pytest -q',
        stdout: '2 failed, 40 passed\nexit 1' }),
      // The agent's own prose about a fix is not a failed span.
      replyProse: f({ response_text: 'Fixed. 24 errors (unchanged \u2014 still the skip-list).' },
        { name: 'assistant_response' }),
      // Reading a file that contains a failure marker is not failing.
      readContent: f({ tool_name: 'Read', file_path: '/repo/log.txt',
        content: '\u2717 3 violations' }, { name: 'tool.Read' }),
      thinkingProse: f({ thinking_text: 'that run had 3 failures, let me retry' },
        { name: 'assistant.thinking' }),
      resultPreview: f({ result_preview: 'reported 5 errors' }),
      // The honest signals still fire.
      stderrShape: f({ stderr: 'exit 127\ncommand not found' }),
      errorAttr: f({ tool_name: 'Bash', error: 'Exit code 1\n(eval):1: == not found' },
        { name: 'tool.failure' }),
      statusCode: f({ stdout: 'all good' }, { status_code: 'ERROR' }),
      rejected: f({ rejected: true }),
    }
  })
  expect(r.bashLog).toBe(false)
  expect(r.replyProse).toBe(false)
  expect(r.readContent).toBe(false)
  expect(r.thinkingProse).toBe(false)
  expect(r.resultPreview).toBe(false)
  expect(r.stderrShape).toBe(true)
  expect(r.errorAttr).toBe(true)
  expect(r.statusCode).toBe(true)
  expect(r.rejected).toBe(true)
})

// CAI-217. Every top `tool.failure` in the live store is the model fumbling
// and immediately correcting \u2014 `File does not exist`, `(eval):1: no such file
// or directory: .venv/bin/python`, `(eval):1: == not found`. The agent retried
// and answered, but the failure was the turn's last KEPT row, so it took the
// card and the answer went behind the strip.
//
// The split is Sentry's handled-vs-unhandled: a caught-and-recovered error is
// a breadcrumb, not the issue title. A failure is the turn's story only when
// the turn ENDED on it.
test('Focus: a failure the turn recovered from does not headline it', async ({ page }) => {
  await page.goto('/trace/sessions')
  const r = await page.evaluate(async () => {
    const s = await import('/src/composables/useDetailSpine.js')
    const S = (name, attributes = {}, id = name, extra = {}) => (
      { span_id: id, name, attributes, ...extra })
    const head = (rows) => s.buildSpine('latest', 'T', rows, () => false)
      .rows.filter((x) => !x.strip).map((x) => x.span.span_id)
    return {
      // The exact shape behind the report: the agent answered, typo'd a
      // command, ran it again, and stopped. The failure is the last KEPT row,
      // so it took the card — but the retry after it says the turn moved on.
      repliedThenFumbledAndRetried: head([
        { span: S('assistant_response', { text: 'done' }, 'reply') },
        { span: S('tool.failure', { tool_name: 'Bash', error: 'Exit code 1\n(eval):1: == not found' }, 'fail') },
        { span: S('tool.Bash', { command: 'test = 1', stdout: 'ok' }, 'retry') },
      ]),
      // Recovery does not need a reply, and it does not need a KEPT row: any
      // real work after the failure is the agent carrying on.
      recoveredIntoARead: head([
        { span: S('tool.Edit', { file_path: '/a.js' }, 'edit') },
        { span: S('tool.failure', { tool_name: 'Read', error: 'File does not exist.' }, 'fail') },
        { span: S('tool.Read', { file_path: '/b.js' }, 'read') },
      ]),
      // Two fumbles in a row, then the agent got on with it.
      twoFailuresThenWork: head([
        { span: S('assistant_response', { text: 'done' }, 'reply') },
        { span: S('tool.failure', { error: 'Exit code 1' }, 'fail1') },
        { span: S('tool.failure', { error: 'Exit code 127' }, 'fail2') },
        { span: S('tool.Bash', { command: 'ls', stdout: 'ok' }, 'retry') },
      ]),
      // A span whose status column says ERROR is a failure too, and recovers
      // the same way.
      statusErrorRecovered: head([
        { span: S('assistant_response', { text: 'done' }, 'reply') },
        { span: S('tool.Grep', { pattern: 'x' }, 'grep', { status_code: 'ERROR' }) },
        { span: S('tool.Bash', { command: 'ls', stdout: 'ok' }, 'retry') },
      ]),
      // The recovery is a SUBAGENT step. `recoveredFailures` counts that as the
      // turn moving on, so the failure is demoted — and the second card must
      // honour the same demotion, or the fumble comes straight back at full
      // prominence as the turn's newest work.
      recoveredIntoAnAgentStep: head([
        { span: S('assistant_response', { text: 'delegating' }, 'reply') },
        { span: S('tool.failure', { error: 'Exit code 1' }, 'fail') },
        { span: S('tool.Read', { file_path: '/b.js' }, 'childread'), inAgent: true },
      ]),
      // Only NOISE follows, so the failure still stands as the turn's outcome.
      failureThenOnlyNoise: head([
        { span: S('tool.Bash', { command: 'ls', stdout: 'ok' }, 'bash') },
        { span: S('tool.failure', { error: 'Exit code 1' }, 'fail') },
        { span: S('assistant.thinking', {}, 'think') },
        { span: S('harness.recap', {}, 'recap') },
      ]),
      // A later failure is not a recovery from an earlier one.
      failureAfterFailure: head([
        { span: S('tool.Bash', { command: 'ls', stdout: 'ok' }, 'bash') },
        { span: S('tool.failure', { error: 'Exit code 1' }, 'fail1') },
        { span: S('tool.failure', { error: 'Exit code 127' }, 'fail2') },
      ]),
    }
  })
  // The recovered failure is gone from the spine in every case; what survives
  // beside the headline is the work the agent recovered INTO, never the fumble.
  expect(r.repliedThenFumbledAndRetried).toEqual(['reply', 'retry'])
  expect(r.recoveredIntoARead).toEqual(['edit', 'read'])
  expect(r.twoFailuresThenWork).toEqual(['reply', 'retry'])
  expect(r.statusErrorRecovered).toEqual(['reply', 'retry'])
  expect(r.recoveredIntoAnAgentStep).toEqual(['reply'])
  expect(r.failureThenOnlyNoise).toEqual(['fail'])
  expect(r.failureAfterFailure).toEqual(['fail2'])
})

// CAI-217, the other half. Demoting a recovered failure must not demote the
// one the reader actually needs: a turn that ended on a failure ended on it,
// and that is the honest card.
test('Focus: a turn that ended on a failure still headlines it', async ({ page }) => {
  await page.goto('/trace/sessions')
  const r = await page.evaluate(async () => {
    const s = await import('/src/composables/useDetailSpine.js')
    const S = (name, attributes = {}, id = name) => ({ span_id: id, name, attributes })
    const head = (rows) => s.buildSpine('latest', 'T', rows, () => false)
      .rows.filter((x) => !x.strip).map((x) => x.span.span_id)
    return {
      editThenFailure: head([
        { span: S('tool.Edit', { file_path: '/a.js' }, 'edit') },
        { span: S('tool.failure', { error: 'Exit code 1' }, 'fail') },
      ]),
      replyThenFailure: head([
        { span: S('assistant_response', { text: 'running it now' }, 'reply') },
        { span: S('tool.failure', { error: 'Exit code 127' }, 'fail') },
      ]),
      // Nothing but failures: the last one is still the turn.
      onlyFailures: head([
        { span: S('tool.failure', { error: 'Exit code 1' }, 'fail1') },
        { span: S('tool.failure', { error: 'Exit code 127' }, 'fail2') },
      ]),
    }
  })
  // The failure is both the headline AND the turn's newest work, so it
  // collapses back to one card and the edit stays behind the strip.
  expect(r.editThenFailure).toEqual(['fail'])
  expect(r.replyThenFailure).toEqual(['fail'])
  expect(r.onlyFailures).toEqual(['fail2'])
})

test('a folded span that failed promotes to condensed via status_code', async ({ page }) => {
  test.setTimeout(60_000)
  const traceId = randomUUID()
  const p8 = traceId.slice(0, 8)
  const prompt = `p-${p8}`
  const at = i => `2026-05-08T15:00:${String(i).padStart(2, '0')}`
  // The failure is recorded ONLY in the status column — no stderr, no marker
  // text — so the text-shape probe cannot see it.
  const spans = [
    { trace_id: traceId, span_id: prompt, parent_id: null, name: 'prompt',
      start_time: at(0), attributes: { text: 'status promotion', is_test: true } },
    { trace_id: traceId, span_id: `f1-${p8}`, parent_id: prompt, name: 'tool.ToolSearch',
      start_time: at(1), status_code: 'ERROR',
      attributes: { query: 'broken lookup', is_test: true } },
    { trace_id: traceId, span_id: `f2-${p8}`, parent_id: prompt, name: 'tool.ToolSearch',
      start_time: at(2), status_code: 'OK',
      attributes: { query: 'fine lookup', is_test: true } },
  ]
  await page.goto('/trace/sessions')
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect((await page.request.post('/api/session-spans', {
    headers: { Authorization: `Bearer ${token}` }, data: spans,
  })).ok()).toBeTruthy()
  await page.goto(`/trace/sessions/${traceId}?view=conversation`)
  await expect(page.locator('.event-spine')).toBeVisible({ timeout: 20_000 })
  await page.getByTestId('focus-toggle').click()

  // The failed one is promoted onto the spine; only the clean one folds.
  await expect(rowFor(page, 2)).toHaveCount(1)
  // Singular, and from an explicit map — never `s.replace(/s$/, '')`.
  await expect(page.getByTestId('fold-strip')).toContainText(/background event(?!s)/)
})

// CAI-217. A recovered failure leaves the spine, so the strip has to say it is
// in there. The mix bar's `failures` segment is a sliver on a 50-row turn and
// the label sits in a truncating list; the mark is the same affordance a
// violating `rule.check` already gets, with the tally in the tooltip rather
// than on screen.
test('FoldStrip marks a set-aside failure', async ({ page }) => {
  test.setTimeout(60_000)
  const traceId = randomUUID()
  const p8 = traceId.slice(0, 8)
  const prompt = `p-${p8}`
  const at = i => `2026-05-08T16:00:${String(i).padStart(2, '0')}`
  const spans = [
    { trace_id: traceId, span_id: prompt, parent_id: null, name: 'prompt',
      start_time: at(0), attributes: { text: 'recovered failure', is_test: true } },
    { trace_id: traceId, span_id: `g1-${p8}`, parent_id: prompt, name: 'assistant_response',
      start_time: at(1), attributes: { text: 'MARKER the answer', is_test: true } },
    { trace_id: traceId, span_id: `g2-${p8}`, parent_id: prompt, name: 'tool.failure',
      start_time: at(2), status_code: 'ERROR',
      attributes: { tool_name: 'Bash', error: 'Exit code 1\n(eval):1: == not found',
        command_preview: 'test == 1', is_test: true } },
    { trace_id: traceId, span_id: `g3-${p8}`, parent_id: prompt, name: 'tool.Bash',
      start_time: at(3), attributes: { tool_name: 'Bash', command: 'test = 1',
        command_preview: 'test = 1', stdout: 'ok', is_test: true } },
  ]
  await page.goto('/trace/sessions')
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect((await page.request.post('/api/session-spans', {
    headers: { Authorization: `Bearer ${token}` }, data: spans,
  })).ok()).toBeTruthy()
  await page.goto(`/trace/sessions/${traceId}?view=conversation`)
  await expect(page.locator('.event-spine')).toBeVisible({ timeout: 20_000 })
  await page.getByTestId('latest-toggle').click()

  // The reply took the card; the failure went behind the strip.
  await expect(page.getByText('MARKER the answer')).toBeVisible()
  const strip = page.getByTestId('fold-strip')
  await expect(strip).toContainText('failures')
  await expect(strip).toContainText('⚠')
  await expect(strip.locator('[title$="in this turn"]')).toHaveAttribute(
    'title', '1 failure in this turn')
})
