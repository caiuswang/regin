/**
 * Feature #131 S3 — a reference in a spec renders as a live chip, and a
 * feature, task, defect or session lists the features and tasks that cite it.
 *
 * Unstubbed, like ref-mentions.spec.js: each test seeds its own rows through
 * the ORM and reads the real `/api/features/<id>` and `/api/refs/resolve`.
 * The scratch DB is shared by every spec running in parallel, so rows are
 * found by the ids the seed returned, never by position.
 */
import { execFileSync } from 'node:child_process'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, test } from './auth-fixture.js'
import { API_BASE } from './helpers/api-base.js'
import { BASELINE_TRACE } from './helpers/fixtures.js'

// The rich face is the default editor now; these specs drive the source one.
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('regin_editor_mode', 'source'))
})

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..')
const PY = resolve(ROOT, '.venv/bin/python')

// Rows in order; a title or body names an earlier row's id as `<<KEY>>`.
// `copies` creates that many tasks from one row, keyed `KEY0`, `KEY1`, …
function seed(rows) {
  const script = `
import json, sys
sys.path.insert(0, ".")
import lib.memory as memory
from lib.orm import SessionLocal
from lib.orm.models.tasks import AgentTask
from lib.defects import store as defects
from lib.features import store as features
ids = {}
def fill(text):
    for key, value in ids.items():
        text = text.replace("<<" + key + ">>", str(value))
    return text
def task(key, row):
    with SessionLocal() as s:
        t = AgentTask(title=fill(row["title"]), body=fill(row.get("body") or "") or None,
                      status="idea", source="test", feature_id=ids.get(row.get("feature")))
        s.add(t); s.commit(); s.refresh(t)
        ids[key] = t.id
for row in json.loads(sys.argv[1]):
    if row["kind"] == "feature":
        ids[row["key"]] = features.create(fill(row["title"]),
                                          body=fill(row.get("body") or "") or None).id
    elif row["kind"] == "defect":
        ids[row["key"]] = defects.record(fill(row["title"]),
                                         feature_id=ids.get(row.get("feature"))).id
    elif row["kind"] == "memory":
        ids[row["key"]] = memory.remember(fill(row["body"]), title=fill(row["title"]))
    elif "copies" in row:
        for n in range(row["copies"]):
            task(row["key"] + str(n), row)
    else:
        task(row["key"], row)
print(json.dumps(ids))
`
  const out = execFileSync(PY, ['-c', script, JSON.stringify(rows)], { cwd: ROOT, encoding: 'utf8' })
  return JSON.parse(out.trim().split('\n').pop())
}

const specBody = (page) => page.getByTestId('feature-spec-body')

// Written beside the open page, not through its editor: the ledger hears the
// write as a pipeline frame and re-reads in place, so the body it renders
// changes under the same chips.
async function patchSpec(page, featureId, body) {
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  const res = await page.request.patch(`${API_BASE}/api/features/${featureId}`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { body },
  })
  expect(res.ok()).toBe(true)
}
const chip = (scope, ref) => scope.locator(`[data-testid="ref-chip"][data-ref="${ref}"]`)
const isResolve = (url) => new URL(url).pathname === '/api/refs/resolve'

test('test_reference_renders_as_a_live_chip', async ({ page }) => {
  const s = seed([
    { kind: 'feature', key: 'A', title: 'Alpha' },
    { kind: 'feature', key: 'C', title: 'Citer', body: 'builds on [F<<A>> Old name](feature:<<A>>) today' },
  ])
  // Held until the stored label has been seen: the chip draws before any
  // answer, and the answer then replaces the snapshot with the live title.
  let release
  const held = new Promise((done) => { release = done })
  await page.route((url) => isResolve(url.href), async (route) => {
    await held
    await route.continue()
  })

  await page.goto(`/features/${s.C}`)
  const a = chip(specBody(page), `feature:${s.A}`)
  await expect(a).toHaveText(`F${s.A} Old name`)
  await expect(a).toHaveAttribute('data-state', 'pending')
  release()

  await expect(a).toHaveText(`F${s.A} Alpha`)
  await expect(a).toHaveAttribute('data-state', 'live')
  await expect(a).toHaveAttribute('data-status', 'spec')
  await expect(a).toHaveAttribute('href', `/features/${s.A}`)
  await expect(a.locator('.ref-chip-dot')).toHaveAttribute('aria-label', 'spec')
  await expect(specBody(page)).toHaveText(`builds on F${s.A} Alpha today`)
})

test('test_chip_click_navigates_in_app', async ({ page }) => {
  const s = seed([
    { kind: 'feature', key: 'A', title: 'Alpha' },
    { kind: 'feature', key: 'C', title: 'Citer', body: 'see [F<<A>> Alpha](feature:<<A>>)' },
  ])
  await page.goto(`/features/${s.C}`)
  const a = chip(specBody(page), `feature:${s.A}`)
  await expect(a).toHaveAttribute('data-state', 'live')

  // A modified click is the browser's: a new tab, and this page stays put.
  const [tab] = await Promise.all([
    page.context().waitForEvent('page'),
    a.click({ modifiers: ['ControlOrMeta'] }),
  ])
  await expect.poll(() => tab.url()).toContain(`/features/${s.A}`)
  await tab.close()
  await expect(page).toHaveURL(new RegExp(`/features/${s.C}$`))

  // A marker on window survives only a navigation that never reloaded.
  await page.evaluate(() => { window.__sameDocument = true })
  await a.click()
  await expect(page).toHaveURL(new RegExp(`/features/${s.A}$`))
  await expect(page.getByTestId('feature-id')).toHaveText(`#${s.A}`)
  expect(await page.evaluate(() => window.__sameDocument)).toBe(true)
})

test('test_dangling_reference_renders_not_found', async ({ page }) => {
  const s = seed([
    { kind: 'feature', key: 'C', title: 'Citer', body: 'gone: [F999999 Gone](feature:999999)' },
  ])
  await page.goto(`/features/${s.C}`)
  const gone = chip(specBody(page), 'feature:999999')

  await expect(gone).toHaveAttribute('data-state', 'missing')
  await expect(gone).toHaveText('F999999 Gone')
  await expect(gone).toHaveAttribute('title', 'feature:999999 — not found')
  expect(await gone.evaluate((el) => el.tagName)).toBe('SPAN')
  expect(await gone.getAttribute('href')).toBeNull()
  expect(await gone.evaluate((el) => getComputedStyle(el).textDecorationLine)).toContain('line-through')

  // Renaming the label alone moves no placeholder, so the rendered HTML is
  // unchanged; the chip still has to read the label the body now holds.
  await patchSpec(page, s.C, 'gone: [F999999 Renamed](feature:999999)')
  await expect(gone).toHaveText('F999999 Renamed')
  await expect(gone).toHaveAttribute('data-state', 'missing')
})

// The indented line is a reference to the grammar, which does not model
// indented code, and code to `marked`: it renders as the source it is.
const codeBody = (a, indented) => [
  `inline \`[F${a} x](feature:${a})\` here`,
  '',
  '```',
  `[F${a} y](feature:${a})`,
  '```',
  '',
  `    [F${a} ${indented}](feature:${a})`,
  '',
  `bad [w](feature:abc), titled [t](feature:${a} "t"), live [F${a} v](feature:${a})`,
].join('\n')

test('test_reference_inside_code_stays_code', async ({ page }) => {
  const s = seed([
    { kind: 'feature', key: 'A', title: 'Alpha' },
    { kind: 'feature', key: 'C', title: 'Citer', body: codeBody('<<A>>', 'z') },
  ])
  await page.goto(`/features/${s.C}`)
  const body = specBody(page)

  // Exactly the one reference outside code is a chip.
  await expect(chip(body, `feature:${s.A}`)).toHaveCount(1)
  await expect(chip(body, `feature:${s.A}`)).toHaveText(`F${s.A} Alpha`)
  await expect(body.locator('[data-testid="ref-chip"]')).toHaveCount(1)
  // Fenced, inline and indented code all show the source as typed.
  await expect(body.locator('code')).toHaveText([
    `[F${s.A} x](feature:${s.A})`,
    `[F${s.A} y](feature:${s.A})`,
    `[F${s.A} z](feature:${s.A})`,
  ])
  // A link in a reference's scheme that the grammar refused is plain text.
  await expect(body.locator('a[href^="feature:"]')).toHaveCount(0)
  await expect(body).toContainText('bad w, titled t, live')

  // A label edit inside the indented code changes no HTML; the source shown
  // there still has to be the source the body now holds.
  await patchSpec(page, s.C, codeBody(s.A, 'zz'))
  await expect(body.locator('code').nth(2)).toHaveText(`[F${s.A} zz](feature:${s.A})`)
  await expect(chip(body, `feature:${s.A}`)).toHaveText(`F${s.A} Alpha`)
})

test('test_references_resolve_in_one_request', async ({ page }) => {
  const s = seed([
    { kind: 'feature', key: 'A', title: 'Alpha' },
    { kind: 'feature', key: 'B', title: 'Beta', body: 'no references in this one' },
    { kind: 'task', key: 'G', title: 'Gamma' },
    { kind: 'task', key: 'H', title: 'Eta' },
    {
      kind: 'feature', key: 'C', title: 'Citer',
      body: '[F<<A>> a](feature:<<A>>) [F<<B>> b](feature:<<B>>) [T<<G>> g](task:<<G>>)\n\n'
        + '[T<<H>> h](task:<<H>>), [F999998 x](feature:999998) and [F<<A>> again](feature:<<A>>)',
    },
  ])
  const refs = [`feature:${s.A}`, `feature:${s.B}`, `task:${s.G}`, `task:${s.H}`, 'feature:999998']
  const asked = []
  page.on('request', (req) => { if (isResolve(req.url())) asked.push(new URL(req.url())) })

  await page.goto(`/features/${s.C}`)
  for (const ref of refs) {
    await expect(chip(specBody(page), ref).first()).not.toHaveAttribute('data-state', 'pending')
  }
  expect(asked).toHaveLength(1)
  expect(asked[0].searchParams.getAll('ref').sort()).toEqual([...refs].sort())

  // A body that cites nothing asks nothing.
  await chip(specBody(page), `feature:${s.B}`).click()
  await expect(specBody(page)).toHaveText('no references in this one')
  expect(asked).toHaveLength(1)

  // Past a hundred distinct references, one request per hundred.
  const many = Array.from({ length: 101 }, (_, i) => `feature:${900001 + i}`)
  const big = seed([{ kind: 'feature', key: 'M', title: 'Many',
                      body: many.map((ref) => `[x](${ref})`).join(' ') }])
  await page.goto(`/features/${big.M}`)
  await expect(chip(specBody(page), many[100])).toHaveAttribute('data-state', 'missing')
  await expect(chip(specBody(page), many[0])).toHaveAttribute('data-state', 'missing')
  const batches = asked.slice(1).map((url) => url.searchParams.getAll('ref'))
  expect(batches.map((refs) => refs.length).sort((a, b) => a - b)).toEqual([1, 100])
  expect(batches.flat().sort()).toEqual([...many].sort())
})

test('test_hostile_title_renders_as_text', async ({ page }) => {
  const title = '<img src=x onerror=window.__pwned=1>'
  const s = seed([
    { kind: 'feature', key: 'H', title },
    { kind: 'feature', key: 'C', title: 'Citer', body: 'cites' },
  ])
  await page.goto(`/features/${s.C}`)
  await page.getByTestId('feature-spec-edit').click()
  const input = page.getByTestId('feature-spec-input')
  await input.focus()
  await input.evaluate((el) => el.setSelectionRange(el.value.length, el.value.length))
  await input.pressSequentially(` @F${s.H}`)
  const menu = page.getByTestId('ref-menu')
  await expect(menu).toHaveAttribute('data-query', `F${s.H}`)
  await expect(menu).not.toHaveClass(/is-stale/)
  await input.press('Enter')
  // The menu wrote the label escaped, so `marked` could never read it as HTML.
  await expect(input).toHaveValue(
    `cites [F${s.H} \\<img src=x onerror=window.__pwned=1\\>](feature:${s.H}) `)
  await page.getByTestId('feature-spec-save').click()

  const h = chip(specBody(page), `feature:${s.H}`)
  await expect(h).toHaveAttribute('data-state', 'live')
  await expect(h.locator('.ref-chip-title')).toHaveText(title)
  await expect(page.getByTestId('feature-spec').locator('img')).toHaveCount(0)
  expect(await page.evaluate(() => window.__pwned)).toBeUndefined()
})

test('test_referenced_by_lists_citing_features_and_tasks', async ({ page }) => {
  const s = seed([
    { kind: 'feature', key: 'A', title: 'Alpha', body: 'the target' },
    { kind: 'feature', key: 'C', title: 'Citer', body: 'builds on [F<<A>> Alpha](feature:<<A>>)' },
    { kind: 'task', key: 'G', title: 'Gamma', body: 'needs [F<<A>> Alpha](feature:<<A>>)' },
    { kind: 'task', key: 'O', title: 'Own step', feature: 'A', body: '[F<<A>> Alpha](feature:<<A>>)' },
    { kind: 'feature', key: 'Z', title: 'Popular' },
    { kind: 'task', key: 'Z', title: 'Fan', copies: 22, body: '[F<<Z>> Popular](feature:<<Z>>)' },
  ])
  const backlinks = page.getByTestId('feature-backlinks')
  const order = () => backlinks.getByTestId('ref-chip').evaluateAll((els) => els.map((el) => el.dataset.ref))

  await page.goto(`/features/${s.A}`)
  await expect(backlinks).toContainText('Referenced by')
  await expect(backlinks.getByTestId('ref-chip')).toHaveCount(2)
  expect(await order()).toEqual([`feature:${s.C}`, `task:${s.G}`])
  await expect(chip(backlinks, `feature:${s.C}`)).toHaveText(`F${s.C} Citer`)

  // A save re-seats the whole ledger from the PATCH answer; the list rides it.
  await page.getByTestId('feature-spec-edit').click()
  await page.getByTestId('feature-spec-input').fill('the target, reworded')
  await page.getByTestId('feature-spec-save').click()
  await expect(specBody(page)).toHaveText('the target, reworded')
  expect(await order()).toEqual([`feature:${s.C}`, `task:${s.G}`])

  // A feature nobody cites draws no card.
  await chip(backlinks, `feature:${s.C}`).click()
  await expect(page.getByTestId('feature-id')).toHaveText(`#${s.C}`)
  await expect(specBody(page)).toBeVisible()
  await expect(backlinks).toHaveCount(0)

  // Twenty chips at most, then the remainder as a count, newest first.
  await page.goto(`/features/${s.Z}`)
  await expect(backlinks.getByTestId('ref-chip')).toHaveCount(20)
  await expect(backlinks.getByTestId('feature-backlinks-more')).toHaveText('+2 more')
  expect((await order())[0]).toBe(`task:${s.Z21}`)
})

test('test_task_defect_and_session_pages_list_who_cites_them', async ({ page }) => {
  const s = seed([
    { kind: 'task', key: 'G', title: 'Gamma' },
    { kind: 'task', key: 'L', title: 'Lonely' },
    { kind: 'feature', key: 'P', title: 'Parent' },
    { kind: 'defect', key: 'D', title: 'Delta broke', feature: 'P' },
    {
      kind: 'feature', key: 'C', title: 'Citer',
      body: `[T<<G>> Gamma](task:<<G>>), [D<<D>> Delta](defect:<<D>>) and [S x](session:${BASELINE_TRACE})`,
    },
    { kind: 'task', key: 'H', title: 'Helper', body: 'see [T<<G>> Gamma](task:<<G>>), not `[D<<D>> x](defect:<<D>>)`' },
  ])
  const order = (scope) => scope.getByTestId('ref-chip').evaluateAll((els) => els.map((el) => el.dataset.ref))

  // Features first, then tasks; a citation inside code is no citation.
  await page.goto(`/tasks/${s.G}`)
  const onTask = page.getByTestId('task-backlinks')
  await expect(onTask).toContainText('Referenced by')
  await expect(onTask.getByTestId('ref-chip')).toHaveCount(2)
  expect(await order(onTask)).toEqual([`feature:${s.C}`, `task:${s.H}`])
  await expect(chip(onTask, `task:${s.H}`)).toHaveText(`T${s.H} Helper`)

  // A body that starts citing the task while its page is open is a write the
  // page hears, and the list re-reads. Newest first is by creation: P is older.
  await patchSpec(page, s.P, `slices: [T${s.G} Gamma](task:${s.G})`)
  await expect(onTask.getByTestId('ref-chip')).toHaveCount(3)
  expect(await order(onTask)).toEqual([`feature:${s.C}`, `feature:${s.P}`, `task:${s.H}`])

  await page.goto(`/defects/${s.D}`)
  const onDefect = page.getByTestId('defect-backlinks')
  await expect(onDefect.getByTestId('ref-chip')).toHaveCount(1)
  await chip(onDefect, `feature:${s.C}`).click()
  await expect(page).toHaveURL(new RegExp(`/features/${s.C}$`))

  await page.goto(`/trace/sessions/${BASELINE_TRACE}`)
  const onSession = page.getByTestId('session-backlinks')
  await expect(chip(onSession, `feature:${s.C}`)).toHaveText(`F${s.C} Citer`)

  // Nobody cites it, so nothing is drawn.
  await page.goto(`/tasks/${s.L}`)
  await expect(page.getByTestId('task-detail-actions')).toBeVisible()
  await expect(page.getByTestId('task-backlinks')).toHaveCount(0)
})

test('test_memory_chip_opens_the_memory', async ({ page }) => {
  const s = seed([
    { kind: 'memory', key: 'M', title: 'A lesson to open', body: 'what the lesson says' },
    { kind: 'feature', key: 'C', title: 'Citer', body: 'remember [M lesson](memory:<<M>>)' },
  ])
  await page.goto(`/features/${s.C}`)
  const m = chip(specBody(page), `memory:${s.M}`)
  await expect(m).toHaveAttribute('data-state', 'live')
  await expect(m).toHaveAttribute('href', `/memory?id=${s.M}`)

  await m.click()
  await expect(page).toHaveURL(new RegExp(`/memory\\?id=${s.M}$`))
  await expect(page.getByRole('heading', { name: 'A lesson to open' })).toBeVisible()
})

test('test_references_where_no_chip_can_go_keep_their_source', async ({ page }) => {
  const gif = 'data:image/gif;base64,R0lGODlhAQABAAAAACw='
  const s = seed([
    { kind: 'feature', key: 'A', title: 'Alpha' },
    {
      kind: 'feature', key: 'C', title: 'Citer',
      body: [
        'live [F<<A>> Alpha](feature:<<A>>)',
        '',
        '<span title="[F<<A>> Alpha](feature:<<A>>)">hover</span>',
        '',
        `![shot of [F<<A>> Alpha](feature:<<A>>) here](${gif})`,
        '',
        'glued http://example.com/[F<<A>> Alpha](feature:<<A>>) tail',
        '',
        // Put back as its source, the path runs the autolink on into the
        // reference after it, which then has nowhere to go either.
        'chained http://chain.example/[x.md](file:x.md)[F<<A>> Alpha](feature:<<A>>) end',
        '',
        // A one-space placeholder would end these early, and be read as a link.
        'short <abbr title=[F<<A>>](feature:<<A>>)>abbr</abbr> form',
        '',
        '[doc](/d/[x.md](file:x.md))',
        '',
        '![Alt words](feature:<<A>>)',
      ].join('\n'),
    },
  ])
  await page.goto(`/features/${s.C}`)
  const body = specBody(page)
  const source = `[F${s.A} Alpha](feature:${s.A})`

  await expect(chip(body, `feature:${s.A}`)).toHaveAttribute('data-state', 'live')
  await expect(body.getByTestId('ref-chip')).toHaveCount(1)
  // No placeholder survives anywhere — not as text, not in an attribute.
  const leaked = await body.evaluate((root) => {
    const hits = root.textContent.includes('#regin-ref-') ? ['text'] : []
    for (const el of root.querySelectorAll('*')) {
      for (const attr of el.attributes) {
        if (attr.value.includes('#regin-ref-')) hits.push(`${el.tagName}@${attr.name}`)
      }
    }
    return hits
  })
  expect(leaked).toEqual([])
  // Each keeps what `marked` makes of its own source.
  await expect(body.getByText('hover', { exact: true })).toHaveAttribute('title', source)
  await expect(body.locator(`img[src="${gif}"]`)).toHaveAttribute('alt', `shot of F${s.A} Alpha here`)
  await expect(body.locator('a[href^="http://example.com/"]')).toHaveText(`http://example.com/[F${s.A}`)
  await expect(body.locator('abbr')).toHaveAttribute('title', `[F${s.A}](feature:${s.A})`)
  await expect(body.locator('a[href^="/d/"]')).toHaveText('doc')
  // An image in a reference's scheme is its alt text, never a broken image.
  await expect(body.locator('img[src^="feature:"]')).toHaveCount(0)
  await expect(body).toContainText('Alt words')
})
