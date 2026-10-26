/**
 * The `@` menu inside a feature: that feature and its work lead the menu, a
 * step's row names the feature it belongs to, and the highlighted row is
 * previewed beside the list.
 *
 * Unstubbed, against the real `/api/refs` and `/api/refs/preview`; each test
 * seeds its own project and finds its rows by the ids the seed returned.
 */
import { execFileSync } from 'node:child_process'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, test } from './auth-fixture.js'
import { contentOverflow } from './helpers/overflow.js'

// The rich face is the default editor now; these specs drive the source one.
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('regin_editor_mode', 'source')
    localStorage.setItem('regin_editor_mode:compose', 'source')
  })
})

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..')
const PY = resolve(ROOT, '.venv/bin/python')

function mark() {
  const info = test.info()
  return `rc${info.workerIndex}x${info.retry}x${Date.now().toString(36)}`
}

// B is the feature the composer writes inside. Its step's title says nothing
// on its own; A owns a task whose title is the better text match.
function seed(tag) {
  const script = `
import json, sys
sys.path.insert(0, ".")
from lib.orm import SessionLocal
from lib.orm.models.sync import Repo
from lib.orm.models.tasks import AgentTask
from lib.features import store as features
from lib.defects import store as defects
tag = sys.argv[1]
with SessionLocal() as s:
    repo = Repo(name=f"refs-{tag}", path="/nonexistent/" + tag, is_active=1,
                default_branch="master", description="ref context e2e")
    s.add(repo); s.commit(); s.refresh(repo)
    project = repo.id
a = features.create(f"Alpha {tag}", project_id=project)
b = features.create(f"Beta {tag}", project_id=project,
                    body="## Intent\\n\\nThe pixel redesign of " + tag)
ids = {}
with SessionLocal() as s:
    for key, title, fid, body in (
            ("mine", f"S14 {tag} verify", b.id, "Run the gates for " + tag),
            ("theirs", f"{tag} verify everything", a.id, "elsewhere")):
        row = AgentTask(title=title, status="idea", source="test", body=body,
                        project_id=project, feature_id=fid)
        s.add(row); s.commit(); s.refresh(row)
        ids[key] = row.id
d = defects.record(f"Delta {tag} broke", feature_id=b.id)
print(json.dumps({"project": project, "A": a.id, "B": b.id, "D": d.id, **ids}))
`
  const out = execFileSync(PY, ['-c', script, tag], { cwd: ROOT, encoding: 'utf8' })
  return JSON.parse(out.trim().split('\n').pop())
}

const menu = (page) => page.getByTestId('ref-menu')
const group = (page, key) => menu(page).getByTestId(`ref-menu-group-${key}`)
const row = (page, ref) => menu(page).locator(`[data-testid="ref-menu-row"][data-ref="${ref}"]`)
const preview = (page) => menu(page).getByTestId('ref-preview')

async function settled(page, query) {
  await expect(menu(page)).toHaveAttribute('data-query', query)
  await expect(menu(page)).not.toHaveClass(/is-stale/)
}

async function specEditor(page, s) {
  await page.goto(`/features/${s.B}`)
  await page.getByTestId('feature-spec-edit').click()
  const field = page.getByTestId('feature-spec-input')
  await field.click()
  await field.press('ControlOrMeta+End')
  await field.pressSequentially('\n')
  return field
}

test('test_at_sign_pins_the_current_feature_and_its_work', async ({ page }) => {
  const s = seed(mark())
  const field = await specEditor(page, s)
  const asked = page.waitForRequest((req) => new URL(req.url()).pathname === '/api/refs')
  await field.pressSequentially('@')
  expect(new URL((await asked).url()).searchParams.get('feature_id')).toBe(String(s.B))
  await settled(page, '')

  const pinned = group(page, 'context').getByTestId('ref-menu-row')
  expect(await pinned.evaluateAll((els) => els.map((el) => el.dataset.ref)))
    .toEqual([`feature:${s.B}`, `task:${s.mine}`, `defect:${s.D}`])
  const order = await menu(page).locator('[data-testid^="ref-menu-group-"]')
    .evaluateAll((els) => els.map((el) => el.dataset.testid))
  expect(order[0]).toBe('ref-menu-group-context')
  // The pinned feature's own step needs no line naming its feature.
  await expect(row(page, `task:${s.mine}`).getByTestId('ref-menu-parent')).toHaveCount(0)
})

test('test_search_ranks_the_current_feature_first_and_names_other_parents', async ({ page }) => {
  const s = seed(mark())
  const field = await specEditor(page, s)
  await field.pressSequentially('@task:verify')
  await settled(page, 'task:verify')

  const tasks = menu(page).getByTestId('ref-menu-row')
  const refs = await tasks.evaluateAll((els) => els.map((el) => el.dataset.ref))
  expect(refs.indexOf(`task:${s.mine}`)).toBe(0)
  expect(refs).toContain(`task:${s.theirs}`)
  await expect(row(page, `task:${s.theirs}`).getByTestId('ref-menu-parent'))
    .toHaveText(new RegExp(`F${s.A} Alpha`))
})

test('test_highlighted_row_is_previewed', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  const s = seed(mark())
  const field = await specEditor(page, s)
  await field.pressSequentially('@task:verify')
  await settled(page, 'task:verify')

  await expect(preview(page).getByTestId('ref-preview-title')).toContainText('verify')
  await expect(preview(page).getByTestId('ref-preview-body')).toContainText('Run the gates for')

  await field.press('ArrowDown')
  const second = await menu(page).locator('[aria-selected="true"]').getAttribute('data-ref')
  expect(second).not.toBe(`task:${s.mine}`)
  await expect(preview(page).getByTestId('ref-preview-title')).not.toContainText('S14')

  // Moving back reads the kept answer: no second request for the first row.
  let asked = 0
  page.on('request', (req) => {
    const url = new URL(req.url())
    if (url.pathname === '/api/refs/preview' && url.searchParams.get('ref') === `task:${s.mine}`) asked += 1
  })
  await field.press('ArrowUp')
  await expect(preview(page).getByTestId('ref-preview-body')).toContainText('Run the gates for')
  expect(asked).toBe(0)

  await field.press('Enter')
  await expect(field).toHaveValue(new RegExp(`\\[T${s.mine} S14 [^\\]]+ verify\\]\\(task:${s.mine}\\) $`))
})

test('test_preview_stays_out_of_a_phone_viewport', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const s = seed(mark())
  const field = await specEditor(page, s)
  await field.pressSequentially('@')
  await settled(page, '')

  await expect(preview(page)).toHaveCount(0)
  const box = await menu(page).boundingBox()
  expect(box.x + box.width).toBeLessThanOrEqual(390)
  expect((await contentOverflow(page)).offenders).toEqual([])
})

test('test_defect_symptom_offers_references_inside_its_feature', async ({ page }) => {
  const s = seed(mark())
  await page.goto(`/defects/${s.D}`)
  await expect(page.getByText(`Delta`).first()).toBeVisible()
  await page.locator('#pipeline-actions [data-testid="defect-new"]').click()
  const field = page.getByTestId('new-defect-symptom')
  await expect(field).toBeVisible()
  // Opened before the defect row loads, the dialog names no feature.
  await expect(page.getByTestId('new-defect-feature-chosen')).toBeVisible()
  const asked = page.waitForRequest((req) => new URL(req.url()).pathname === '/api/refs')
  await field.pressSequentially('@')
  expect(new URL((await asked).url()).searchParams.get('feature_id')).toBe(String(s.B))
  await settled(page, '')
  await expect(group(page, 'context').getByTestId('ref-menu-row').first())
    .toHaveAttribute('data-ref', `feature:${s.B}`)
  await field.press('Escape')
  await expect(menu(page)).toHaveCount(0)
  await expect(page.getByRole('dialog')).toHaveCount(1)
})
