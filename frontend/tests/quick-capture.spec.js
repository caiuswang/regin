import { test, expect } from './auth-fixture.js'

// Serial: these share the one `agent_tasks` table and count rows on /tasks.
test.describe.configure({ mode: 'serial' })

const MOD = process.platform === 'darwin' ? 'Meta' : 'Control'

const PNG_B64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM' +
  'IQAAAABJRU5ErkJggg=='

// Paste is the gesture this bubble exists for — a screenshot of the thing you
// just noticed — and no Playwright API fires one carrying a file, so the
// ClipboardEvent is built in the page with the shape a real paste has.
async function pasteInto(page, selector) {
  await page.locator(selector).focus()
  await page.evaluate(([sel, data]) => {
    const bytes = Uint8Array.from(atob(data), c => c.charCodeAt(0))
    const dt = new DataTransfer()
    dt.items.add(new File([bytes], 'pasted.png', { type: 'image/png' }))
    document.querySelector(sel).dispatchEvent(
      new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }))
  }, [selector, PNG_B64])
}

function watchConsole(page) {
  const errors = []
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()) })
  page.on('pageerror', e => errors.push(String(e)))
  return errors
}

// Grouping and every filter axis persist in localStorage, so a run would
// otherwise inherit whatever the last one left selected. What this file reads
// on /tasks is the flat list, which is `group='none'`.
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

// The task specs share one `agent_tasks` table and run in parallel workers,
// and a retry re-runs this whole serial file against a table the attempt
// before it already wrote to. Every capture a /tasks assertion reads back
// carries this attempt's marker in its body, and the list is narrowed to that
// marker server-side.
function runMark() {
  const info = test.info()
  return `bubble${info.workerIndex}x${info.retry}`
}

async function markDraft(page) {
  await page.locator('#quick-capture-body').fill(runMark())
}

async function gotoTasks(page) {
  await page.goto('/tasks')
  await page.getByTestId('task-search').fill(runMark())
  await page.getByTestId('task-search').press('Enter')
}

// The keyboard path runs through the palette, which owns its own keyboard:
// ⌘K to open it, then ctrl+i — the same shape as its ctrl+s scope toggle.
async function openByChord(page) {
  await expect(page.getByTestId('quick-capture-fab')).toBeVisible()
  await page.keyboard.press(`${MOD}+KeyK`)
  await expect(page.locator('[data-search-palette]')).toBeVisible()
  await page.keyboard.press('Control+KeyI')
  await expect(page.getByTestId('quick-capture-form')).toBeVisible()
  // The palette must be gone, not stacked underneath: two trapped focus
  // scopes leave the lower dialog un-typeable.
  await expect(page.locator('[data-search-palette]')).toHaveCount(0)
}

test.describe('quick idea capture', () => {
  test('the bubble is reachable from a route that is not /tasks', async ({ page }) => {
    const errors = watchConsole(page)
    await page.goto('/settings')
    await expect(page.getByTestId('quick-capture-fab')).toBeVisible()

    await page.getByTestId('quick-capture-fab').click()
    await expect(page.getByTestId('quick-capture-form')).toBeVisible()
    // The chord's point is that you never left the page you were on.
    expect(new URL(page.url()).pathname).toBe('/settings')
    expect(errors).toEqual([])
  })

  test('the chord opens it from anywhere, focused on the title', async ({ page }) => {
    await page.goto('/repos')
    await openByChord(page)
    await expect(page.locator('#quick-capture-title')).toBeFocused()

    await page.keyboard.type('captured from the bubble')
    await markDraft(page)
    await page.getByTestId('quick-capture-save').click()
    await expect(page.getByTestId('quick-capture-form')).toBeHidden()

    await gotoTasks(page)
    await expect(page.getByTestId('task-list').getByText('captured from the bubble', { exact: true }))
      .toBeVisible()
  })

  test('capturing while on /tasks refreshes the list underneath', async ({ page }) => {
    await gotoTasks(page)
    await expect(page.getByTestId('task-list')).toBeVisible()
    const before = await page.locator('.task-row').count()

    await openByChord(page)
    await page.keyboard.type('captured without leaving tasks')
    await markDraft(page)
    await page.getByTestId('quick-capture-save').click()

    await expect(page.locator('.task-row')).toHaveCount(before + 1)
    await expect(page.locator('.task-title').first())
      .toHaveText('captured without leaving tasks')
  })

  test('an empty title cannot be saved by button or by keyboard', async ({ page }) => {
    await page.goto('/repos')
    await openByChord(page)
    await expect(page.getByTestId('quick-capture-save')).toBeDisabled()

    // The keyboard path must refuse on the same condition, and say so rather
    // than swallowing the keystroke.
    await page.keyboard.press('Enter')
    await expect(page.getByTestId('quick-capture-error')).toContainText('title is required')
    await expect(page.getByTestId('quick-capture-form')).toBeVisible()
  })

  test('Escape keeps the draft', async ({ page }) => {
    await page.goto('/repos')
    await openByChord(page)
    await page.keyboard.type('half-written idea')
    await page.keyboard.press('Escape')
    await expect(page.getByTestId('quick-capture-form')).toBeHidden()

    await openByChord(page)
    await expect(page.locator('#quick-capture-title')).toHaveValue('half-written idea')
    // Leave nothing behind for the next test.
    await page.locator('#quick-capture-title').fill('')
    await page.keyboard.press('Escape')
  })

  test('the palette chord still works with the capture dialog open', async ({ page }) => {
    await page.goto('/repos')
    await openByChord(page)
    await page.keyboard.press(`${MOD}+KeyK`)
    await expect(page.locator('[role="dialog"]').filter({ has: page.locator('input[role="combobox"]') }))
      .toBeVisible()
  })

  test('no global chord is bound — the browser keeps its ⌘-letters', async ({ page }) => {
    await page.goto('/repos')
    await expect(page.getByTestId('quick-capture-fab')).toBeVisible()
    for (const chord of [`${MOD}+KeyI`, 'Control+KeyI', `${MOD}+KeyL`]) {
      await page.keyboard.press(chord)
    }
    await expect(page.getByTestId('quick-capture-form')).toHaveCount(0)
  })

  test('the bubble does not share its corner with the notification stack', async ({ page }) => {
    await page.goto('/tasks')
    const fab = await page.getByTestId('quick-capture-fab').boundingBox()
    const host = await page.locator('.toast-host').boundingBox()
    // The stack is empty-but-positioned here; its bottom edge is what rows
    // would grow up from, and it has to start above the bubble.
    expect(host.y + host.height).toBeLessThanOrEqual(fab.y)
  })

  test('an image pasted into the bubble lands on the captured task', async ({ page }) => {
    await gotoTasks(page)
    await openByChord(page)
    await pasteInto(page, '#quick-capture-title')
    // Held in the browser — the row it will hang off does not exist yet.
    await expect(page.getByTestId('task-image-strip').locator('img')).toHaveCount(1)

    await page.locator('#quick-capture-title').fill('idea with a pasted screenshot')
    await markDraft(page)
    await page.getByTestId('quick-capture-save').click()
    await expect(page.getByTestId('quick-capture-form')).toBeHidden()

    const row = page.locator('.task-row').first()
    await expect(row.locator('.task-title')).toHaveText('idea with a pasted screenshot')
    await expect(row.getByTestId('task-image-count')).toHaveText('1 image')
  })

  test('a held Enter captures the idea once, not twice', async ({ page }) => {
    await gotoTasks(page)
    await expect(page.getByTestId('task-list')).toBeVisible()
    const before = await page.locator('.task-row').count()

    await openByChord(page)
    await page.keyboard.type('pressed enter twice in a hurry')
    await markDraft(page)
    await page.locator('#quick-capture-title').focus()
    // The keyboard path has to refuse a second submit on the same condition
    // that disables the button, or the in-flight POST gets a twin.
    await page.keyboard.press('Enter')
    await page.keyboard.press('Enter')

    await expect(page.locator('.task-row')).toHaveCount(before + 1)
  })

  test('the bubble stands down where a phone view owns the bottom edge', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto('/live')
    await expect(page.getByTestId('quick-capture-fab')).toBeHidden()

    // Standing down must not strand capture: a phone has no chord, so the
    // drawer carries it on exactly the routes the bubble gives up.
    await page.getByLabel('Open navigation').click()
    await page.getByTestId('drawer-capture-idea').click()
    await expect(page.getByTestId('quick-capture-form')).toBeVisible()
    await page.keyboard.press('Escape')

    // Only at that width: the wide layout leaves the corner free.
    await page.setViewportSize({ width: 1440, height: 900 })
    await expect(page.getByTestId('quick-capture-fab')).toBeVisible()
  })

  test('the bubble stays visible and clear of the edge on a phone', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto('/tasks')
    const fab = page.getByTestId('quick-capture-fab')
    await expect(fab).toBeVisible()
    const box = await fab.boundingBox()
    expect(box.width).toBeGreaterThanOrEqual(44)
    expect(box.height).toBeGreaterThanOrEqual(44)
    expect(box.x + box.width).toBeLessThanOrEqual(390)
    expect(box.y + box.height).toBeLessThanOrEqual(844)
  })
})
