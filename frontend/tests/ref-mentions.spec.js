/**
 * Feature #131 S2 — the `@` composer: typing `@` in a body editor searches
 * regin and writes `[label](kind:key)`.
 *
 * Unstubbed: each test seeds its own project, features, task and defect
 * through the ORM and searches the real `/api/refs`. The scratch DB is shared
 * by every spec running in parallel, so nothing here assumes an id or an empty
 * table: rows are found by the ids the seed returned, and the selected
 * project is what puts this test's rows first.
 */
import { execFileSync } from 'node:child_process'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, test } from './auth-fixture.js'
import { SCRATCH_DIR } from '../e2e-env.js'
import { contentOverflow } from './helpers/overflow.js'
import { rowAction } from './helpers/rowAction.js'

// The rich face is the default editor now; these specs drive the source one.
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('regin_editor_mode', 'source'))
})

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..')
const PY = resolve(ROOT, '.venv/bin/python')

function mark() {
  const info = test.info()
  return `rm${info.workerIndex}x${info.retry}x${Date.now().toString(36)}`
}

// Straight through the ORM, never `POST /api/repos`: registering a repo there
// reconciles the table against settings.repo_paths and deletes every row it
// does not list — the fixture checkout and every other test's project too.
// D hangs off B, so B is the most active feature of P whichever second the
// rows land in.
function seed(tag, { repoPath = join(SCRATCH_DIR, 'refs-repos', tag) } = {}) {
  const script = `
import json, sys
sys.path.insert(0, ".")
from lib.orm import SessionLocal
from lib.orm.models.sync import Branch, Repo
from lib.orm.models.tasks import AgentTask
from lib.features import store as features
from lib.defects import store as defects
tag, path = sys.argv[1], sys.argv[2]
with SessionLocal() as s:
    repo = Repo(name=f"refs-{tag}", path=path, is_active=1, default_branch="master",
                description="ref-mentions e2e project")
    s.add(repo); s.commit(); s.refresh(repo)
    s.add(Branch(repo_id=repo.id, name="master", is_tracked=1)); s.commit()
    project, name = repo.id, repo.name
a = features.create("Alpha", project_id=project)
b = features.create("Beta", project_id=project)
with SessionLocal() as s:
    g = AgentTask(title="Gamma", status="idea", source="test", project_id=project,
                  tags_json=json.dumps([tag]))
    s.add(g); s.commit(); s.refresh(g)
    g_id = g.id
d = defects.record("Delta broke", feature_id=b.id)
print(json.dumps({"project": project, "name": name, "A": a.id, "B": b.id, "G": g_id, "D": d.id}))
`
  const out = execFileSync(PY, ['-c', script, tag, repoPath], { cwd: ROOT, encoding: 'utf8' })
  return JSON.parse(out.trim().split('\n').pop())
}

const menu = (page) => page.getByTestId('ref-menu')
const group = (page, key) => menu(page).getByTestId(`ref-menu-group-${key}`)
const row = (page, ref) => menu(page).locator(`[data-testid="ref-menu-row"][data-ref="${ref}"]`)

// The rows on screen answer exactly `query`, not an older one still drawn.
async function settled(page, query) {
  await expect(menu(page)).toHaveAttribute('data-query', query)
  await expect(menu(page)).not.toHaveClass(/is-stale/)
}

async function openDeclare(page, s) {
  await page.goto(`/features?project=${s.project}`)
  await page.getByTestId('declare-feature').click()
  // Typed before the select settles, `@` would search every project.
  await expect(page.getByTestId('declare-project')).toContainText(s.name)
  return page.getByTestId('declare-body')
}

const alphaRef = (s) => `[F${s.A} Alpha](feature:${s.A})`

test('test_at_sign_lists_recent_work_and_six_kinds', async ({ page }) => {
  const s = seed(mark())
  const spec = await openDeclare(page, s)
  await spec.pressSequentially('@')
  await settled(page, '')

  const recent = group(page, 'recent').getByTestId('ref-menu-row')
  await expect(recent).toHaveCount(3)
  expect(await recent.evaluateAll((els) => els.map((el) => el.dataset.ref)))
    .toEqual([`feature:${s.B}`, `feature:${s.A}`, `task:${s.G}`])
  await expect(group(page, 'kinds').getByTestId('ref-menu-row'))
    .toHaveText([/Feature/, /Task/, /Defect/, /Session/, /File/, /Memory/])
  const order = await menu(page).locator('[data-testid^="ref-menu-group-"]')
    .evaluateAll((els) => els.map((el) => el.dataset.testid))
  expect(order).toEqual(['ref-menu-group-recent', 'ref-menu-group-kinds'])
  await expect(spec).toHaveAttribute('aria-expanded', 'true')
})

test('test_typing_searches_every_kind', async ({ page }) => {
  const s = seed(mark())
  const spec = await openDeclare(page, s)
  const answered = page.waitForResponse((res) => {
    const url = new URL(res.url())
    return url.pathname === '/api/refs' && url.searchParams.get('q') === 'alp'
  })
  await spec.pressSequentially('@alp')
  const res = await answered
  await settled(page, 'alp')

  // No kind narrows the search, and every group the answer holds is drawn.
  expect(new URL(res.url()).searchParams.has('kind')).toBe(false)
  const kinds = (await res.json()).groups.map((g) => g.kind)
  expect(await menu(page).locator('[data-testid^="ref-menu-group-"]')
    .evaluateAll((els) => els.map((el) => el.dataset.testid.replace('ref-menu-group-', ''))))
    .toEqual(kinds)

  const alpha = group(page, 'feature').locator(`[data-ref="feature:${s.A}"]`)
  await expect(alpha).toContainText(`F${s.A}`)
  await expect(alpha).toContainText('Alpha')
  await expect(menu(page).getByTestId('ref-menu-row').filter({ hasText: 'Beta' })).toHaveCount(0)
})

test('test_enter_inserts_a_markdown_reference', async ({ page }) => {
  const s = seed(mark())
  const spec = await openDeclare(page, s)
  await spec.pressSequentially('builds on @alp')
  await settled(page, 'alp')
  await expect(row(page, `feature:${s.A}`)).toHaveAttribute('aria-selected', 'true')

  await spec.press('Enter')

  const expected = `builds on ${alphaRef(s)} `
  await expect(spec).toHaveValue(expected)
  expect(await spec.evaluate((el) => [el.selectionStart, el.selectionEnd]))
    .toEqual([expected.length, expected.length])
  await expect(menu(page)).toHaveCount(0)
})

test('test_kind_row_drills_into_one_kind', async ({ page }) => {
  const s = seed(mark())
  const spec = await openDeclare(page, s)
  await spec.pressSequentially('@')
  await settled(page, '')

  const taskKind = group(page, 'kinds').locator('[data-kind="task"]')
  for (let i = 0; i < 12; i += 1) {
    if (await taskKind.getAttribute('aria-selected') === 'true') break
    await spec.press('ArrowDown')
  }
  await expect(taskKind).toHaveAttribute('aria-selected', 'true')
  await spec.press('Enter')

  await expect(spec).toHaveValue('@task:')
  await settled(page, 'task:')
  await expect(menu(page).locator('[data-testid^="ref-menu-group-"]')).toHaveCount(1)
  await expect(group(page, 'task')).toBeVisible()
  const kinds = await menu(page).getByTestId('ref-menu-row')
    .evaluateAll((els) => [...new Set(els.map((el) => el.dataset.kind))])
  expect(kinds).toEqual(['task'])
})

test('test_id_shortcut_finds_the_exact_item', async ({ page }) => {
  const s = seed(mark())
  const spec = await openDeclare(page, s)
  await spec.pressSequentially(`@T${s.G}`)
  await settled(page, `T${s.G}`)

  await expect(menu(page).locator('[data-testid^="ref-menu-group-"]')).toHaveCount(1)
  const first = group(page, 'task').getByTestId('ref-menu-row').first()
  await expect(first).toHaveAttribute('data-ref', `task:${s.G}`)
  await expect(first).toContainText(`T${s.G}`)
  await expect(first).toContainText('Gamma')
})

test('test_escape_closes_the_menu_not_the_dialog', async ({ page }) => {
  const s = seed(mark())
  const spec = await openDeclare(page, s)
  await spec.pressSequentially('@alp')
  await expect(menu(page)).toBeVisible()

  await spec.press('Escape')

  await expect(menu(page)).toHaveCount(0)
  await expect(page.getByRole('dialog')).toBeVisible()
  await expect(spec).toHaveValue('@alp')
  await expect(spec).toHaveAttribute('aria-expanded', 'false')
})

test('test_clicking_a_row_inside_a_dialog_inserts_and_keeps_the_dialog', async ({ page }) => {
  const s = seed(mark())
  const spec = await openDeclare(page, s)
  await spec.pressSequentially('@alp')
  await settled(page, 'alp')

  await row(page, `feature:${s.A}`).click()

  await expect(spec).toHaveValue(`${alphaRef(s)} `)
  await expect(page.getByRole('dialog')).toBeVisible()
  await expect(spec).toBeFocused()
  await expect(menu(page)).toHaveCount(0)
})

test('test_enter_before_results_land_accepts_the_fresh_row', async ({ page }) => {
  const s = seed(mark())
  await page.route(/\/api\/refs\?/, async (route) => {
    await new Promise((done) => setTimeout(done, 600))
    await route.continue()
  })
  const spec = await openDeclare(page, s)

  await spec.pressSequentially('@alp')
  await spec.press('Enter')

  await expect(spec).toHaveValue(`${alphaRef(s)} `)
  await expect(menu(page)).toHaveCount(0)
})

// An answer to a query the user has typed past is dropped by sequence, so it
// can neither be drawn as current nor disarm the Enter waiting on the newer
// one. Held until after Enter and then answered at once, it lands inside the
// newer query's debounce, the window in which it used to be taken.
test('test_an_older_answer_landing_after_enter_keeps_it_armed', async ({ page }) => {
  const s = seed(mark())
  let release
  const held = new Promise((done) => { release = done })
  await page.route(/\/api\/refs\?/, async (route) => {
    if (new URL(route.request().url()).searchParams.get('q') !== 'al') return route.continue()
    await held
    return route.fulfill({ json: { query: 'al', kind: null, groups: [] } })
  })
  const spec = await openDeclare(page, s)
  const older = page.waitForRequest((req) => {
    const url = new URL(req.url())
    return url.pathname === '/api/refs' && url.searchParams.get('q') === 'al'
  })
  await spec.pressSequentially('@al')
  await older

  await spec.pressSequentially('p')
  await spec.press('Enter')
  release()

  await expect(spec).toHaveValue(`${alphaRef(s)} `)
  await expect(menu(page)).toHaveCount(0)
})

test('test_trigger_after_cjk_but_not_in_an_email_or_code', async ({ page }) => {
  const s = seed(mark())
  const spec = await openDeclare(page, s)

  await spec.pressSequentially('基于@alp')
  await expect(menu(page)).toBeVisible()
  await expect(menu(page)).toHaveAttribute('data-query', 'alp')
  await spec.press('Escape')

  await spec.fill('')
  await spec.pressSequentially('mail me@h')
  await expect(menu(page)).toHaveCount(0)
  await spec.pressSequentially('ost.com')
  await expect(menu(page)).toHaveCount(0)
  await expect(spec).toHaveValue('mail me@host.com')

  await spec.fill('')
  await spec.pressSequentially('```')
  await spec.press('Enter')
  await spec.pressSequentially('@app.route')
  await expect(menu(page)).toHaveCount(0)
  await expect(spec).toHaveValue('```\n@app.route')
  await expect(spec).toHaveAttribute('aria-expanded', 'false')
})

test('test_ime_composition_never_accepts_a_row', async ({ page }) => {
  const s = seed(mark())
  const spec = await openDeclare(page, s)
  await spec.pressSequentially('@')
  // A fresh, highlighted row: an Enter that reached the menu would take it.
  await settled(page, '')
  const first = group(page, 'recent').getByTestId('ref-menu-row').first()
  await expect(first).toHaveAttribute('aria-selected', 'true')

  // Mid-composition: the pinyin on the way to 新建 is never read as a query,
  // and neither the arrow nor the committing Enter reaches the menu.
  await spec.evaluate((el) => {
    el.dispatchEvent(new CompositionEvent('compositionstart', { bubbles: true, data: '' }))
    for (const partial of ['x', 'xin', '新建']) {
      el.value = `@${partial}`
      el.dispatchEvent(new InputEvent('input', {
        bubbles: true, isComposing: true, data: partial, inputType: 'insertCompositionText',
      }))
    }
    for (const key of ['ArrowDown', 'Enter']) {
      el.dispatchEvent(new KeyboardEvent('keydown', {
        key, bubbles: true, cancelable: true, isComposing: true,
      }))
    }
  })
  await expect(menu(page)).toHaveAttribute('data-query', '')
  await expect(first).toHaveAttribute('aria-selected', 'true')
  await expect(spec).toHaveValue('@新建')

  const searched = page.waitForRequest((req) => {
    const url = new URL(req.url())
    return url.pathname === '/api/refs' && url.searchParams.get('q') === '新建'
  })
  await spec.evaluate((el) => {
    el.dispatchEvent(new CompositionEvent('compositionend', { bubbles: true, data: '新建' }))
  })
  await searched

  await settled(page, '新建')
  await expect(spec).toHaveValue('@新建')
})

test('test_undo_restores_the_typed_query', async ({ page }) => {
  const s = seed(mark())
  const spec = await openDeclare(page, s)
  await spec.pressSequentially('@alp')
  await settled(page, 'alp')
  await spec.press('Enter')
  await expect(spec).toHaveValue(`${alphaRef(s)} `)

  await spec.press('ControlOrMeta+z')

  await expect(spec).toHaveValue('@alp')
})

test('test_backspace_removes_a_whole_reference', async ({ page }) => {
  const s = seed(mark())
  const spec = await openDeclare(page, s)
  const typed = `see ${alphaRef(s)}`
  await spec.fill(typed)
  await spec.press('End')

  await spec.press('Backspace')
  await expect(spec).toHaveValue('see ')

  // One keystroke took it, so one undo brings it back whole.
  await spec.press('ControlOrMeta+z')
  await expect(spec).toHaveValue(typed)

  // Inside a reference Backspace is an ordinary one-character delete.
  await spec.evaluate((el) => el.setSelectionRange(el.value.length - 1, el.value.length - 1))
  await spec.press('Backspace')
  await expect(spec).toHaveValue(`see [F${s.A} Alpha](feature:${String(s.A).slice(0, -1)})`)
})

test('test_pasting_a_regin_url_inserts_a_reference', async ({ page }) => {
  const s = seed(mark())
  const spec = await openDeclare(page, s)
  await spec.focus()
  const url = `${new URL(page.url()).origin}/tasks/${s.G}`

  await spec.evaluate((el, text) => {
    const data = new DataTransfer()
    data.setData('text/plain', text)
    el.dispatchEvent(new ClipboardEvent('paste', { clipboardData: data, bubbles: true, cancelable: true }))
  }, url)

  await expect(spec).toHaveValue(`[T${s.G} Gamma](task:${s.G}) `)
})

test('test_file_search_is_scoped_to_the_project_repo', async ({ page }) => {
  const s = seed(mark(), { repoPath: ROOT })
  const spec = await openDeclare(page, s)

  await spec.pressSequentially('@file:usefilemen')
  await settled(page, 'file:usefilemen')
  await expect(row(page, 'file:frontend/src/composables/useFileMentions.js')).toBeVisible()

  await spec.fill('')
  await spec.pressSequentially('@file:../')
  await settled(page, 'file:../')
  await expect(menu(page).locator('[data-row-type="item"][data-kind="file"]')).toHaveCount(0)
  await expect(menu(page).getByTestId('ref-menu-note')).toHaveText('No matches for "file:../"')
})

// Each site is opened the way an operator reaches it; ` @` rather than `@`
// so a field that already holds text still has a boundary before the `@`.
// The search the `@` sends has to carry the project that site is scoped to.
async function offersReferences(page, field, project) {
  await expect(field).toBeVisible()
  await field.click()
  await field.press('End')
  const searched = page.waitForRequest((req) => new URL(req.url()).pathname === '/api/refs')
  await field.pressSequentially(' @')
  await expect(menu(page)).toBeVisible()
  await expect(field).toHaveAttribute('aria-expanded', 'true')
  expect(new URL((await searched).url()).searchParams.get('project_id')).toBe(String(project))
  await field.press('Escape')
  await expect(menu(page)).toHaveCount(0)
}

test('test_every_body_editor_offers_references', async ({ page }) => {
  const s = seed(mark())

  // 1. New feature → Spec
  await offersReferences(page, await openDeclare(page, s), s.project)

  // 2–3. Ledger → spec editor, then a step's instruction
  await page.goto(`/features/${s.A}`)
  await page.getByTestId('feature-spec-edit').click()
  await offersReferences(page, page.getByTestId('feature-spec-input'), s.project)
  await page.getByTestId('feature-verb-spec').click()
  await offersReferences(page, page.getByTestId('feature-step-body'), s.project)

  // 4. Task composer → body, searching the project picked in the composer
  await page.goto(`/tasks?project=${s.project}`)
  await page.getByTestId('task-new').click()
  const composerProject = page.getByTestId('task-composer-project')
  await composerProject.click()
  await page.getByRole('option', { name: s.name, exact: true }).click()
  await expect(composerProject).toContainText(s.name)
  await offersReferences(page, page.locator('#task-body'), s.project)
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).toHaveCount(0)

  // 5. Quick capture → body
  await page.getByTestId('quick-capture-fab').click()
  await offersReferences(page, page.locator('#quick-capture-body'), s.project)
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).toHaveCount(0)

  // 6–7. Task detail → body editor, then the edit sheet
  await page.goto(`/tasks/${s.G}`)
  await page.getByTestId('task-body-edit').click()
  await offersReferences(page, page.getByTestId('task-body-area'), s.project)
  await page.getByTestId('task-body-cancel').click()
  await page.getByRole('button', { name: 'Discard changes?' }).click()
  await rowAction(page.getByTestId('task-detail-actions'), 'edit')
  await offersReferences(page, page.locator('#task-edit-body'), s.project)
})

test('test_menu_fits_a_phone_viewport', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const s = seed(mark())
  await page.goto(`/tasks/${s.G}`)
  await page.getByTestId('task-body-edit').click()
  const field = page.getByTestId('task-body-area')
  await field.click()
  await field.pressSequentially('@')
  await settled(page, '')

  const box = await menu(page).boundingBox()
  expect(box.x).toBeGreaterThanOrEqual(0)
  expect(box.x + box.width).toBeLessThanOrEqual(390)
  expect(box.y).toBeGreaterThanOrEqual(0)
  expect(box.y + box.height).toBeLessThanOrEqual(844)
  // Below 640px the menu spans the textarea it belongs to.
  const area = await field.boundingBox()
  expect(Math.abs(box.x - area.x)).toBeLessThanOrEqual(1)
  expect(Math.abs(box.width - area.width)).toBeLessThanOrEqual(1)

  const pane = await contentOverflow(page)
  expect(pane.offenders).toEqual([])
  expect(pane.scrollWidth).toBeLessThanOrEqual(pane.clientWidth + 1)
  const page_ = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth,
  }))
  expect(page_.scroll).toBeLessThanOrEqual(page_.client + 1)
})

// The Recent screen is never empty, so an Enter armed on it would write
// whichever reference landed on top — one nobody saw. Unanswered, Enter is
// the textarea's; answered, it takes the highlighted row as before.
test('test_enter_on_the_empty_query_before_results_land_inserts_nothing', async ({ page }) => {
  const s = seed(mark())
  let release
  const held = new Promise((done) => { release = done })
  await page.route(/\/api\/refs\?/, async (route) => {
    await held
    await route.continue()
  })
  const spec = await openDeclare(page, s)
  const asked = page.waitForRequest((req) => new URL(req.url()).pathname === '/api/refs')
  await spec.pressSequentially('@')
  await asked

  await spec.press('Enter')
  await expect(spec).toHaveValue('@\n')
  await expect(menu(page)).toHaveCount(0)
  const landed = page.waitForResponse((res) => new URL(res.url()).pathname === '/api/refs')
  release()
  await landed
  await expect(spec).toHaveValue('@\n')

  await page.unroute(/\/api\/refs\?/)
  await spec.pressSequentially('@')
  await settled(page, '')
  await spec.press('Enter')
  await expect(spec).toHaveValue(`@\n[F${s.B} Beta](feature:${s.B}) `)
})

test('test_references_are_tinted_where_they_sit', async ({ page }) => {
  const s = seed(mark())
  const spec = await openDeclare(page, s)
  const ref = alphaRef(s)
  const lines = Array.from({ length: 14 }, (_, i) => `line ${i} ${'words '.repeat(i % 3 ? 4 : 30)}`)
  await spec.fill(`${lines.join('\n')}\nsee ${ref} here\n\n\`${ref}\``)

  const hl = page.getByTestId('ref-highlight')
  await expect(hl).toBeVisible()
  // The reference outside code is tinted; the one inside code is not.
  await expect(hl.locator('.ref-hl-ref')).toHaveText([ref])

  // Same box, same metrics, same text: it wraps where the field wraps.
  const geometry = () => spec.evaluate((field) => {
    const mirror = field.parentElement.querySelector('[data-testid="ref-highlight"]')
    const a = field.getBoundingClientRect()
    const b = mirror.getBoundingClientRect()
    const fs = getComputedStyle(field)
    const ms = getComputedStyle(mirror)
    const metrics = ['fontFamily', 'fontSize', 'lineHeight', 'letterSpacing', 'paddingLeft',
      'paddingRight', 'paddingTop', 'overflowWrap', 'whiteSpace'].filter((p) => fs[p] !== ms[p])
    return {
      box: [b.left - a.left - field.clientLeft, b.top - a.top - field.clientTop,
        b.width - field.clientWidth, b.height - field.clientHeight].map(Math.round),
      scrollHeight: mirror.scrollHeight - field.scrollHeight,
      scrollTop: mirror.scrollTop - field.scrollTop,
      metrics,
      inert: ms.pointerEvents,
      rootGap: Math.round(field.parentElement.getBoundingClientRect().height - a.height),
    }
  })
  const aligned = { box: [0, 0, 0, 0], scrollHeight: 0, scrollTop: 0, metrics: [], inert: 'none', rootGap: 0 }
  expect(await geometry()).toEqual(aligned)

  // It follows the field's scroll, and its size when the field is resized.
  await spec.evaluate((field) => { field.scrollTop = field.scrollHeight })
  await expect.poll(geometry).toEqual(aligned)
  await spec.evaluate((field) => { field.style.height = `${field.offsetHeight + 60}px` })
  await expect.poll(geometry).toEqual(aligned)

  // A click on the tint lands in the field, inside the reference.
  const tint = await hl.locator('.ref-hl-ref').boundingBox()
  await page.mouse.click(tint.x + 8, tint.y + tint.height / 2)
  await expect(spec).toBeFocused()
  const at = await spec.evaluate((field) => field.selectionStart)
  const start = (await spec.inputValue()).indexOf(`see ${ref}`) + 4
  expect(at).toBeGreaterThan(start)
  expect(at).toBeLessThan(start + ref.length)

  // A body with nothing to tint draws no tint.
  await spec.fill('no references')
  await expect(hl).toBeHidden()
})

test('test_task_composer_carries_its_project_once_the_list_loads', async ({ page }) => {
  const s = seed(mark())
  let release
  const held = new Promise((done) => { release = done })
  await page.route((url) => url.pathname === '/api/repos', async (route) => {
    await held
    await route.continue()
  })
  await page.goto(`/tasks?project=${s.project}`)
  await page.getByTestId('task-new').click()
  const composerProject = page.getByTestId('task-composer-project')
  await expect(composerProject).toContainText('No project')

  release()
  await expect(composerProject).toContainText(s.name)
  const asked = page.waitForRequest((req) => {
    const url = new URL(req.url())
    return url.pathname === '/api/refs' && url.searchParams.get('q') === 'alp'
  })
  await page.locator('#task-body').pressSequentially('@alp')
  expect(new URL((await asked).url()).searchParams.get('project_id')).toBe(String(s.project))
})
