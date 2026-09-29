/**
 * Feature #125 T2/T3 — one stamp, one local day, on every surface that
 * prints it. Server stamps are naive UTC; 03:30Z on Sep 2 is Sep 1 in Los
 * Angeles, which the old `shortDate` (UTC slice) and `monthDay` (UTC zone)
 * printed as Sep 2 while `taskDay` printed Sep 1.
 */
import { execFileSync } from 'node:child_process'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, test } from './auth-fixture.js'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..')
const PY = resolve(ROOT, '.venv/bin/python')
const STAMP = '2026-09-02 03:30:00'

test.use({ timezoneId: 'America/Los_Angeles' })

function seed(tag) {
  const script = `
import json, sys
sys.path.insert(0, ".")
from sqlmodel import text
from lib.orm import SessionLocal
from lib.orm.models.tasks import AgentTask
from lib.defects import store as defects
from lib.features import store as features
tag, stamp = sys.argv[1], sys.argv[2]
feature = features.create(f"{tag} feature")
defects.record(f"{tag} symptom", feature_id=feature.id)
with SessionLocal() as s:
    row = AgentTask(title=f"{tag} task", status="idea", source="test",
                    tags_json=json.dumps([tag]))
    s.add(row); s.commit(); s.refresh(row)
    task_id = row.id
    s.exec(text("UPDATE agent_tasks SET created_at = :s, updated_at = :s WHERE id = :i")
           .bindparams(s=stamp, i=task_id))
    s.exec(text("UPDATE defects SET opened_at = :s WHERE feature_id = :f")
           .bindparams(s=stamp, f=feature.id))
    defect_id = s.exec(text("SELECT id FROM defects WHERE feature_id = :f")
                       .bindparams(f=feature.id)).one()[0]
    s.commit()
print(json.dumps({"task": task_id, "feature": feature.id, "defect": defect_id}))
`
  const out = execFileSync(PY, ['-c', script, tag, STAMP], { cwd: ROOT, encoding: 'utf8' })
  return JSON.parse(out.trim().split('\n').pop())
}

test('test_local_day_helpers_agree', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  const tag = `ld${test.info().workerIndex}x${Date.now().toString(36)}`
  const ids = seed(tag)

  await page.goto(`/tasks?tag=${tag}&group=none`)
  await expect(page.getByTestId('task-row').locator('.task-day')).toHaveText('Sep 1')

  await page.goto(`/tasks?tag=${tag}`)
  await expect(page.getByTestId(`next-step-row-${ids.task}`).locator('.nsr-day')).toHaveText('Sep 1')

  await page.goto(`/defects?feature_id=${ids.feature}`)
  const when = page.getByTestId(`defect-row-${ids.defect}`).locator('.when')
  await expect(when).toHaveText('Sep 1')
  await expect(when).toHaveAttribute('title', '2026-09-02 03:30 UTC')

  await page.goto(`/tasks/${ids.task}`)
  const created = page.locator('.td-fact', { hasText: 'Created' }).locator('dd')
  await expect(created).toHaveText('Sep 1, 20:30')
  await expect(created).toHaveAttribute('title', '2026-09-02 03:30 UTC')
})
