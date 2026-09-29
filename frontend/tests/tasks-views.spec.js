/**
 * Feature #110 S3 — the Tasks tab's three readings and its phone layout.
 *
 * Every task a test seeds carries the test's own tag, and the list and board
 * are read under `?tag=<mark>`: the scratch DB is shared by every spec in the
 * run, and a count asserted over the whole backlog would be a count of
 * whatever else happened to be running.
 */
import { execFileSync } from 'node:child_process'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, test } from './auth-fixture.js'
import { contentOverflow } from './helpers/overflow.js'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..')
const PY = resolve(ROOT, '.venv/bin/python')

function mark() {
  const info = test.info()
  return `tv${info.workerIndex}x${info.retry}x${Date.now().toString(36)}`
}

// Straight through the ORM: `running`, `done` and `failed` are statuses no
// API call can put a task in without launching a real agent.
function seed(tag) {
  const script = `
import json, sys
sys.path.insert(0, ".")
from lib.orm import SessionLocal
from lib.orm.models.tasks import AgentTask, AgentTaskDep, AgentTaskRun, PRIORITY_ORDER
from lib.features import store as features
tag = sys.argv[1]
feature = features.create(f"{tag} lane feature")
settled = features.create(f"{tag} settled feature")

def task(title, status, prio="none", feature_id=None, mark=tag):
    with SessionLocal() as s:
        row = AgentTask(title=f"{tag} {title}", status=status, source="test",
                        tags_json=json.dumps([mark]), priority=prio,
                        priority_rank=PRIORITY_ORDER[prio], feature_id=feature_id)
        s.add(row); s.commit(); s.refresh(row)
        return row.id

def dep(a, b):
    with SessionLocal() as s:
        s.add(AgentTaskDep(task_id=a, depends_on_id=b, kind="requires")); s.commit()

ids = {"running": task("running", "running", "high"),
       "ready": task("ready", "armed", "urgent"),
       "blocked": task("blocked", "armed", "high"),
       "ideas": [task(f"idea {i}", "idea", "low") for i in range(5)],
       "done": task("done", "done"),
       "failed": task("failed", "failed"),
       "g_done": task("g done", "done", feature_id=feature.id),
       "g_run": task("g run", "running", feature_id=feature.id),
       "g_wait": task("g wait", "armed", feature_id=feature.id),
       "s_done": task("s done", "done", feature_id=settled.id, mark=f"{tag}s"),
       "feature": feature.id, "settled": settled.id}
dep(ids["blocked"], ids["running"])
dep(ids["g_run"], ids["g_done"])
dep(ids["g_wait"], ids["g_run"])
with SessionLocal() as s:
    s.add(AgentTaskRun(task_id=ids["running"], trace_id=f"{tag}-trace", attempt=1,
                       status="running", started_at="2026-09-01 10:00:00"))
    s.commit()
print(json.dumps(ids))
`
  const out = execFileSync(PY, ['-c', script, tag], { cwd: ROOT, encoding: 'utf8' })
  return JSON.parse(out.trim().split('\n').pop())
}

const groupN = (page, key) => page.getByTestId(`next-step-group-${key}`).getByTestId('next-step-n')

test('test_tasks_list_board_graph_views', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  const tag = mark()
  const ids = seed(tag)

  // List, grouped by next step, is the default reading.
  await page.goto(`/tasks?tag=${tag}`)
  await expect(page.getByTestId('next-step-list')).toBeVisible()
  // A feature's steps are in the backlog like any other task (defect #186).
  await expect(groupN(page, 'ready')).toHaveText('1')
  await expect(groupN(page, 'running')).toHaveText('2')
  await expect(groupN(page, 'blocked')).toHaveText('2')
  await expect(groupN(page, 'failed')).toHaveText('1')
  await expect(groupN(page, 'ideas')).toHaveText('5')
  await expect(page.getByTestId('list-count')).toHaveText('11 open · 2 done hidden')
  await expect(page.getByTestId(`next-step-row-${ids.g_run}`)).toBeVisible()
  await expect(page.getByTestId(`next-step-row-${ids.g_wait}`)).toBeVisible()

  // One visible action per row, decided by the server.
  const ready = page.getByTestId(`next-step-row-${ids.ready}`)
  await expect(ready.getByTestId('task-action-run')).toBeVisible()
  await expect(page.getByTestId(`next-step-row-${ids.running}`).getByTestId('task-action-watch'))
    .toHaveAttribute('href', `/trace/sessions/${tag}-trace`)
  const blocked = page.getByTestId(`next-step-row-${ids.blocked}`)
  await expect(blocked.getByTestId('task-action-waits')).toHaveText(`waits on #${ids.running} (running)`)
  await expect(blocked.getByTestId('task-action-waits')).toHaveAttribute('href', `/tasks/${ids.running}`)
  await expect(blocked.getByTestId('task-action-run')).toHaveCount(0)
  await expect(page.getByTestId(`next-step-row-${ids.ideas[0]}`).getByTestId('task-action-arm')).toBeVisible()

  // Ideas previews three, and "more" reads the rest from the server.
  const ideas = page.getByTestId('next-step-group-ideas')
  await expect(ideas.locator('li')).toHaveCount(3)
  await ideas.getByTestId('next-step-more-ideas').click()
  await expect(ideas.locator('li')).toHaveCount(5)

  // A live frame (any task write) re-reads the list without collapsing the
  // group the reader opened. The renamed title appearing proves the re-read
  // has rendered before the row count is checked.
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect((await page.request.patch(`/api/tasks/${ids.ideas[0]}`, {
    headers: { Authorization: `Bearer ${token}` }, data: { title: `${tag} idea renamed` },
  })).ok()).toBeTruthy()
  await expect(page.getByTestId(`next-step-row-${ids.ideas[0]}`)).toContainText(`${tag} idea renamed`)
  await expect(ideas.locator('li')).toHaveCount(5)

  // "Run all N" posts a run for every Ready task — intercepted, so no agent starts.
  const runs = []
  await page.route('**/api/tasks/*/run', (route) => {
    runs.push(Number(route.request().url().match(/tasks\/(\d+)\/run/)[1]))
    return route.fulfill({ json: { run: { attempt: 1 } } })
  })
  await page.getByTestId('next-step-run-all').click()
  await page.getByRole('button', { name: 'Run all ready tasks', exact: true }).click()
  await expect.poll(() => runs).toEqual([ids.ready])

  // A priority chip: added from the Filter menu, counted by the server, removable.
  await page.getByTestId('task-filter-button').click()
  await page.getByTestId('task-filter-priority-high').click()
  await page.getByTestId('task-filter-priority-urgent').click()
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('task-chip-priority')).toContainText('Priority')
  await expect(page.getByTestId('task-chip-priority')).toContainText('High, Urgent')
  await expect(page).toHaveURL(/priority=urgent(,|%2C)high/)
  await expect(page.getByTestId('list-count')).toHaveText('3 open · 0 done hidden')
  await page.getByTestId('task-chip-remove-priority').click()
  await expect(page).not.toHaveURL(/priority=/)
  await expect(page.getByTestId('list-count')).toHaveText('11 open · 2 done hidden')

  // Board: four columns with server counts; failed only behind Done's footer.
  await page.getByTestId('list-views').getByRole('tab', { name: 'Board' }).click()
  await expect(page).toHaveURL(/view=board/)
  const col = s => page.getByTestId(`board-column-${s}`)
  await expect(col('idea').getByTestId('board-column-n')).toHaveText('5')
  await expect(col('armed').getByTestId('board-column-n')).toHaveText('3')
  await expect(col('armed')).toContainText('2 blocked')
  await expect(col('running').getByTestId('board-column-n')).toHaveText('2')
  await expect(col('done').getByTestId('board-column-n')).toHaveText('2')
  await expect(page.getByTestId('board-column-failed')).toHaveCount(0)
  await expect(page.getByTestId(`board-card-${ids.failed}`)).toHaveCount(0)
  await page.getByTestId('board-failed-show').click()
  await expect(page.getByTestId(`board-card-${ids.failed}`)).toBeVisible()
  await expect(page.getByTestId('list-count')).toHaveText(/^13( of \d+)? tasks$/)

  // Graph: the seeded feature has its own lane; the unmet edge is the blocking one.
  await page.getByTestId('list-views').getByRole('tab', { name: 'Graph' }).click()
  const lane = page.getByTestId(`graph-lane-${ids.feature}`)
  await expect(lane).toContainText(`${tag} lane feature`)
  await expect(lane.getByTestId('graph-lane-count')).toHaveText('1 / 3')
  await expect(page.getByTestId(`graph-edge-${ids.g_wait}-${ids.g_run}`)).toHaveAttribute('data-blocking', '')
  const settled = page.getByTestId(`graph-edge-${ids.g_run}-${ids.g_done}`)
  await expect(settled).not.toHaveAttribute('data-blocking', '')
  await expect(settled).toHaveClass(/is-settled/)
  await expect(page.getByTestId('graph-footer'))
    .toHaveText(/^\d+ chains? · \d+ feature lanes? hidden \(no open tasks\)$/)
  await page.getByTestId('task-chip-remove-open').click()
  await expect(page).toHaveURL(/open=0/)
  await expect(page.getByTestId('graph-footer')).toHaveText(/^\d+ chains?$/)

  // The graph takes the list's filters too (defect #189): the tag it arrived
  // under is a chip, and a priority from the Filter menu narrows the drawing.
  await expect(page.getByTestId('task-chip-tag')).toContainText(tag)
  await page.getByTestId('task-filter-button').click()
  await expect(page.getByTestId('task-filter-open')).toBeVisible()
  await page.getByTestId('task-filter-priority-high').click()
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('task-chip-priority')).toContainText('High')
  await expect(page.getByTestId(`graph-node-${ids.running}`)).toBeVisible()
  await expect(page.getByTestId(`graph-node-${ids.blocked}`)).toBeVisible()
  await expect(page.getByTestId(`graph-node-${ids.ready}`)).toHaveCount(0)
  await expect(lane).toHaveCount(0)
  await page.getByTestId('task-chip-remove-priority').click()
  await expect(lane.getByTestId('graph-lane-count')).toHaveText('1 / 3')
  await page.goto(`/tasks?view=graph&tag=${tag}&priority=medium&open=0`)
  await expect(page.getByTestId('task-graph-empty')).toHaveText('No tasks match these filters.')
  // A feature whose steps are all done: they match, open chains hides them.
  await page.goto(`/tasks?view=graph&feature_id=${ids.settled}`)
  await expect(page.getByTestId('task-graph-empty')).toContainText('No open chains match these filters')
  await page.getByTestId('task-chip-remove-open').click()
  await expect(page.getByTestId(`graph-node-${ids.s_done}`)).toBeVisible()
})

test('test_pipeline_mobile_layouts', async ({ page }) => {
  const tag = mark()
  const ids = seed(tag)

  // Tasks is the list at phone width, whatever the URL asks for.
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto(`/tasks?view=board&tag=${tag}`)
  await expect(page.getByTestId('next-step-list')).toBeVisible()
  await expect(page.getByTestId('task-board')).toHaveCount(0)
  await expect(page.getByTestId('list-views')).toHaveCount(0)

  // The Run action collapses to its glyph, and every row action is a 44px target.
  const run = page.getByTestId(`next-step-row-${ids.ready}`).getByTestId('task-action-run')
  await expect(run.locator('.tpa-label')).toBeHidden()
  for (const action of [run,
    page.getByTestId(`next-step-row-${ids.running}`).getByTestId('task-action-watch')]) {
    const box = await action.boundingBox()
    expect(box.width).toBeGreaterThanOrEqual(44)
    expect(box.height).toBeGreaterThanOrEqual(44)
  }
  await expect(page.getByTestId('task-search-open')).toBeVisible()
  await expect(page.getByTestId('task-sort')).toBeVisible()

  // A chip's remove control is a tap target too.
  await page.goto(`/tasks?tag=${tag}&priority=urgent,high`)
  await expect(page.getByTestId('task-chip-priority')).toContainText('High+')
  const x = await page.getByTestId('task-chip-remove-priority').boundingBox()
  expect(x.width).toBeGreaterThanOrEqual(44)
  expect(x.height).toBeGreaterThanOrEqual(44)

  // No page scrolls sideways, at any width, in either theme.
  for (const theme of ['light', 'dark']) {
    await page.evaluate(t => localStorage.setItem('regin_theme', t), theme)
    for (const [width, views] of [[390, ['list']], [1024, ['list', 'board', 'graph']],
      [1440, ['list', 'board', 'graph']]]) {
      await page.setViewportSize({ width, height: 900 })
      for (const view of views) {
        await page.goto(`/tasks?view=${view}&tag=${tag}`)
        await expect(page.locator('html')).toHaveAttribute('data-theme', theme)
        await expect(page.getByTestId(view === 'list' ? 'next-step-list'
          : view === 'board' ? 'task-board' : 'task-lane-graph')).toBeVisible()
        const overflow = await contentOverflow(page)
        expect(overflow.scrollWidth, `${view} @${width} ${theme}: ${overflow.offenders.join(', ')}`)
          .toBeLessThanOrEqual(overflow.clientWidth + 1)
      }
    }
  }
})

// A board of the given statuses. `ended` is how many days ago a done task's
// one run ended; the run is what `done_week` reads.
function seedBoard(tag, rows) {
  const script = `
import json, sys
from datetime import datetime, timedelta, timezone
sys.path.insert(0, ".")
from lib.orm import SessionLocal
from lib.orm.models.tasks import AgentTask, AgentTaskRun
tag, rows = sys.argv[1], json.loads(sys.argv[2])
stamp = lambda d: (datetime.now(timezone.utc) - timedelta(days=d)).strftime("%Y-%m-%d %H:%M:%S")
ids = []
with SessionLocal() as s:
    for i, row in enumerate(rows):
        task = AgentTask(title=f"{tag} {row['status']} {i}", status=row["status"], source="test",
                         tags_json=json.dumps([tag]))
        s.add(task); s.flush()
        if "ended" in row:
            s.add(AgentTaskRun(task_id=task.id, attempt=1, status="done",
                               started_at=stamp(row["ended"]), ended_at=stamp(row["ended"])))
        ids.append(task.id)
    s.commit()
print(json.dumps(ids))
`
  const out = execFileSync(PY, ['-c', script, tag, JSON.stringify(rows)], { cwd: ROOT, encoding: 'utf8' })
  return JSON.parse(out.trim().split('\n').pop())
}

const widthOf = async locator => (await locator.boundingBox()).width

test('test_tasks_board_sparse_layout', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  const col = s => page.getByTestId(`board-column-${s}`)

  // 1h: Armed is empty — it collapses to a rail, the rest share the row.
  const mixed = mark()
  seedBoard(mixed, [{ status: 'idea' }, { status: 'idea' }, { status: 'running' },
    { status: 'done', ended: 1 }, { status: 'done', ended: 2 }])
  await page.goto(`/tasks?view=board&tag=${mixed}`)
  await expect(col('idea').getByTestId('board-column-n')).toHaveText('2')
  for (const s of ['armed']) {
    await expect(col(s)).toHaveAttribute('data-kind', 'rail')
    expect(await widthOf(col(s))).toBeCloseTo(48, 0)
    await expect(col(s).getByTestId('board-rail-n')).toHaveText('0')
    await expect(col(s).getByTestId('board-rail-label'))
      .toHaveCSS('writing-mode', 'vertical-rl')
    await expect(col(s).locator('.tb-hint, button')).toHaveCount(0)
  }
  const full = await Promise.all(['idea', 'running', 'done'].map(s => widthOf(col(s))))
  for (const w of full) {
    expect(w).toBeGreaterThan(200)
    expect(Math.abs(w - full[0])).toBeLessThanOrEqual(1)
  }
  await expect(page.getByTestId('board-queue-clear')).toHaveCount(0)

  // 1g: only done tasks match — three rails, one Done column spread over a card grid.
  const clear = mark()
  seedBoard(clear, [1, 1, 2, 3, 4, 6, 12].map(ended => ({ status: 'done', ended })))
  await page.goto(`/tasks?view=board&tag=${clear}`)
  await expect(col('done').getByTestId('board-column-n')).toHaveText('7')
  for (const s of ['idea', 'armed', 'running']) {
    await expect(col(s)).toHaveAttribute('data-kind', 'rail')
    expect(await widthOf(col(s))).toBeCloseTo(48, 0)
  }
  const cards = col('done').locator('[data-testid^="board-card-"]')
  await expect(cards).toHaveCount(7)
  const lefts = new Set(await cards.evaluateAll(els => els.map(e => Math.round(e.getBoundingClientRect().left))))
  expect(lefts.size).toBeGreaterThan(1)
  const banner = page.getByTestId('board-queue-clear')
  await expect(banner).toHaveText(/Queue is clear\. Nothing armed or running — 6 tasks done this week\./)

  await banner.getByRole('button', { name: 'New task' }).click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await banner.getByRole('button', { name: 'Switch to List' }).click()
  // List is the default view, so the URL drops the axis rather than spelling it.
  await expect(page).not.toHaveURL(/view=/)
  await expect(page.getByTestId('next-step-list')).toBeVisible()

  // Only a failed task: Done stays open, or its "failed · show" footer has nowhere to live.
  const failedOnly = mark()
  seedBoard(failedOnly, [{ status: 'failed' }])
  await page.goto(`/tasks?view=board&tag=${failedOnly}`)
  await expect(col('done')).toHaveAttribute('data-kind', 'full')
  await expect(page.getByTestId('board-failed-show')).toBeVisible()
  await expect(page.getByTestId('board-queue-clear')).toContainText('0 tasks done this week')

  // Nothing matches: four rails and the banner.
  await page.goto(`/tasks?view=board&tag=${mark()}-empty`)
  await expect(page.locator('[data-kind="rail"]')).toHaveCount(4)
  await expect(page.getByTestId('board-queue-clear')).toBeVisible()
})

// ── Feature #119 S4: task verbs by state (R4a, D5) ──────────────────────────

const menuOf = scope => scope.getByTestId('task-menu')

async function openMenu(page, scope) {
  await menuOf(scope).click()
  return page.getByRole('menu')
}

// A fix task (armed, deps clear) carrying one open defect — what Mark done's
// confirm has to name.
function seedFixTask(tag) {
  const script = `
import json, sys
sys.path.insert(0, ".")
from lib.orm import SessionLocal
from lib.orm.models.tasks import AgentTask
from lib.defects import store as defects
tag = sys.argv[1]
with SessionLocal() as s:
    task = AgentTask(title=f"{tag} fix", status="idea", source="test", kind="fix",
                     tags_json=json.dumps([tag]))
    s.add(task); s.commit(); s.refresh(task)
defect = defects.record(f"{tag} still broken")
from lib.orm.models.defects import Defect
with SessionLocal() as s:
    s.get(Defect, defect.id).fix_task_id = task.id
    s.commit()
print(json.dumps({"task": task.id, "defect": defect.id}))
`
  const out = execFileSync(PY, ['-c', script, tag], { cwd: ROOT, encoding: 'utf8' })
  return JSON.parse(out.trim().split('\n').pop())
}

test('test_task_primary_action_on_every_surface', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  const tag = mark()
  const ids = seed(tag)

  // List (next step): one primary per state, the ⋯ menu per R4a.
  await page.goto(`/tasks?tag=${tag}`)
  const row = id => page.getByTestId(`next-step-row-${id}`)
  await expect(row(ids.ready).getByTestId('task-action-run')).toBeVisible()
  await expect(row(ids.ideas[0]).getByTestId('task-action-arm')).toBeVisible()
  await expect(row(ids.running).getByTestId('task-action-watch')).toBeVisible()
  await expect(row(ids.running).getByTestId('task-action-stop')).toBeVisible()
  await expect(row(ids.blocked).getByTestId('task-action-waits')).toBeVisible()
  const failed = page.getByTestId('next-step-group-failed')
  await expect(failed.getByTestId(`next-step-row-${ids.failed}`).getByTestId('task-action-retry')).toBeVisible()
  let menu = await openMenu(page, row(ids.ready))
  await expect(menu.getByRole('menuitem')).toHaveText(
    ['Disarm', 'Re-arm', 'Edit', 'Dependencies', 'Mark done', 'Archive', 'Delete'])
  await page.keyboard.press('Escape')
  menu = await openMenu(page, row(ids.blocked))
  await expect(menu.getByRole('menuitem')).toHaveText(
    ['Disarm', 'Re-arm', 'Dependencies', 'Edit', 'Archive', 'Delete'])
  await page.keyboard.press('Escape')
  menu = await openMenu(page, row(ids.running))
  await expect(menu.getByRole('menuitem')).toHaveText(['Interrupt'])
  await page.keyboard.press('Escape')

  // Board card: the same primary, and the card still opens the task.
  await page.goto(`/tasks?view=board&tag=${tag}`)
  const card = id => page.getByTestId(`board-card-${id}`)
  await expect(card(ids.ready).getByTestId('task-action-run')).toBeVisible()
  await expect(card(ids.ideas[0]).getByTestId('task-action-arm')).toBeVisible()
  await expect(card(ids.running).getByTestId('task-action-stop')).toBeVisible()
  await expect(card(ids.blocked).getByTestId('task-action-waits')).toBeVisible()
  await card(ids.ideas[1]).locator('.tbc-title').click()
  await expect(page).toHaveURL(new RegExp(`/tasks/${ids.ideas[1]}$`))

  // Graph node: the primary only; the node body opens the detail page.
  await page.goto(`/tasks?view=graph&tag=${tag}`)
  const node = id => page.getByTestId(`graph-node-${id}`)
  await expect(node(ids.ready).getByTestId('task-action-run')).toBeVisible()
  await expect(node(ids.g_wait).getByTestId('task-action-waits')).toHaveText(`waits on #${ids.g_run}`)
  await expect(node(ids.ready).getByTestId('task-menu')).toHaveCount(0)
  await node(ids.g_wait).locator('.tln-title').click()
  await expect(page).toHaveURL(new RegExp(`/tasks/${ids.g_wait}$`))

  // Detail: primary + ⋯ menu in the header.
  await page.goto(`/tasks/${ids.failed}`)
  const head = page.getByTestId('task-detail-actions')
  await expect(head.getByTestId('task-action-retry')).toBeVisible()
  menu = await openMenu(page, head)
  await expect(menu.getByRole('menuitem')).toHaveText(['Re-arm', 'Mark done', 'Archive', 'Delete'])
  await page.keyboard.press('Escape')

  // Disarm from the detail menu: armed → idea, and the primary becomes Arm.
  await page.goto(`/tasks/${ids.ready}`)
  await expect(head.getByTestId('task-action-run')).toBeVisible()
  await openMenu(page, head)
  await page.getByTestId('task-verb-disarm').click()
  await expect(head.getByTestId('task-action-arm')).toBeVisible()
  await expect(page.getByText('idea', { exact: true })).toBeVisible()
})

test('test_task_stop_on_running_rows', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  const tag = mark()
  const ids = seed(tag)
  const stops = []
  await page.route('**/api/tasks/*/stop', (route) => {
    stops.push(Number(route.request().url().match(/tasks\/(\d+)\/stop/)[1]))
    return route.fulfill({ json: { delivered: true, detail: '', trace_id: `${tag}-trace`, run_id: 1 } })
  })

  await page.goto(`/tasks?tag=${tag}`)
  const running = page.getByTestId(`next-step-row-${ids.running}`)
  await expect(running.getByTestId('task-action-watch')).toBeVisible()
  await running.getByTestId('task-action-stop').click()
  // Stop asks first; cancelling sends nothing.
  await page.getByRole('button', { name: 'Cancel', exact: true }).click()
  expect(stops).toEqual([])
  await running.getByTestId('task-action-stop').click()
  await page.getByRole('button', { name: 'Stop run', exact: true }).click()
  await expect.poll(() => stops).toEqual([ids.running])
})

test('test_run_all_names_each_refusal', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  const tag = mark()
  const [started, blocked, busy] = seedBoard(tag,
    [{ status: 'armed' }, { status: 'armed' }, { status: 'armed' }])
  await page.route('**/api/tasks/*/run', (route) => {
    const id = Number(route.request().url().match(/tasks\/(\d+)\/run/)[1])
    if (id === blocked) {
      return route.fulfill({ status: 409, json: { error: 'blocked by 7', blocking: [7] } })
    }
    if (id === busy) {
      return route.fulfill({ status: 409, json: { error: 'launch refused: max_concurrent_runs reached', blocking: [] } })
    }
    return route.fulfill({ status: 201, json: { run: { attempt: 1 } } })
  })

  await page.goto(`/tasks?tag=${tag}`)
  await page.getByTestId('next-step-run-all').click()
  await page.getByRole('button', { name: 'Run all ready tasks', exact: true }).click()

  const report = page.getByTestId('run-all-report')
  await expect(report).toContainText('1 started · 2 refused')
  await expect(report.getByTestId(`run-all-refused-${started}`)).toHaveCount(0)
  const one = report.getByTestId(`run-all-refused-${blocked}`)
  await expect(one).toContainText(`#${blocked} ${tag} armed 1`)
  await expect(one).toContainText('blocked by 7')
  await expect(one.getByTestId('task-blocker-7')).toBeVisible()
  await expect(report.getByTestId(`run-all-refused-${busy}`)).toContainText('max_concurrent_runs reached')
})

test('test_queued_not_offered', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  const tag = mark()
  seed(tag)

  await page.goto(`/tasks?view=board&tag=${tag}`)
  await expect(page.getByTestId('board-column-armed')).toBeVisible()
  await expect(page.getByTestId('board-column-queued')).toHaveCount(0)

  await page.goto(`/tasks?group=none&tag=${tag}`)
  await page.getByTestId('task-filter-button').click()
  await page.getByTestId('list-facet-status').click()
  await expect(page.getByRole('option', { name: 'Armed', exact: true })).toBeVisible()
  await expect(page.getByRole('option', { name: 'Queued', exact: true })).toHaveCount(0)
})

test('mark done confirm names the open defects a fix task closes', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  const tag = mark()
  const ids = seedFixTask(tag)

  await page.goto(`/tasks/${ids.task}`)
  const head = page.getByTestId('task-detail-actions')
  await openMenu(page, head)
  await page.getByTestId('task-verb-mark_done').click()
  const links = page.getByTestId('confirm-links')
  await expect(links.getByRole('link', { name: `defect #${ids.defect}` }))
    .toHaveAttribute('href', `/defects/${ids.defect}`)
  await page.getByRole('button', { name: 'Mark done', exact: true }).click()
  await expect(head.getByTestId('task-action-arm')).toHaveCount(0)
  await expect(page.getByText('done', { exact: true })).toBeVisible()
})

// ── Feature #119 S5: task editing & bulk (R4c) ──────────────────────────────

// Ideas plus one running task, and a live feature to file them under.
function seedEditing(tag) {
  const script = `
import json, sys
sys.path.insert(0, ".")
from lib.orm import SessionLocal
from lib.orm.models.tasks import AgentTask
from lib.features import store as features
tag = sys.argv[1]
feature = features.create(f"{tag} target feature")
ids = {"feature": feature.id, "ideas": []}
with SessionLocal() as s:
    for status in ("idea", "idea", "idea", "running"):
        row = AgentTask(title=f"{tag} {status} {len(ids['ideas'])}", status=status,
                        source="test", tags_json=json.dumps([tag]))
        s.add(row); s.flush()
        if status == "running":
            ids["running"] = row.id
        else:
            ids["ideas"].append(row.id)
    s.commit()
print(json.dumps(ids))
`
  const out = execFileSync(PY, ['-c', script, tag], { cwd: ROOT, encoding: 'utf8' })
  return JSON.parse(out.trim().split('\n').pop())
}

const readTask = (page, id) => page.evaluate(async (tid) => {
  const token = localStorage.getItem('regin_auth_token')
  const r = await fetch(`/api/tasks/${tid}`, { headers: { Authorization: `Bearer ${token}` } })
  return (await r.json()).task
}, id)

async function pickOption(page, trigger, name) {
  await trigger.click()
  await page.getByRole('option', { name, exact: true }).click()
}

test('test_task_edit_sheet_sets_kind_assignee_feature', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  const tag = mark()
  const ids = seedEditing(tag)
  const id = ids.ideas[0]

  await page.goto(`/tasks/${id}`)
  const head = page.getByTestId('task-detail-actions')
  await openMenu(page, head)
  await page.getByTestId('task-verb-edit').click()
  const sheet = page.getByTestId('task-edit-form')
  await expect(sheet).toBeVisible()

  await pickOption(page, sheet.getByTestId('task-edit-kind'), 'build')
  await sheet.getByTestId('task-edit-assignee').click()
  const options = page.getByRole('option')
  await expect(options.nth(1)).toBeVisible()
  await options.filter({ hasNotText: 'Unassigned' }).first().click()

  // The picker searches the server's feature index, not a preloaded page.
  await sheet.getByTestId('task-edit-feature').fill(tag)
  await sheet.getByTestId(`task-edit-feature-option-${ids.feature}`).click()
  await expect(sheet.getByTestId('task-edit-feature-chosen')).toContainText(`#${ids.feature}`)
  await page.getByTestId('task-edit-save').click()
  await expect(sheet).toHaveCount(0)

  await expect(page.getByTestId('task-detail-feature')).toHaveText(`#${ids.feature} ${tag} target feature`)
  await expect(page.getByTestId('task-kind-chip')).toHaveText('build')
  const saved = await readTask(page, id)
  expect(saved.kind).toBe('build')
  expect(typeof saved.assignee).toBe('number')
  expect(saved.feature_id).toBe(ids.feature)

  // Re-opening shows the feature it is filed under; clearing it detaches.
  await openMenu(page, head)
  await page.getByTestId('task-verb-edit').click()
  await expect(sheet.getByTestId('task-edit-feature-chosen'))
    .toContainText(`#${ids.feature} ${tag} target feature`)
  await sheet.getByTestId('task-edit-feature-clear').click()
  await pickOption(page, sheet.getByTestId('task-edit-assignee'), 'Unassigned')
  await page.getByTestId('task-edit-save').click()
  await expect(sheet).toHaveCount(0)
  await expect(page.getByTestId('task-detail-feature')).toHaveCount(0)
  const cleared = await readTask(page, id)
  expect(cleared.feature_id).toBeNull()
  expect(cleared.assignee).toBeNull()
  expect(cleared.kind).toBe('build')
})

test('test_task_detail_archive_and_viewer_gating', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  const tag = mark()
  const ids = seedEditing(tag)
  const id = ids.ideas[0]

  await page.goto(`/tasks/${id}`)
  const head = page.getByTestId('task-detail-actions')
  await head.getByTestId('task-detail-archive').click()
  await expect(page.getByText('archived', { exact: true })).toBeVisible()
  await expect(head.getByTestId('task-detail-archive')).toHaveCount(0)
  await head.getByTestId('task-detail-restore').click()
  await expect(page.getByText('idea', { exact: true })).toBeVisible()
  await expect(head.getByTestId('task-detail-archive')).toBeVisible()

  // The header offers Archive only where the state's menu does.
  await page.goto(`/tasks/${ids.running}`)
  await expect(head.getByTestId('task-menu')).toBeVisible()
  await expect(head.getByTestId('task-detail-archive')).toHaveCount(0)

  // A viewer reads the page and holds no write control on it.
  await page.addInitScript(() => {
    const raw = localStorage.getItem('regin_auth_user')
    if (!raw) return
    const user = JSON.parse(raw)
    user.role = 'viewer'
    localStorage.setItem('regin_auth_user', JSON.stringify(user))
  })
  await page.goto(`/tasks/${id}`)
  await expect(page.getByTestId('task-detail-runs')).toBeVisible()
  for (const testid of ['task-action-arm', 'task-menu', 'task-detail-archive',
    'task-detail-restore', 'task-body-edit', 'task-comment-draft',
    'task-place-feature']) {
    await expect(page.getByTestId(testid)).toHaveCount(0)
  }

  // The list draws no selection for a viewer either.
  await page.goto(`/tasks?tag=${tag}`)
  await expect(page.getByTestId(`next-step-row-${id}`)).toBeVisible()
  await expect(page.getByTestId('task-select')).toHaveCount(0)
})

test('test_tasks_bulk_bar_on_grouped_list', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  const tag = mark()
  const ids = seedEditing(tag)
  const [a, b, c] = ids.ideas
  const row = id => page.getByTestId(`next-step-row-${id}`)

  await page.goto(`/tasks?tag=${tag}`)
  await expect(row(a)).toBeVisible()
  await expect(page.getByTestId('task-bulk')).toHaveCount(0)
  await row(a).getByTestId('task-select').click()
  await row(b).getByTestId('task-select').click()
  const bar = page.getByTestId('task-bulk')
  await expect(bar).toContainText('2 selected')
  // The open backlog has nothing archived to restore.
  await expect(bar.getByTestId('task-bulk-restore')).toHaveCount(0)

  // Set feature: one request, both rows filed under it.
  await bar.getByTestId('task-bulk-feature').fill(tag)
  await bar.getByTestId(`task-bulk-feature-option-${ids.feature}`).click()
  await expect(page.getByTestId('task-bulk')).toHaveCount(0)
  await expect.poll(async () => (await readTask(page, a)).feature_id).toBe(ids.feature)
  expect((await readTask(page, b)).feature_id).toBe(ids.feature)
  expect((await readTask(page, c)).feature_id).toBeNull()

  // Priority through the bar.
  await row(a).getByTestId('task-select').click()
  await pickOption(page, page.getByTestId('task-bulk-priority'), 'High')
  await expect.poll(async () => (await readTask(page, a)).priority).toBe('high')

  // Archive: a row the server refuses is named with its reason, not counted.
  // No agent can be made live here, so the refusal is the server's shape,
  // injected for the running row while the rest go through for real.
  const refusal = 'the task has a live run — stop it before archiving'
  await page.route('**/api/tasks/bulk', async (route) => {
    if (route.request().method() !== 'POST') return route.continue()
    const body = route.request().postDataJSON()
    const rest = body.ids.filter(i => i !== ids.running)
    const res = await route.fetch({ postData: JSON.stringify({ ...body, ids: rest }) })
    const json = await res.json()
    return route.fulfill({ json: { ...json, rejected: [...json.rejected, { id: ids.running, reason: refusal }] } })
  })
  await row(c).getByTestId('task-select').click()
  await row(ids.running).getByTestId('task-select').click()
  await page.getByTestId('task-bulk-archive').click()
  const rejected = page.getByTestId('task-bulk-rejected')
  await expect(rejected.getByTestId(`task-bulk-rejected-${ids.running}`))
    .toHaveText(`#${ids.running} ${tag} running 3 — ${refusal}`)
  await expect(rejected.getByTestId(`task-bulk-rejected-${c}`)).toHaveCount(0)
  await expect(row(c)).toHaveCount(0)
  await expect(row(ids.running)).toBeVisible()

  // Delete asks first, naming the rows.
  await row(b).getByTestId('task-select').click()
  await page.getByTestId('task-bulk-delete').click()
  await expect(page.getByText(`Delete #${b} “${tag} idea 1”`)).toBeVisible()
  await page.getByRole('button', { name: 'Cancel', exact: true }).click()
  await expect(row(b)).toBeVisible()
})

// Defect #196: a graph node clamps its title to two lines, and both lines must
// sit inside the box — at the old 72px height the second was sliced in half,
// so the ellipsis never showed. The full title rides on the link's tooltip.
test('test_graph_node_title_fits_its_box', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  const tag = mark()
  const title = `${tag} S12 — MANUAL — restart from S1 and S2 once the lane settles, then re-run every open defect check`
  const script = `
import json, sys
sys.path.insert(0, ".")
from lib.orm import SessionLocal
from lib.orm.models.tasks import AgentTask
with SessionLocal() as s:
    row = AgentTask(title=sys.argv[2], status="done", source="test", tags_json=json.dumps([sys.argv[1]]))
    s.add(row); s.commit(); s.refresh(row)
    print(row.id)
`
  const id = execFileSync(PY, ['-c', script, tag, title], { cwd: ROOT, encoding: 'utf8' }).trim().split('\n').pop()
  await page.goto(`/tasks?view=graph&tag=${tag}&open=0`)
  const node = page.getByTestId(`graph-node-${id}`)
  const link = node.locator('.tln-title')
  await expect(link).toHaveAttribute('title', title)
  const geo = await node.evaluate((el) => {
    const t = el.querySelector('.tln-title')
    const box = el.getBoundingClientRect()
    const pad = parseFloat(getComputedStyle(el).paddingBottom)
    const lh = parseFloat(getComputedStyle(t).lineHeight)
    return { inner: box.bottom - pad, bottom: t.getBoundingClientRect().bottom, height: t.clientHeight, full: t.scrollHeight, lh }
  })
  expect(geo.full).toBeGreaterThan(geo.height)
  expect(geo.height).toBeGreaterThanOrEqual(2 * geo.lh - 1)
  expect(geo.bottom).toBeLessThanOrEqual(geo.inner + 0.5)
})
