/**
 * Feature #167 S6 — the run-slot holders, shown where a full pool is felt.
 *
 * Tasks are seeded straight through the ORM (no API call can make a task
 * `done` without a real agent); the registry is process memory no spec can
 * populate, so `/api/agent-runs/slots`, the capacity 409 and the stop route are
 * stubbed with the shapes S2 serves.
 */
import { execFileSync } from 'node:child_process'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, test } from './auth-fixture.js'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..')
const PY = resolve(ROOT, '.venv/bin/python')

function mark() {
  const info = test.info()
  return `rs${info.workerIndex}x${info.retry}x${Date.now().toString(36)}`
}

function seed(tag) {
  const script = `
import json, sys
sys.path.insert(0, ".")
from lib.orm import SessionLocal
from lib.orm.models.tasks import AgentTask, AgentTaskRun
tag = sys.argv[1]

def task(title, status):
    with SessionLocal() as s:
        row = AgentTask(title=f"{tag} {title}", status=status, source="test",
                        tags_json=json.dumps([tag]))
        s.add(row); s.commit(); s.refresh(row)
        return row.id

ids = {"armed": task("armed", "armed"), "done": task("done", "done"),
       "failed": task("failed", "failed")}
with SessionLocal() as s:
    s.add(AgentTaskRun(task_id=ids["done"], trace_id=f"{tag}-done", attempt=1,
                       status="done", started_at="2026-10-22 08:00:00"))
    s.add(AgentTaskRun(task_id=ids["failed"], trace_id=f"{tag}-failed", attempt=1,
                       status="failed", started_at="2026-10-22 08:00:00"))
    s.commit()
print(json.dumps(ids))
`
  const out = execFileSync(PY, ['-c', script, tag], { cwd: ROOT, encoding: 'utf8' })
  return JSON.parse(out.trim().split('\n').pop())
}

const holder = (traceId, phase, task = null) => ({
  trace_id: traceId, phase, cwd: '/tmp/repo',
  task_id: task?.id ?? null, task_title: task?.title ?? null, task_status: task?.status ?? null,
})

async function stubStop(page) {
  const stops = []
  await page.route('**/api/agent-runs/*/stop', (route) => {
    stops.push(route.request().url().match(/agent-runs\/([^/]+)\/stop/)[1])
    return route.fulfill({ json: { delivered: true, detail: '' } })
  })
  return stops
}

async function stubRefusal(page, taskId, holders) {
  await page.route(`**/api/tasks/${taskId}/run`, route => route.fulfill({
    status: 409,
    json: { error: 'launch refused: max_concurrent_runs reached', blocking: [],
            slots: { limit: holders.length, used: holders.length, holders } },
  }))
}

test('test_refusal_lets_the_user_close_an_idle_holder', async ({ page }) => {
  const tag = mark()
  const ids = seed(tag)
  const idle = holder(`${tag}-idle`, 'idle', { id: ids.done, title: `${tag} done`, status: 'done' })
  const live = holder(`${tag}-live`, 'working')
  await stubRefusal(page, ids.armed, [idle, live])
  const stops = await stubStop(page)

  await page.goto(`/tasks/${ids.armed}`)
  const run = page.getByTestId('task-detail-actions').getByTestId('task-action-run')
  await run.click()

  const refusal = page.getByTestId('task-refusal')
  await expect(refusal.getByTestId('task-refusal-slots')).toHaveText('运行槽已满（2/2）')
  await expect(run).toBeDisabled()
  const idleRow = refusal.getByTestId(`run-slot-${idle.trace_id}`)
  await expect(idleRow.getByTestId('run-slot-task')).toHaveAttribute('href', `/tasks/${ids.done}`)
  await expect(refusal.getByTestId(`run-slot-${live.trace_id}`).getByTestId('run-slot-open')).toBeVisible()
  await expect(refusal.getByTestId(`run-slot-${live.trace_id}`).getByTestId('run-slot-close')).toHaveCount(0)

  await idleRow.getByTestId('run-slot-close').click()
  await expect.poll(() => stops).toEqual([idle.trace_id])
  await expect(idleRow).toHaveCount(0)
  await expect(refusal.getByTestId('task-refusal-slots')).toHaveText('运行槽已满（1/2）')
  await expect(run).toBeEnabled()
})

test('test_waiting_input_holder_is_opened_not_closed', async ({ page }) => {
  const tag = mark()
  const ids = seed(tag)
  const asking = holder(`${tag}-ask`, 'waiting-input')
  const starting = holder(`${tag}-start`, 'starting')
  await stubRefusal(page, ids.armed, [asking, starting])
  const stops = await stubStop(page)

  await page.goto(`/tasks/${ids.armed}`)
  await page.getByTestId('task-detail-actions').getByTestId('task-action-run').click()

  const row = page.getByTestId(`run-slot-${asking.trace_id}`)
  await expect(row.getByTestId('run-slot-open')).toHaveAttribute('href', `/live/${asking.trace_id}`)
  await expect(row.getByTestId('run-slot-close')).toHaveCount(0)
  const startRow = page.getByTestId(`run-slot-${starting.trace_id}`)
  await expect(startRow.getByTestId('run-slot-phase')).toHaveText('starting')
  await expect(startRow.locator('a, button')).toHaveCount(0)
  expect(stops).toEqual([])
})

test('test_done_task_with_open_session_shows_close_banner', async ({ page }) => {
  const tag = mark()
  const ids = seed(tag)
  const open = holder(`${tag}-done`, 'idle', { id: ids.done, title: `${tag} done`, status: 'done' })
  let holders = [open]
  await page.route('**/api/agent-runs/slots', route => route.fulfill({
    json: { limit: 4, used: holders.length, holders } }))
  const stops = await stubStop(page)

  await page.goto(`/tasks/${ids.done}`)
  const banner = page.getByTestId('task-open-session')
  await expect(banner).toContainText('会话仍开着（idle），占用 1 个运行槽')
  holders = []
  await banner.getByTestId('task-open-session-close').click()
  await expect.poll(() => stops).toEqual([open.trace_id])
  await expect(banner).toHaveCount(0)

  // An armed task holds no finished session, so it carries no banner.
  await page.goto(`/tasks/${ids.armed}`)
  await expect(page.getByTestId('task-detail-actions')).toBeVisible()
  await expect(page.getByTestId('task-open-session')).toHaveCount(0)
})

test('test_overview_lists_finished_tasks_still_holding_a_slot', async ({ page }) => {
  const tag = mark()
  const ids = seed(tag)
  const open = holder(`${tag}-done`, 'idle', { id: ids.done, title: `${tag} done`, status: 'done' })
  let holders = [open]
  await page.route('**/api/agent-runs/slots', route => route.fulfill({
    json: { limit: 4, used: holders.length, holders } }))
  const stops = await stubStop(page)

  await page.goto('/overview')
  const running = page.getByTestId('running-now')
  await expect(running.getByTestId('running-now-finished-title')).toHaveText('已完成但会话未关闭')
  const row = running.getByTestId('running-now-finished').getByTestId(`run-slot-${open.trace_id}`)
  await expect(row.getByTestId('run-slot-task')).toContainText(`#${ids.done}`)
  const chip = running.getByTestId('run-slots-chip')
  await expect(chip).toHaveText('Run slots 1/4')
  await chip.click()
  await expect(running.getByTestId('run-slots-holders').getByTestId(`run-slot-${open.trace_id}`)).toBeVisible()

  holders = []
  await row.getByRole('button', { name: `Close session #${ids.done}` }).click()
  await expect.poll(() => stops).toEqual([open.trace_id])
  await expect(running.getByTestId('running-now-finished')).toHaveCount(0)
})

test('test_closing_from_the_banner_releases_the_refusal', async ({ page }) => {
  const tag = mark()
  const ids = seed(tag)
  const own = holder(`${tag}-failed`, 'idle', { id: ids.failed, title: `${tag} failed`, status: 'failed' })
  let holders = [own]
  await page.route('**/api/agent-runs/slots', route => route.fulfill({
    json: { limit: 1, used: holders.length, holders } }))
  await stubRefusal(page, ids.failed, [own])
  const stops = await stubStop(page)

  await page.goto(`/tasks/${ids.failed}`)
  const retry = page.getByTestId('task-detail-actions').getByTestId('task-action-retry')
  await retry.click()
  await expect(page.getByTestId('task-refusal-slots')).toHaveText('运行槽已满（1/1）')
  await expect(retry).toBeDisabled()

  holders = []
  await page.getByTestId('task-open-session-close').click()
  await expect.poll(() => stops).toEqual([own.trace_id])
  await expect(page.getByTestId('task-open-session')).toHaveCount(0)
  await expect(page.getByTestId(`run-slot-${own.trace_id}`)).toHaveCount(0)
  await expect(retry).toBeEnabled()
})
