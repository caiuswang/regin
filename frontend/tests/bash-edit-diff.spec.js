/**
 * A shell command that edits files shows what it changed.
 *
 * Claude Code stamps a per-file diff on every file-mutating Bash call;
 * `post_tool_trace._attach_bash_edit_diff` puts it on the span as
 * `bash_edits`. The sharpest form of the defect this covers is a silent
 * `sed -i`: no stdout, no stderr, and a command short enough to stay under the
 * preview cap, so the card chain's attribute guard failed and the call
 * degraded to a bare inline row with no body at all.
 */
import { randomUUID } from 'node:crypto'
import { test, expect } from './auth-fixture.js'

const HUNK = '@@ -10,3 +10,5 @@\n ctx\n+one\n+two\n+three\n-gone'

async function seed(page, bashAttrs) {
  const traceId = randomUUID()
  const p8 = traceId.slice(0, 8)
  const prompt = `p-${p8}`
  const at = i => `2026-05-09T09:00:${String(i).padStart(2, '0')}`
  const spans = [
    { trace_id: traceId, span_id: prompt, parent_id: null, name: 'prompt',
      start_time: at(0), attributes: { text: 'bash edit diffs', is_test: true } },
    // A silent `sed -i`: command_preview only, exactly as the hook stores it.
    { trace_id: traceId, span_id: `b1-${p8}`, parent_id: prompt, name: 'tool.Bash',
      start_time: at(1), duration_ms: 90,
      attributes: { tool_name: 'Bash', command_preview: "sed -i s/gone/one/ a.py",
        ...bashAttrs, is_test: true } },
    // A Bash call that changed nothing, as the control.
    { trace_id: traceId, span_id: `b2-${p8}`, parent_id: prompt, name: 'tool.Bash',
      start_time: at(2), duration_ms: 40,
      attributes: { tool_name: 'Bash', command_preview: 'ls -la',
        stdout: 'a.py', is_test: true } },
  ]
  await page.goto('/trace/sessions')
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect((await page.request.post('/api/session-spans', {
    headers: { Authorization: `Bearer ${token}` }, data: spans,
  })).ok()).toBeTruthy()
  await page.goto(`/trace/sessions/${traceId}?view=conversation`)
  await expect(page.locator('.event-spine')).toBeVisible({ timeout: 20_000 })
  return traceId
}

const editRow = page => page.locator('.event-spine-row').filter({ hasText: 'sed -i' })

test('a silent sed edit renders as a card with its diff', async ({ page }) => {
  test.setTimeout(60_000)
  await seed(page, {
    bash_edits: [{ file_path: '/repo/a.py', edit_op: 'update', diff: HUNK,
      added_lines: 3, removed_lines: 1 }],
    bash_edit_files: ['/repo/a.py'],
  })

  const row = editRow(page)
  // R8/R9: a card with a disclosure, not the bodyless inline row.
  await expect(row).toContainText('Shell')
  await expect(row).toContainText('▸')
  // R2: the collapsed row carries the count and the line totals.
  await expect(row.locator('[data-bash-edit-count]')).toHaveText('1 file')
  await expect(row).toContainText('+3')
  await expect(row).toContainText('−1')

  await row.getByText('sed -i').click()
  await expect(row).toContainText('Update')
  await expect(row).toContainText('/repo/a.py')
  // The hunk body is styled as additions and deletions, not printed as text.
  await expect(row.locator('.diff-block span[class*="bg-emerald-500"]').first())
    .toContainText('one')
  await expect(row.locator('.diff-block span[class*="bg-red-500"]').first())
    .toContainText('gone')
})

test('more changed files than diffs lists the rest by name', async ({ page }) => {
  test.setTimeout(60_000)
  const paths = i => `/repo/f${i}.py`
  await seed(page, {
    bash_edits: [0, 1, 2, 3, 4].map(i => ({
      file_path: paths(i), edit_op: 'update', diff: HUNK,
      added_lines: 3, removed_lines: 1,
    })),
    bash_edit_more: 4,
    bash_edit_files: [0, 1, 2, 3, 4, 5, 6, 7, 8].map(paths),
  })

  const row = editRow(page)
  await expect(row.locator('[data-bash-edit-count]')).toHaveText('9 files')
  await row.getByText('sed -i').click()
  await expect(row.locator('[data-bash-edit]')).toHaveCount(5)
  await expect(row.locator('[data-bash-edit-path]')).toHaveCount(4)
  await expect(row.locator('[data-bash-edit-path]').first()).toHaveText('/repo/f5.py')
})

test('an undiffable command still names its files', async ({ page }) => {
  test.setTimeout(60_000)
  await seed(page, {
    bash_edit_unavailable: true,
    bash_edit_files: ['/repo/db.sqlite', '/repo/img.png', '/repo/blob.bin'],
  })

  const row = editRow(page)
  await expect(row.locator('[data-bash-edit-count]')).toHaveText('3 files')
  await expect(row).not.toContainText('+3')
  await row.getByText('sed -i').click()
  await expect(row).toContainText('diff unavailable')
  await expect(row.locator('[data-bash-edit-path]')).toHaveCount(3)
  await expect(row.locator('[data-bash-edit]')).toHaveCount(0)
})

test('a file-editing shell call survives Focus at full tier', async ({ page }) => {
  test.setTimeout(60_000)
  await seed(page, {
    bash_edits: [{ file_path: '/repo/a.py', edit_op: 'update', diff: HUNK,
      added_lines: 3, removed_lines: 1 }],
    bash_edit_files: ['/repo/a.py'],
  })

  await page.getByTestId('focus-toggle').click()
  // R10: the editing call is spine; the plain `ls` condenses as before.
  await expect(editRow(page)).toHaveAttribute('data-tier', '1')
  await expect(page.locator('.event-spine-row').filter({ hasText: 'ls -la' }))
    .toHaveAttribute('data-tier', '2')
})

/**
 * Defect #151. Two escapes from the first round: the presentation reader had
 * no block for `bash_edits`, so it dumped the whole array — diffs included, as
 * `\n`-escaped JSON — into the attributes table; and a long stdout pushed the
 * file changes below the fold of the card that exists to show them.
 */
const OUT = Array.from({ length: 40 }, (_, i) => `stdout line ${i}`).join('\n')
const EDIT_ATTRS = {
  stdout: OUT,
  bash_edits: [
    { file_path: '/repo/a.py', edit_op: 'update', diff: HUNK, added_lines: 3, removed_lines: 1 },
    { file_path: '/repo/b.js', edit_op: 'create', diff: HUNK, added_lines: 3, removed_lines: 0 },
  ],
  bash_edit_files: ['/repo/a.py', '/repo/b.js'],
}

test('the reader renders bash edits as diffs, not as a JSON attribute', async ({ page }) => {
  test.setTimeout(60_000)
  await seed(page, EDIT_ATTRS)

  await editRow(page).locator('.present-open').click()
  const reader = page.getByTestId('span-present-reader')
  await expect(reader).toBeVisible()

  const blocks = reader.locator('[data-present-bash-edit]')
  await expect(blocks).toHaveCount(2)
  await expect(blocks.first()).toContainText('/repo/a.py')
  await expect(blocks.first()).toContainText('Update')
  await expect(blocks.first()).toContainText('+3')
  await expect(blocks.first()).toContainText('−1')
  await expect(blocks.nth(1)).toContainText('Create')
  // The hunk is a painted diff here too, not text.
  await expect(blocks.first().locator('.diff-block span[class*="bg-emerald-500"]').first())
    .toContainText('one')

  // …and the raw array is gone from the attributes table.
  await expect(reader).not.toContainText('bash_edits')
  await expect(reader).not.toContainText('bash_edit_files')
  await expect(reader).not.toContainText('\\n')
})

test('the reader names the files of an undiffable command', async ({ page }) => {
  test.setTimeout(60_000)
  await seed(page, { bash_edit_unavailable: true, bash_edit_files: ['/repo/db.sqlite', '/repo/img.png'] })

  await editRow(page).locator('.present-open').click()
  const reader = page.getByTestId('span-present-reader')
  await expect(reader).toBeVisible()
  await expect(reader).toContainText('diff unavailable')
  await expect(reader.locator('[data-present-bash-path]')).toHaveCount(2)
  await expect(reader.locator('[data-present-bash-edit]')).toHaveCount(0)
})

test('a file-editing shell call folds its output and leads with the diff', async ({ page }) => {
  test.setTimeout(60_000)
  await seed(page, EDIT_ATTRS)

  const row = editRow(page)
  await row.getByText('sed -i').click()
  // The diff is what the card opens on; the result is one click away.
  await expect(row.locator('[data-bash-edit]')).toHaveCount(2)
  await expect(row.locator('[data-bash-output]')).toHaveCount(0)
  const toggle = row.locator('[data-bash-output-toggle]')
  await expect(toggle).toContainText('stdout')
  await toggle.click()
  await expect(row.locator('[data-bash-output]')).toContainText('stdout line 39')
})

test('a shell call that edited nothing still opens on its output', async ({ page }) => {
  test.setTimeout(60_000)
  await seed(page, { stdout: OUT })

  const row = editRow(page)
  await row.getByText('sed -i').click()
  await expect(row.locator('[data-bash-output]')).toContainText('stdout line 39')
})
