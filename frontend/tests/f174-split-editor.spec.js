/**
 * F174 S1 — the feature spec edited as source. The split preview it began
 * with is retired (F176 edits the rendering itself), so what the source
 * renders to is read off the rich face.
 *
 * Stubbed the way `feature-images.spec.js` is: every assertion is about the
 * editor, so the ledger is a fixture rather than seeded data.
 */
import { test, expect } from './auth-fixture.js'

// The rich face is the default editor now; these specs drive the source one.
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('regin_editor_mode', 'source'))
})

const PNG_B64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM' +
  'IQAAAABJRU5ErkJggg=='

function ledger(body) {
  return {
    id: 242, title: 'Split editor', origin: 'native',
    external_ref: null, external_url: null, sync_error: null, body,
    stage: {
      stage: 'building', blurb: 'being built', rounds: 1,
      claimed_at: null, open_defects: 0, escapes: 0,
    },
    synced_at: null, stage_live: true,
    images: [{ id: 1, idx: 1, media_type: 'image/png', byte_size: 68 }],
    cost: { usd: 1, sessions: 1, recalled: 0, shared: 0, gaps: {} },
    work_time: { active_work_ms: 1000, gaps: {} },
    tokens: { input: 1, output: 1 },
    sessions: { rows: [], gaps: { traceless_runs: 0 } },
    defects: { open: 0, total: 0, escapes: 0 },
    coverage: { declared_sessions: 1, all_sessions: 2, declared_usd: 1, all_usd: 2 },
  }
}

async function stub(page, body, onPatch) {
  await page.route(/\/api\/features\/242\/images\/\d+$/, (route) => route.fulfill({
    contentType: 'image/png', body: Buffer.from(PNG_B64, 'base64'),
  }))
  await page.route(/\/api\/features\/242$/, (route) => {
    if (route.request().method() === 'PATCH') {
      const sent = JSON.parse(route.request().postData() || '{}')
      onPatch?.(sent)
      return route.fulfill({ json: ledger(sent.body) })
    }
    return route.fulfill({ json: ledger(body) })
  })
  await page.route(/\/api\/features\/242\/(tasks|defects)/, (route) =>
    route.fulfill({ json: { items: [] } }))
}

const editor = (page) => page.getByTestId('markdown-editor')
const input = (page) => page.getByTestId('feature-spec-input')
const face = (page) => page.getByTestId('feature-spec-rich')
const storedMode = (page) => page.evaluate(() => localStorage.getItem('regin_editor_mode'))

async function openEditor(page) {
  await page.goto('/features/242')
  await page.getByTestId('feature-spec-edit').click()
  await expect(input(page)).toBeVisible()
}

test.describe('wide viewport', () => {
  test.use({ viewport: { width: 1280, height: 900 } })

  test('test_typing_renders_preview', async ({ page }) => {
    await stub(page, '')
    await openEditor(page)

    await input(page).click()
    await input(page).pressSequentially('## 目标')
    await expect(input(page)).toHaveValue('## 目标')

    await page.getByTestId('editor-mode-rich').click()
    await expect(face(page).locator('h2')).toHaveText('目标')
  })

  test('test_mode_is_remembered', async ({ page }) => {
    await stub(page, 'thin prose')
    await openEditor(page)
    await expect(editor(page)).toHaveAttribute('data-mode', 'source')

    await page.getByTestId('editor-mode-rich').click()
    await expect(editor(page)).toHaveAttribute('data-mode', 'rich')
    await expect(input(page)).toHaveCount(0)
    expect(await storedMode(page)).toBe('rich')

    await page.evaluate(() => localStorage.setItem('regin_editor_mode', 'source'))
    await page.reload()
    await page.getByTestId('feature-spec-edit').click()
    await expect(editor(page)).toHaveAttribute('data-mode', 'source')
    await expect(page.getByTestId('editor-mode-source')).toHaveAttribute('aria-pressed', 'true')
    await expect(face(page)).toHaveCount(0)
  })

  test('test_save_is_byte_exact', async ({ page }) => {
    const body = [
      '## Intent  ',
      '',
      'See [the parent](task:42) and *this*  —  verbatim.',
      '',
      '![](image:1)',
      '',
      '| a | bb |',
      '|---|-|',
      '|ccc|   d |',
      '',
      '<!-- kept -->',
      '* star list',
      '+ plus list',
      '',
      '',
    ].join('\n')
    let patched = null
    await stub(page, body, (sent) => { patched = sent })
    await openEditor(page)
    await expect(input(page)).toHaveValue(body)

    await page.getByTestId('feature-spec-save').click()

    await expect.poll(() => patched?.body).toBe(body)
    await expect(page.getByTestId('feature-spec-body').locator('img')).toHaveAttribute('src', /^blob:/)
  })
})
