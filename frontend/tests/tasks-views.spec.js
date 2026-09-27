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

def task(title, status, prio="none", feature_id=None):
    with SessionLocal() as s:
        row = AgentTask(title=f"{tag} {title}", status=status, source="test",
                        tags_json=json.dumps([tag]), priority=prio,
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
       "feature": feature.id}
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
  await expect(groupN(page, 'ready')).toHaveText('1')
  await expect(groupN(page, 'running')).toHaveText('1')
  await expect(groupN(page, 'blocked')).toHaveText('1')
  await expect(groupN(page, 'ideas')).toHaveText('5')
  await expect(page.getByTestId('list-count')).toHaveText('8 open · 2 done hidden')

  // One visible action per row, decided by the server.
  const ready = page.getByTestId(`next-step-row-${ids.ready}`)
  await expect(ready.getByTestId('task-action-run')).toBeVisible()
  await expect(page.getByTestId(`next-step-row-${ids.running}`).getByTestId('task-action-watch'))
    .toHaveAttribute('href', `/trace/sessions/${tag}-trace`)
  const blocked = page.getByTestId(`next-step-row-${ids.blocked}`)
  await expect(blocked.getByTestId('task-waits')).toHaveText(`waits on #${ids.running} (running)`)
  await expect(blocked.getByRole('button')).toHaveCount(0)
  await expect(page.getByTestId(`next-step-row-${ids.ideas[0]}`).getByTestId('task-action-arm')).toBeVisible()

  // Ideas previews three, and "more" reads the rest from the server.
  const ideas = page.getByTestId('next-step-group-ideas')
  await expect(ideas.locator('li')).toHaveCount(3)
  await ideas.getByTestId('next-step-more-ideas').click()
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
  await expect(page.getByTestId('list-count')).toHaveText('8 open · 2 done hidden')

  // Board: five columns with server counts; failed only behind Done's footer.
  await page.getByTestId('list-views').getByRole('tab', { name: 'Board' }).click()
  await expect(page).toHaveURL(/view=board/)
  const col = s => page.getByTestId(`board-column-${s}`)
  await expect(col('idea').getByTestId('board-column-n')).toHaveText('5')
  await expect(col('armed').getByTestId('board-column-n')).toHaveText('2')
  await expect(col('armed')).toContainText('1 blocked')
  await expect(col('running').getByTestId('board-column-n')).toHaveText('1')
  await expect(col('done').getByTestId('board-column-n')).toHaveText('1')
  await expect(page.getByTestId('board-column-failed')).toHaveCount(0)
  await expect(page.getByTestId(`board-card-${ids.failed}`)).toHaveCount(0)
  await page.getByTestId('board-failed-show').click()
  await expect(page.getByTestId(`board-card-${ids.failed}`)).toBeVisible()
  await expect(page.getByTestId('list-count')).toHaveText(/^10 of \d+ tasks$/)

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
  await expect(run.locator('.nsr-run-label')).toBeHidden()
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
