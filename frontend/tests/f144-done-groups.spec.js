/**
 * Feature #144 S4 — done tasks are listed last in Next step, and the Overview
 * names what finished this week.
 *
 * The scratch DB is shared by every spec in the run, so the Next step list is
 * read under the test's own tag and the Overview under a project only this
 * test files tasks into.
 */
import { execFileSync } from 'node:child_process'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, test } from './auth-fixture.js'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..')
const PY = resolve(ROOT, '.venv/bin/python')

function mark() {
  const info = test.info()
  return `dg${info.workerIndex}x${info.retry}x${Date.now().toString(36)}`
}

// Straight through the ORM: `done` is a status no API call puts a task in
// without a real agent, and `updated_at` is what orders a task that never ran.
// `spec` is [[status, hours_ago], …]; with `project` the tasks are filed under a
// fresh repo and its id is returned as `project`.
function seed(tag, spec, { project = false } = {}) {
  const script = `
import json, sys
from datetime import datetime, timedelta, timezone
sys.path.insert(0, ".")
from lib.orm import SessionLocal
from lib.orm.models.sync import Repo
from lib.orm.models.tasks import AgentTask
tag, spec, project = sys.argv[1], json.loads(sys.argv[2]), sys.argv[3] == "1"
now = datetime.now(timezone.utc)
with SessionLocal() as s:
    repo_id = None
    if project:
        repo = Repo(name=tag, path=f"/tmp/{tag}")
        s.add(repo); s.commit(); s.refresh(repo)
        repo_id = repo.id
    ids = []
    for i, (status, hours) in enumerate(spec):
        stamp = (now - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
        row = AgentTask(title=f"{tag} {status} {i}", status=status, source="test",
                        tags_json=json.dumps([tag]), project_id=repo_id,
                        updated_at=stamp)
        s.add(row); s.commit(); s.refresh(row)
        ids.append(row.id)
print(json.dumps({"ids": ids, "project": repo_id}))
`
  const out = execFileSync(PY, ['-c', script, tag, JSON.stringify(spec), project ? '1' : '0'],
    { cwd: ROOT, encoding: 'utf8' })
  return JSON.parse(out.trim().split('\n').pop())
}

const rowIds = async (locator) => (await locator.evaluateAll(
  els => els.map(el => el.dataset.testid))).map(t => Number(t.split('-').pop()))

test('Next step lists done tasks last', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  const tag = mark()
  const hours = [5, 1, 7, 3, 2, 6, 4]
  const { ids } = seed(tag, [...hours.map(h => ['done', h]), ['armed', 0]])
  const newest = hours.map((h, i) => [h, ids[i]]).sort((a, b) => a[0] - b[0]).map(([, id]) => id)

  const feed = page.waitForResponse(r => r.url().includes('/api/tasks?')
    && r.url().includes('group=next_step') && !r.url().includes('only='))
  await page.goto(`/tasks?tag=${tag}`)
  const body = await (await feed).json()

  expect('done_hidden' in body).toBe(false)
  expect(body.open).toBe(1)
  const sections = page.getByTestId('next-step-list').locator('section[data-testid^="next-step-group-"]')
  await expect(sections.last()).toHaveAttribute('data-testid', 'next-step-group-done')
  const done = page.getByTestId('next-step-group-done')
  await expect(done.getByTestId('next-step-n')).toHaveText('7')
  await expect(done.getByTestId('next-step-more-done')).toHaveText('2 more done')
  const shown = done.locator('[data-testid^="next-step-row-"]')
  await expect(shown).toHaveCount(5)
  expect(await rowIds(shown)).toEqual(newest.slice(0, 5))
  await expect(page.getByTestId('list-count').first()).toHaveText('1 open · 7 done')
})

test('Group address for done', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 600 })
  const tag = mark()
  seed(tag, [...Array.from({ length: 12 }, () => ['idea', 1]),
             ...Array.from({ length: 12 }, (_, i) => ['armed', i]),
             ['done', 1], ['done', 2]])

  await page.goto(`/tasks?tag=${tag}&group=done`)

  const done = page.getByTestId('next-step-group-done')
  await expect(done).toHaveAttribute('data-anchored', 'true')
  await expect(done.getByTestId('next-step-toggle-done')).toBeInViewport()
  await expect(page).toHaveURL(/group=done/)
})

test('Overview shows recently done tasks', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  const tag = mark()
  const { ids, project } = seed(tag, [['done', 30], ['done', 2], ['done', 50], ['armed', 0]],
    { project: true })

  await page.goto(`/overview?project=${project}`)

  const block = page.getByTestId('run-next-done')
  await expect(block).toContainText('Done recently')
  await expect(page.getByTestId('run-next-done-week')).toHaveText('· 3 this week')
  const rows = page.getByTestId('run-next').locator('[data-testid^="run-next-done-row-"]')
  await expect(rows).toHaveCount(3)
  expect(await rowIds(rows)).toEqual([ids[1], ids[0], ids[2]])

  await page.getByTestId('run-next-done-all').click()
  await expect(page).toHaveURL(new RegExp(`/tasks\\?(?=.*view=list)(?=.*group=done)(?=.*project=${project}\\b)`))
})

test('Overview with nothing done hides the block', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  const tag = mark()
  const { project } = seed(tag, [['armed', 0], ['done', 24 * 10]], { project: true })

  await page.goto(`/overview?project=${project}`)

  await expect(page.getByTestId('run-next')).toBeVisible()
  await expect(page.getByTestId('run-next-done')).toHaveCount(0)
  await expect(page.locator('[data-testid^="run-next-done-row-"]')).toHaveCount(0)
})
