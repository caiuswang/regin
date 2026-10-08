/**
 * Feature #144 R4 — a defect's symptom takes `@` references when it is edited,
 * and every surface that shows a symptom draws them as chips.
 *
 * Unstubbed for the writes and the resolves: each test seeds its own rows
 * through the ORM. The scratch DB is shared by every spec running in
 * parallel, so the two capped lists (the triage rail, Needs a decision) are
 * read for real and only reordered, putting this test's defect in view.
 */
import { execFileSync } from 'node:child_process'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, test } from './auth-fixture.js'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..')
const PY = resolve(ROOT, '.venv/bin/python')

function mark() {
  const info = test.info()
  return `sr${info.workerIndex}x${info.retry}x${Date.now().toString(36)}`
}

// F; T under F; A attributed to F; Q unattributed (in the triage queue),
// whose symptom cites T among characters that must stay verbatim.
function seed(tag) {
  const script = `
import json, sys
sys.path.insert(0, ".")
from lib.orm import SessionLocal
from lib.orm.models.tasks import AgentTask
from lib.features import store as features
from lib.defects import store as defects
tag = sys.argv[1]
f = features.create(f"Symptom refs {tag}")
with SessionLocal() as s:
    t = AgentTask(title=f"Cited task {tag}", status="idea", source="test", feature_id=f.id)
    s.add(t); s.commit(); s.refresh(t)
    t_id = t.id
a = defects.record(f"plain wording {tag}", feature_id=f.id)
q = defects.record(f"**kept** {tag}\\nsee [x](task:{t_id}) and \`[y](task:{t_id})\`")
print(json.dumps({"F": f.id, "T": t_id, "A": a.id, "Q": q.id}))
`
  const out = execFileSync(PY, ['-c', script, tag], { cwd: ROOT, encoding: 'utf8' })
  return JSON.parse(out.trim().split('\n').pop())
}

async function defectRow(route, id) {
  const origin = new URL(route.request().url()).origin
  const res = await route.fetch({ url: `${origin}/api/defects/${id}`, method: 'GET' })
  return (await res.json()).defect
}

const first = (rows, id, row) => [row, ...rows.filter((r) => r.id !== id)]

async function pinToTriage(page, id) {
  await page.route(/\/api\/defects\?/, async (route) => {
    const url = new URL(route.request().url())
    if (route.request().method() !== 'GET' || url.searchParams.get('scope') !== 'triage') {
      return route.fallback()
    }
    const res = await route.fetch()
    const body = await res.json()
    body.items = first(body.items, id, await defectRow(route, id))
    return route.fulfill({ response: res, json: body })
  })
}

async function pinToNeedsDecision(page, id) {
  await page.route(/\/api\/pipeline\/overview(\?|$)/, async (route) => {
    const res = await route.fetch()
    const body = await res.json()
    const d = await defectRow(route, id)
    body.needs_decision.rows = first(body.needs_decision.rows, id, {
      id: d.id, round: d.round, opened_at: d.opened_at, symptom: d.symptom, paths: d.suspect_paths,
    })
    return route.fulfill({ response: res, json: body })
  })
}

// One chip for T, live and linking to it; the coded `[y](...)` stays source.
async function expectTaskChip(scope, s) {
  const chip = scope.locator(`[data-testid="ref-chip"][data-ref="task:${s.T}"]`)
  await expect(chip).toHaveCount(1)
  await expect(chip).toHaveAttribute('data-state', 'live')
  await expect(chip).toHaveAttribute('href', `/tasks/${s.T}`)
  await expect(scope).toContainText('**kept**')
  await expect(scope).toContainText(`\`[y](task:${s.T})\``)
  await expect(scope).not.toContainText(`[x](task:${s.T})`)
}

test('Editing a symptom takes @ references', async ({ page }) => {
  const s = seed(mark())
  await page.goto(`/defects/${s.A}`)
  await page.getByTestId(`defect-more-${s.A}`).click()
  await page.getByRole('menuitem', { name: 'Edit symptom' }).click()

  const field = page.getByTestId(`defect-edit-field-${s.A}`)
  await field.fill('')
  await field.pressSequentially('blamed on @')
  // The defect's feature leads the menu (`:feature`), so it is offered unsearched.
  const pick = page.getByTestId('ref-menu').locator(`[data-testid="ref-menu-row"][data-ref="feature:${s.F}"]`).first()
  await expect(pick).toBeVisible()
  await pick.click()
  await expect(field).toHaveValue(new RegExp(`\\]\\(feature:${s.F}\\) $`))

  const patched = page.waitForRequest((req) => req.method() === 'PATCH'
    && new URL(req.url()).pathname === `/api/defects/${s.A}`)
  await page.getByTestId(`defect-edit-submit-${s.A}`).click()
  expect((await patched).postDataJSON().symptom).toContain(`](feature:${s.F})`)

  const symptom = page.getByTestId(`defect-row-${s.A}`).locator('.symptom')
  const chip = symptom.locator(`[data-testid="ref-chip"][data-ref="feature:${s.F}"]`)
  await expect(chip).toHaveAttribute('href', `/features/${s.F}`)
  await expect(symptom).not.toContainText(`(feature:${s.F})`)

  await chip.click()
  await expect(page).toHaveURL(new RegExp(`/features/${s.F}$`))
})

test('the other defect forms stay plain fields', async ({ page }) => {
  const s = seed(mark())
  await page.goto(`/defects/${s.A}`)
  await page.getByTestId(`defect-more-${s.A}`).click()
  await page.getByRole('menuitem', { name: 'Edit suspect paths' }).click()
  await page.getByTestId(`defect-paths-field-${s.A}`).pressSequentially('@')
  await expect(page.getByTestId('ref-menu')).toHaveCount(0)
})

test('Symptom references render on every symptom surface', async ({ page }) => {
  const s = seed(mark())

  await page.goto(`/defects/${s.Q}`)
  await expectTaskChip(page.getByTestId(`defect-row-${s.Q}`).locator('.symptom'), s)

  await pinToTriage(page, s.Q)
  await page.goto('/features')
  await expectTaskChip(page.getByTestId(`triage-card-${s.Q}`).locator('.tc-symptom'), s)

  await pinToNeedsDecision(page, s.Q)
  await page.goto('/overview')
  await expectTaskChip(page.getByTestId(`needs-decision-row-${s.Q}`).getByTestId('needs-decision-symptom'), s)
})
