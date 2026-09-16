import { test, expect } from './auth-fixture.js'

// Serial: every test here counts rows in one shared `agent_tasks` table, and
// the count is only meaningful if nothing else in this file is capturing
// concurrently.
test.describe.configure({ mode: 'serial' })

// The list persists grouping and every filter axis in localStorage, so a run
// would otherwise inherit whatever the last one selected. These tests count
// rows in the flat list, which is `group='none'`.
const FLAT_VIEW = {
  'regin.tasks.group': 'none',
  'regin.tasks.status': '',
  'regin.tasks.archived': '',
  'regin.tasks.priority': '',
  'regin.tasks.project': '',
  'regin.tasks.sort': 'created_desc',
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript((state) => {
    for (const [key, value] of Object.entries(state)) {
      localStorage.setItem(key, value)
    }
  }, FLAT_VIEW)
})

// This file shares `agent_tasks` with the other task specs running in parallel
// workers, so every count narrows the SERVER-side search to this attempt's own
// marker — a retry gets a fresh one and never counts what it left behind.
function runMark() {
  const info = test.info()
  return `del${info.workerIndex}x${info.retry}`
}

async function gotoTasks(page) {
  await page.goto('/tasks')
  await page.getByTestId('task-search').fill(runMark())
  await page.getByTestId('task-search').press('Enter')
}

async function capture(page, title) {
  await page.getByTestId('task-new').click()
  await expect(page.locator('#task-title')).toBeVisible()
  await page.fill('#task-title', title)
  await page.fill('#task-body', runMark())
  await page.getByTestId('task-submit').click()
  await expect(page.getByTestId('task-list').getByText(title, { exact: true }))
    .toBeVisible()
}

function confirmButton(page, label) {
  return page.getByRole('button', { name: label, exact: true })
}

test.describe('/tasks — delete', () => {
  test('a row delete asks first, and cancelling keeps the task', async ({ page }) => {
    await gotoTasks(page)
    await capture(page, 'cancel this delete')
    await expect(page.locator('.task-row')).toHaveCount(1)

    await page.locator('.task-row').first().getByTestId('task-delete').click()
    // Named, not counted: the dialog quotes the title the operator was looking
    // at, and says what archiving would have kept.
    await expect(page.getByText('cancel this delete', { exact: false }).last())
      .toBeVisible()
    await confirmButton(page, 'Cancel').click()

    await expect(page.locator('.task-row')).toHaveCount(1)
  })

  test('confirming removes the row for good', async ({ page }) => {
    await gotoTasks(page)
    await capture(page, 'delete this one')
    await expect(page.locator('.task-row')).toHaveCount(2)

    await page.locator('.task-row', { hasText: 'delete this one' }).first()
      .getByTestId('task-delete').click()
    await confirmButton(page, 'Delete task').click()

    await expect(page.locator('.task-row')).toHaveCount(1)
    // Not archived — asking for archived by name finds nothing either.
    await expect(page.getByTestId('task-list')
      .getByText('delete this one', { exact: true })).toHaveCount(0)
  })

  test('the bulk bar deletes the whole selection', async ({ page }) => {
    await gotoTasks(page)
    await capture(page, 'bulk delete a')
    await capture(page, 'bulk delete b')
    await expect(page.locator('.task-row')).toHaveCount(3)

    await page.getByTestId('task-select-all').click()
    await page.getByTestId('task-bulk-delete').click()
    await confirmButton(page, 'Delete tasks').click()

    await expect(page.getByTestId('tasks-empty')).toBeVisible()
    await expect(page.locator('.task-row')).toHaveCount(0)
  })

  test('the detail view deletes and returns to the list', async ({ page }) => {
    await gotoTasks(page)
    await capture(page, 'delete from detail')
    await page.locator('.task-row').first().getByTestId('task-id').click()
    await expect(page.getByTestId('task-detail-delete')).toBeVisible()

    await page.getByTestId('task-detail-delete').click()
    await confirmButton(page, 'Delete task').click()

    // The address can only 404 once the row is gone, so it is left behind.
    await expect(page).toHaveURL(/\/tasks$/)
  })
})
