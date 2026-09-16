/**
 * Presentation mode: one span's complete payload as a document.
 *
 * The invariants worth pinning are the ones the feed cannot show and a
 * screenshot cannot prove: that all three entry routes reach the reader
 * without the detail rail, that ↑/↓ walk the WHOLE trace in order (prompt
 * first, subagent children inline) and clamp at the ends, that the reader
 * swallows the page's own single-key shortcuts while it owns the viewport,
 * and that no excerpt surface leaks raw markdown marks.
 */
import { randomUUID } from 'node:crypto'
import { test, expect } from './auth-fixture.js'
import { contentOverflow } from './helpers/overflow.js'

const LONG_MD = [
  '## Where the limits actually bind',
  '',
  'The `rateLimit` guard sits in the **router**, not in the handler, so every',
  'retry re-enters it. That is why the p95 moved without any handler change.',
  '',
  '- first bullet, long enough to matter',
  '- second bullet',
  '',
  '```js',
  'const x = 1',
  '```',
  '',
  'A closing paragraph that pushes this comfortably past the 320-character',
  'clamp threshold so the feed card has to fold it behind a fade.',
].join('\n')

const ASK_Q = 'Which cache layer should the retry path read?'
const ASK_DENIED_Q = 'Ship the migration tonight?'

async function seed(page) {
  const traceId = randomUUID()
  const p8 = traceId.slice(0, 8)
  const prompt = `p-${p8}`
  const at = i => `2026-05-08T12:00:${String(i).padStart(2, '0')}`
  const spans = [{
    trace_id: traceId, span_id: prompt, parent_id: null, name: 'prompt',
    start_time: at(0), attributes: { text: '## seeded prompt with `marks`', is_test: true },
  }]
  let i = 1
  const add = (name, attributes, extra = {}) => {
    const id = `s${i}-${p8}`
    spans.push({
      trace_id: traceId, span_id: id, parent_id: extra.parent || prompt, name,
      start_time: at(i), duration_ms: extra.duration ?? 100,
      attributes: { ...attributes, is_test: true },
    })
    i++
    return id
  }
  add('assistant_response', { text: LONG_MD }, { duration: 4200 })
  add('tool.Bash', {
    tool_name: 'Bash', command: 'pytest -q', command_preview: 'pytest -q',
    stdout: '$ pytest -q\n✓ 12 passed\n✗ 1 failed in test_router\nsummary line',
  })
  add('tool.Edit', {
    tool_name: 'Edit', file_path: '/repo/router.py', additions: 2, deletions: 1,
    diff: '@@ -1,3 +1,4 @@\n-old = 1\n+new = 1\n+extra = 2\n keep = 3',
  })
  add('rule.check', {
    rule_id: 'r1', file_path: '/repo/router.py', status: 'violation',
    violating_rule_count: 1, applicable_rule_count: 2, total_rules: 9,
    applicable_rules: [
      { id: 'no-bare-except', violated: true, match_count: 2, summary: 'bare except swallows' },
      { id: 'no-print', violated: false, summary: 'print in lib code' },
    ],
  })
  // An answered ask and a denied one. Their payload lives ONLY in
  // `questions`/`answers`, which the attributes rail hides — so a reader with
  // no Q&A block of its own shows this span as "metadata only".
  add('tool.AskUserQuestion', {
    tool_name: 'AskUserQuestion',
    questions: [{
      header: 'Cache layer', question: ASK_Q, multiSelect: false,
      options: [
        { label: 'Redis', description: 'shared, survives a restart', preview: 'CACHE_URL=redis://' },
        { label: 'In-process', description: 'fastest, lost on every deploy' },
      ],
    }, {
      header: 'Rollout', question: 'Which environments first?', multiSelect: true,
      options: [{ label: 'staging' }, { label: 'canary' }, { label: 'prod' }],
    }],
    answers: { [ASK_Q]: 'Redis', 'Which environments first?': ['staging', 'canary'] },
    annotations: { [ASK_Q]: { notes: 'keep the TTL at 60s' } },
  })
  add('tool.AskUserQuestion', {
    tool_name: 'AskUserQuestion', denied: true, deny_kind: 'chat',
    denial_reason: '[Request interrupted] Neither — measure it first.',
    questions: [{
      header: 'Timing', question: ASK_DENIED_Q,
      options: [{ label: 'Ship it' }, { label: 'Hold until Monday' }],
    }],
  })
  const agent = add('subagent.start', { agent_type: 'goal-verifier', description: 'verify' })
  add('tool.Read', { tool_name: 'Read', file_path: '/repo/deep.py' }, { parent: agent })
  // Metadata-only: no prose, no diff, no output, no children.
  add('tool.Glob', { tool_name: 'Glob', pattern: '**/*.py' })
  // A text-less thinking span that PARENTS a tool span — the ingest hangs a
  // turn's tools off its thinking span, so this is the common shape, not an
  // exotic one. Encrypted-thinking attributes: real billed tokens, no text.
  const think = add('assistant.thinking', {
    thinking_blocks: 1, thinking_signature_bytes: 984, output_tokens: 348, model: 'claude-opus-5',
  }, { duration: 6500 })
  spans[spans.length - 1].output_tokens = 348
  add('tool.Grep', { tool_name: 'Grep', pattern: 'needle' }, { parent: think })

  await page.goto('/trace/sessions')
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect((await page.request.post('/api/session-spans', {
    headers: { Authorization: `Bearer ${token}` }, data: spans,
  })).ok()).toBeTruthy()
  await page.goto(`/trace/sessions/${traceId}?view=conversation`)
  await expect(page.locator('.event-spine')).toBeVisible({ timeout: 20_000 })
  return traceId
}

const reader = (page) => page.getByTestId('span-present-reader')
const rowFor = (page, text) => page.locator('.event-spine-row').filter({ hasText: text }).first()

test('three entry routes, none of them the detail rail', async ({ page }) => {
  test.setTimeout(90_000)
  await seed(page)

  // The rail is closed on the Conversation tab by default — nothing here opens it.
  await expect(reader(page)).toHaveCount(0)

  // 1. the ⤢ button on the row
  await rowFor(page, 'Assistant').locator('.present-open').click()
  await expect(reader(page)).toBeVisible()
  await expect(reader(page)).toContainText('Where the limits actually bind')
  await page.keyboard.press('Escape')
  await expect(reader(page)).toHaveCount(0)

  // 2. double-click the row
  await rowFor(page, 'Shell').dblclick()
  await expect(reader(page)).toBeVisible()
  await expect(reader(page)).toContainText('pytest -q')
  await page.keyboard.press('Escape')

  // 3. `P` on the current selection (the dblclick above left Bash selected)
  await page.keyboard.press('p')
  await expect(reader(page)).toBeVisible()
  await expect(reader(page)).toContainText('tool.Bash')
})

test('every span kind renders its own blocks', async ({ page }) => {
  test.setTimeout(90_000)
  await seed(page)

  // The diff card's verb is "Update", not the tool name.
  await rowFor(page, 'router.py').locator('.present-open').click()
  await expect(reader(page).locator('.diff-block')).toBeVisible()
  await expect(reader(page)).toContainText('router.py')
  await page.keyboard.press('Escape')

  await rowFor(page, 'Shell').locator('.present-open').click()
  // Failure beats success on a mixed-signal run: the ✗ line reads as failure.
  await expect(reader(page).locator('.term-fail')).toContainText('1 failed')
  await expect(reader(page).locator('.term-ok')).toContainText('12 passed')
  await page.keyboard.press('Escape')

  await rowFor(page, 'Rule check').locator('.present-open').click()
  await expect(reader(page).locator('.present-rule')).toHaveCount(2)
  await expect(reader(page).locator('.present-rule-mark.is-bad')).toHaveCount(1)
  await page.keyboard.press('Escape')

  // Metadata-only span: dashed empty state, attributes still rendered.
  await rowFor(page, 'Glob').locator('.present-open').click()
  await expect(reader(page).locator('.present-empty')).toBeVisible()
  await expect(reader(page).locator('.present-attrs')).toContainText('pattern')
})

test('an ask renders its Q&A in the reader, not as “metadata only”', async ({ page }) => {
  test.setTimeout(90_000)
  await seed(page)

  await rowFor(page, ASK_Q).locator('.present-open').click()
  const r = reader(page)
  // The defect this pins: the payload is on ASK_HIDDEN_KEYS, so a reader with
  // no Q&A block shows the dashed empty state over a span full of content.
  await expect(r.locator('.present-empty')).toHaveCount(0)
  await expect(r.locator('.present-ask-question')).toHaveCount(2)
  await expect(r).toContainText(ASK_Q)
  // Single-select marks one; multi-select marks every chosen label.
  await expect(r.locator('.present-ask-opt.is-chosen')).toHaveCount(3)
  await expect(r.locator('.present-ask-opt.is-chosen').first()).toContainText('Redis')
  await expect(r).toContainText('shared, survives a restart')
  // Both modes are named on their own chip — a reader who sees only an option
  // list cannot tell a one-pick question from a tick-every-box one.
  await expect(r.locator('.present-ask-mode.is-multi')).toContainText('Multi-select')
  await expect(r.locator('.present-ask-mode').first()).toContainText('Single-select')
  await expect(r.locator('.present-ask-note')).toContainText('keep the TTL at 60s')
  // The rail must not also dump the same payload as raw JSON.
  await expect(r.locator('.present-attrs')).not.toContainText('Redis')
  // A body means the "content only" walk stops here, so the counter is real.
  await expect(r.locator('.present-count')).not.toContainText('off-list')
  await page.keyboard.press('Escape')

  await rowFor(page, ASK_DENIED_Q).locator('.present-open').click()
  await expect(r.locator('.present-ask-deny')).toContainText('chat instead')
  await expect(r.locator('.present-ask-opt.is-chosen')).toHaveCount(0)
  await expect(r.locator('.present-ask-denial')).toContainText('measure it first')
})

test('arrow keys walk the whole trace in order and clamp at both ends', async ({ page }) => {
  test.setTimeout(90_000)
  await seed(page)

  await rowFor(page, 'Assistant').locator('.present-open').click()
  await expect(reader(page)).toBeVisible()
  // Assistant is the second entry (prompt is first).
  await expect(reader(page).locator('.present-count')).toContainText('2 /')
  const total = Number((await reader(page).locator('.present-count').textContent()).split('/')[1].trim())
  // 12 seeded spans, 8 of which carry a body — the walk filter is on by default.
  expect(total).toBe(8)

  await page.keyboard.press('ArrowUp')
  await expect(reader(page).locator('.present-count')).toContainText('1 /')
  // At the start the prev control names the boundary and greys out.
  await expect(reader(page).locator('.present-step').first()).toBeDisabled()
  await expect(reader(page).locator('.present-step').first()).toContainText('start of trace')
  await page.keyboard.press('ArrowUp')
  await expect(reader(page).locator('.present-count')).toContainText('1 /')

  // Walk to the end. The subagent IS in the sequence even though its card is
  // folded in the feed — the reader walks the trace, not the rendered rows.
  for (let n = 0; n < total + 2; n++) await page.keyboard.press('ArrowDown')
  await expect(reader(page).locator('.present-count')).toContainText(`${total} /`)
  await expect(reader(page).locator('.present-step').last()).toBeDisabled()
  await expect(reader(page).locator('.present-step').last()).toContainText('end of trace')
})

test('the reader owns the keyboard and the selection survives closing it', async ({ page }) => {
  test.setTimeout(90_000)
  await seed(page)

  const focusPressed = () => page.getByTestId('focus-toggle').getAttribute('aria-pressed')
  const before = await focusPressed()

  await rowFor(page, 'Shell').locator('.present-open').click()
  await expect(reader(page)).toBeVisible()
  // F, H, T, \, /, G must not reach the page underneath.
  for (const k of ['f', 'h', 't', '\\', '/', 'g']) await page.keyboard.press(k)
  await expect(reader(page)).toBeVisible()
  expect(await focusPressed()).toBe(before)

  await page.keyboard.press('Escape')
  await expect(reader(page)).toHaveCount(0)
  // Selection persists: the feed shows the span that was just read.
  await expect(page.locator('.event-selected')).toHaveCount(1)
})

test('feed clamps long prose and no excerpt shows raw markdown marks', async ({ page }) => {
  test.setTimeout(90_000)
  await seed(page)

  const card = rowFor(page, 'Assistant')
  await expect(card.locator('.prose-clamp')).toBeVisible()
  await expect(card).toContainText('Read full response')
  const h = await card.locator('.prose-clamp').evaluate(el => el.getBoundingClientRect().height)
  expect(h).toBeLessThan(160)

  // The clamp's release valve is the reader, never a second inline expand.
  await card.getByText('Read full response').click()
  await expect(reader(page)).toBeVisible()
  await page.keyboard.press('Escape')

  // The turn header's prompt preview is an excerpt: no `##`, no backticks.
  const preview = await page.locator('.event-spine').locator('xpath=..').innerText()
  expect(preview).not.toContain('## seeded prompt')

  // Terminal transcript: same rule.
  await page.goto(page.url().replace('view=conversation', 'view=terminal'))
  await page.waitForTimeout(600)
  const term = await page.locator('.content-scroll').innerText()
  expect(term).not.toContain('## Where the limits')
})

test('no horizontal overflow with the reader open, and full-bleed on a phone', async ({ page }) => {
  test.setTimeout(90_000)
  await page.setViewportSize({ width: 1280, height: 800 })
  await seed(page)
  // The rail only renders with a span SELECTED, so a bare goto measures two of
  // three columns and proves nothing about the widest layout.
  await rowFor(page, 'router.py').locator('.present-open').click()
  await expect(reader(page)).toBeVisible()
  const wide = await contentOverflow(page)
  expect(wide.offenders, JSON.stringify(wide)).toEqual([])
  expect(wide.scrollWidth).toBeLessThanOrEqual(wide.clientWidth + 1)

  const box = await reader(page).boundingBox()
  expect(box.width).toBeLessThanOrEqual(1040)

  await page.setViewportSize({ width: 700, height: 800 })
  await page.waitForTimeout(300)
  const phone = await reader(page).boundingBox()
  expect(phone.width).toBeGreaterThanOrEqual(699)
  const narrow = await contentOverflow(page)
  expect(narrow.offenders, JSON.stringify(narrow)).toEqual([])
})

test('Enter does not hijack a focused control, and P still opens the reader', async ({ page }) => {
  test.setTimeout(90_000)
  await seed(page)

  // Select one span without opening the reader, then focus a DIFFERENT span's
  // control. Enter is that control's own activation key, so it must do the
  // control's job — open the Assistant span — not silently open the reader on
  // the unrelated span that happens to be selected.
  await rowFor(page, 'Glob').click()
  await expect(page.locator('.event-selected')).toHaveCount(1)
  await rowFor(page, 'Assistant').locator('.present-open').focus()
  await page.keyboard.press('Enter')
  await expect(reader(page)).toBeVisible()
  await expect(reader(page)).toContainText('assistant_response')
  await expect(reader(page)).not.toContainText('tool.Glob')
  await page.keyboard.press('Escape')
  await expect(reader(page)).toHaveCount(0)

  // `P` is unconditional: it still opens on the current selection.
  await page.keyboard.press('p')
  await expect(reader(page)).toBeVisible()
})

test('a span outside the trace walk opens without wedging the reader-open flag', async ({ page }) => {
  test.setTimeout(90_000)
  await seed(page)

  // Drive the composable directly with an id nothing resolves: the guard under
  // test is that this leaves NO reader on screen and, critically, does not
  // leave `readerOpen` true — which used to swallow Escape for the agent scope
  // underneath with nothing on screen to explain why.
  await rowFor(page, 'Shell').locator('.present-open').click()
  await expect(reader(page)).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(reader(page)).toHaveCount(0)

  // Escape still reaches the page beneath: the detail modes toggle from the
  // header. F is Focus (newest span only); C is Condensed.
  const focusToggle = page.getByTestId('latest-toggle')
  const before = await focusToggle.getAttribute('aria-pressed')
  await page.keyboard.press('f')
  await expect(focusToggle).not.toHaveAttribute('aria-pressed', before)
})

test('“content only” restricts the walk without ever blocking a direct open', async ({ page }) => {
  test.setTimeout(90_000)
  await seed(page)

  // 12 seeded spans; tool.Read, tool.Glob, tool.Grep and the text-less
  // assistant.thinking carry no body, so the filter skips exactly those four.
  await rowFor(page, 'Assistant').locator('.present-open').click()
  const chip = reader(page).locator('.present-filter')
  await expect(chip).toHaveAttribute('aria-pressed', 'true')
  await expect(chip).toContainText('content only')
  await expect(chip).toContainText('4 skipped')
  await expect(reader(page).locator('.present-count')).toContainText('/ 8')

  // ↑↓ never lands on a metadata-only span.
  const seen = []
  for (let n = 0; n < 8; n++) {
    seen.push(await reader(page).locator('.present-name').textContent())
    await page.keyboard.press('ArrowDown')
  }
  expect(seen).not.toContain('tool.Glob')
  expect(seen).not.toContain('tool.Read')
  // Regression: a text-less thinking span parents this turn's tool spans, and
  // "parents anything" used to count as a body — so the filter never skipped
  // one of the 21.8k thinking spans in the real store.
  expect(seen).not.toContain('assistant.thinking')

  // A bodyless span opened DIRECTLY still opens — filtering never blocks an
  // explicit request — and the counter says it is off the list.
  await page.keyboard.press('Escape')
  await rowFor(page, 'Glob').locator('.present-open').click()
  await expect(reader(page)).toContainText('tool.Glob')
  await expect(reader(page).locator('.present-count')).toContainText('off-list · 8 readable')
  await expect(reader(page).locator('.present-empty-walk')).toContainText('skips spans like this')
  // ↑↓ resumes inside the filtered walk rather than dead-ending.
  await page.keyboard.press('ArrowUp')
  await expect(reader(page).locator('.present-name')).not.toHaveText('tool.Glob')

  // Turning it off puts every span back in the walk, and the state persists.
  await page.keyboard.press('Escape')
  await rowFor(page, 'Assistant').locator('.present-open').click()
  await chip.click()
  await expect(chip).toHaveAttribute('aria-pressed', 'false')
  await expect(reader(page).locator('.present-count')).toContainText('/ 12')
  await page.reload()
  await expect(page.locator('.event-spine')).toBeVisible({ timeout: 20_000 })
  await rowFor(page, 'Assistant').locator('.present-open').click()
  await expect(reader(page).locator('.present-filter')).toHaveAttribute('aria-pressed', 'false')
})

test('an encrypted-thinking span never claims to be metadata only', async ({ page }) => {
  test.setTimeout(90_000)
  await seed(page)

  // The attributes panel says 1 thinking block, a 984-byte signature and 348
  // billed output tokens. An empty state reading "carries no body — metadata
  // only" beside that panel contradicts it: the reasoning is real, it was just
  // never handed over in plaintext.
  await rowFor(page, 'thought').locator('.present-open').click()
  await expect(reader(page)).toBeVisible()
  await expect(reader(page)).toContainText('assistant.thinking')
  const empty = reader(page).locator('.present-empty')
  await expect(empty).toContainText('Reasoning returned encrypted')
  await expect(empty).not.toContainText('metadata only')
  await expect(reader(page).locator('.present-empty-meta')).toContainText('1 block')
  await expect(reader(page).locator('.present-empty-meta')).toContainText('984 B signature')
  // The attributes that prove the point are still on screen beside it.
  await expect(reader(page).locator('.present-attrs')).toContainText('thinking_signature_bytes')

  // A span that genuinely carries nothing keeps the plain metadata-only state.
  await page.keyboard.press('Escape')
  await rowFor(page, 'Glob').locator('.present-open').click()
  await expect(reader(page).locator('.present-empty')).toContainText('metadata only')
  await expect(reader(page).locator('.present-empty')).not.toContainText('encrypted')
})

test('a subagent’s child rows stay inside the content column', async ({ page }) => {
  test.setTimeout(90_000)
  const traceId = randomUUID()
  const p8 = traceId.slice(0, 8)
  const prompt = `p-${p8}`
  const agent = `sa-${p8}`
  // Real shape: a verifier subagent whose children are long Bash command lines
  // and absolute paths — the titles that ran across the attributes column.
  const LONG = 'grep -rn "content-visibility\\|contentVisibility\\|contain-intrinsic" src/ tests/ 2>/dev/null | head -20; echo "---GREP DONE---"; ls src/components/trace/'
  const spans = [
    { trace_id: traceId, span_id: prompt, parent_id: null, name: 'prompt',
      start_time: '2026-05-08T12:00:00', attributes: { text: 'verify', is_test: true } },
    { trace_id: traceId, span_id: agent, parent_id: prompt, name: 'subagent.start',
      start_time: '2026-05-08T12:00:01', duration_ms: 257_000,
      attributes: { agent_type: 'goal-verifier', agent_id: 'a84cf4046b80d57c7',
                    main_session_impact_tokens: 267, is_test: true } },
  ]
  for (let i = 0; i < 6; i++) {
    spans.push({ trace_id: traceId, span_id: `c${i}-${p8}`, parent_id: agent, name: 'tool.Bash',
      start_time: `2026-05-08T12:00:1${i}`, duration_ms: 40,
      attributes: { tool_name: 'Bash', command: `${LONG} ${i}`, command_preview: `${LONG} ${i}`,
                    stdout: 'ok', is_test: true } })
  }
  await page.goto('/trace/sessions')
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect((await page.request.post('/api/session-spans', {
    headers: { Authorization: `Bearer ${token}` }, data: spans,
  })).ok()).toBeTruthy()
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto(`/trace/sessions/${traceId}?view=conversation`)
  await expect(page.locator('.event-spine')).toBeVisible({ timeout: 20_000 })

  await rowFor(page, 'goal-verifier').locator('.present-open').click()
  await expect(reader(page)).toBeVisible()
  await expect(reader(page).locator('.present-child')).toHaveCount(6)
  // A subagent launch announced itself as OTHER, because the shared category
  // list has no chip for it. The header names the kind it actually is.
  await expect(reader(page).locator('.present-kind')).toHaveText('subagent')

  // Bounding boxes CANNOT see this defect: `flex:1; min-width:0` shrinks the
  // box correctly while the text paints straight over the attributes column,
  // so every rect assertion passes on the broken build. The real invariant is
  // that a title whose content is wider than its box must be CLIPPED.
  const geom = await page.evaluate(() => {
    const col = document.querySelector('.present-col').getBoundingClientRect()
    const rows = [...document.querySelectorAll('.present-child-title')].map(t => {
      const cs = getComputedStyle(t)
      return {
        overflows: t.scrollWidth > t.clientWidth + 1,
        clips: cs.overflowX !== 'visible',
        ellipsis: cs.textOverflow === 'ellipsis',
        boxRight: t.getBoundingClientRect().right,
        // Where the glyphs actually END, which is what overlapped the panel.
        paintedRight: t.getBoundingClientRect().left + t.scrollWidth,
      }
    })
    return { colRight: col.right, rows }
  })
  expect(geom.rows.length).toBe(6)
  for (const r of geom.rows) {
    // These titles are far too long to fit, so each one must overflow…
    expect(r.overflows, JSON.stringify(r)).toBe(true)
    // …and therefore must be clipped and ellipsized rather than painted over
    // whatever sits to its right.
    expect(r.clips, JSON.stringify(r)).toBe(true)
    expect(r.ellipsis, JSON.stringify(r)).toBe(true)
    expect(r.boxRight, JSON.stringify(geom)).toBeLessThanOrEqual(geom.colRight + 1)
  }
  // The full command is still reachable without opening the child.
  await expect(reader(page).locator('.present-child-title').first())
    .toHaveAttribute('title', /GREP DONE/)
})

test('scrolling inside the reader does not drive the feed\'s pagination', async ({ page }) => {
  test.setTimeout(120_000)
  await page.setViewportSize({ width: 1280, height: 800 })
  const traceId = await seed(page)

  await rowFor(page, 'router.py').locator('.present-open').click()
  await expect(reader(page)).toBeVisible()
  await page.waitForTimeout(1200)   // opening legitimately fetches the content

  const calls = []
  page.on('request', (r) => {
    const u = r.url()
    if (u.includes(`/api/sessions/${traceId}/map`)
        || u.includes(`/api/sessions/${traceId}/subtrees`)) calls.push(u)
  })

  const WINDOW_MS = 5000
  const wheelFor = async (deltaY) => {
    const before = calls.length
    const deadline = Date.now() + WINDOW_MS
    while (Date.now() < deadline) {
      await page.mouse.wheel(0, deltaY)
      await page.waitForTimeout(60)
    }
    return calls.length - before
  }

  // Measured against an idle window of the same length, so the live poll —
  // which fires on its own cadence regardless — cancels out, and what is left
  // is whatever the GESTURE caused.
  await page.waitForTimeout(WINDOW_MS)
  const idle = calls.length

  // Read to the bottom of the span and back up. The listeners are on document
  // in the capture phase, so this used to hit the feed's bottom-edge latch and
  // fire a reload — plus the backend transcript rescan behind it — on a
  // gesture that never touched the feed at all.
  const box = await reader(page).boundingBox()
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
  const down = await wheelFor(500)
  const up = await wheelFor(-500)

  expect(down, `down added over idle=${idle}:\n${calls.join('\n')}`)
    .toBeLessThanOrEqual(idle)
  expect(up, `up added over idle=${idle}:\n${calls.join('\n')}`)
    .toBeLessThanOrEqual(idle)
})
