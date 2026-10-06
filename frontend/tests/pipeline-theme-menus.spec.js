/**
 * Feature #139, slice S11 — menus under the pipeline themes. MenuField is a
 * select under classic and a key + listbox under pixel, both a Reka combobox,
 * so every menu behaves the same from the keyboard either way. The Tasks
 * filter popover is the classic TaskFilterMenu with hooks: toggles apply on
 * click, text fields on APPLY. Shared primitives hook themselves only inside
 * the shell (R13), however they were teleported.
 */
import { test, expect } from './auth-fixture.js'
import { menuValue, pickChoice } from './helpers/choice.js'

const plTheme = page => page.evaluate(() => document.documentElement.getAttribute('data-pl-theme'))
const useTheme = (page, theme) => page.addInitScript(
  id => localStorage.setItem('regin_pipeline_theme', id), theme)

async function open(page, theme, path) {
  await useTheme(page, theme)
  await page.goto(path)
  await expect(page.getByTestId('pipeline-tabs')).toBeVisible()
  await expect.poll(() => plTheme(page)).toBe(theme)
}

const MENUS = [
  { name: 'tasks GROUP', path: '/tasks?view=list&group=next_step', testid: 'task-group', param: 'group' },
  // The tasks SORT menu picks the key; the direction is its own key beside it.
  { name: 'tasks SORT', path: '/tasks?view=list&group=none&sort=created:desc', testid: 'list-sort', param: 'sort',
    key: v => v.split(':')[0], url: v => `${v}:desc` },
  { name: 'features SORT', path: '/features?sort=activity:desc', testid: 'features-sort', param: 'sort' },
  { name: 'defects status', path: '/defects', testid: 'list-facet-status', param: 'status' },
  { name: 'defects triage', path: '/defects', testid: 'list-facet-triage', param: 'triage' },
  { name: 'defects SORT', path: '/defects?sort=opened:desc', testid: 'list-sort', param: 'sort',
    key: v => v.split(':')[0], url: v => `${v}:desc` },
]

function param(page, key) {
  return new URL(page.url()).searchParams.get(key) || ''
}

test('test_task_filter_popover', async ({ page }) => {
  await open(page, 'pixel', '/tasks?view=list')
  const trigger = page.getByTestId('task-filter-button')
  await expect(trigger).toBeVisible()
  await expect(trigger).not.toHaveAttribute('data-pl-on', /.*/)

  await trigger.click()
  const popover = page.locator('[data-pl-part="popover"]')
  const form = popover.locator('[data-pl-part="filter"]')
  await expect(form).toBeVisible()
  await expect(form.locator('[data-pl-part="filter-head"]')).toBeVisible()
  await expect(page.getByTestId('task-filter-on')).toHaveText('None on')
  for (const p of ['urgent', 'high', 'medium', 'low', 'none']) {
    await expect(page.getByTestId(`task-filter-priority-${p}`)
      .locator(`[data-pl-glyph="priority.${p}"]`)).toBeVisible()
  }
  await expect(form.locator('[data-pl-part="filter-foot"]')).toHaveAttribute('data-pl-hint', 'Toggles apply now')

  // A toggle applies on the click; a typed tag waits for APPLY.
  await page.getByTestId('task-filter-priority-urgent').click()
  await page.getByTestId('task-filter-tag').fill('release')
  await expect.poll(() => param(page, 'priority')).toBe('urgent')
  expect(param(page, 'tag')).toBe('')
  await expect(page.getByTestId('task-chip-priority')).toContainText('Urgent')
  await expect(page.getByTestId('task-chip-tag')).toHaveCount(0)
  await expect(page.getByTestId('task-filter-on')).toHaveText('1 on')

  await page.getByTestId('task-more-apply').click()
  await expect(form).toHaveCount(0)
  await expect.poll(() => param(page, 'tag')).toBe('release')
  await expect(page.getByTestId('task-chip-priority')).toHaveText(/Priority\s*Urgent/)
  await expect(page.getByTestId('task-chip-tag')).toHaveText(/Tag\s*release/)
  await expect(trigger).toHaveAttribute('data-pl-on', '')

  // Enter in a text field is APPLY too.
  await trigger.click()
  await page.getByTestId('task-filter-source').fill('linear')
  await page.getByTestId('task-filter-source').press('Enter')
  await expect(form).toHaveCount(0)
  await expect.poll(() => param(page, 'source')).toBe('linear')

  // "Open chains only" belongs to the graph.
  await trigger.click()
  await expect(form).toBeVisible()
  await expect(page.getByTestId('task-filter-open')).toHaveCount(0)
  await page.keyboard.press('Escape')
  await expect(form).toHaveCount(0)
  await page.goto('/tasks?view=graph')
  await expect(trigger).toBeVisible()
  await trigger.click()
  await expect(page.getByTestId('task-filter-open')).toBeVisible()
})

test('test_menus_are_keyboard_complete', async ({ page }) => {
  for (const theme of ['pixel', 'classic']) {
    for (const menu of MENUS) {
      await test.step(`${theme}: ${menu.name}`, async () => {
        await open(page, theme, menu.path)
        const key = page.getByTestId(menu.testid)
        await expect(key).toBeVisible()
        if (theme === 'pixel') await expect(key).toHaveAttribute('data-pl-part', 'menu-key')
        else await expect(key).not.toHaveAttribute('data-pl-part', 'menu-key')
        const read = menu.key || (v => v)
        const write = menu.url || (v => v)
        const before = read(param(page, menu.param))

        await key.focus()
        await page.keyboard.press('Enter')
        const listbox = page.getByRole('listbox')
        await expect(listbox).toBeVisible()
        if (theme === 'pixel') {
          await expect(listbox).toHaveAttribute('data-pl-part', 'listbox')
          await expect(listbox.locator('[data-pl-part="listbox-head"]')).toBeVisible()
        }
        const options = listbox.getByRole('option')
        const values = await options.evaluateAll(els => els.map(e => e.dataset.value))
        const labels = (await options.allTextContents()).map(t => t.trim())
        const at = values.indexOf(before)
        expect(at, `${menu.param}=${before} is one of ${values}`).toBeGreaterThanOrEqual(0)
        // Reka opens with the current pick highlighted — pixel's ▶ cursor.
        await expect(options.nth(at)).toHaveAttribute('data-highlighted', '')
        const next = (at + 1) % values.length

        await page.keyboard.press('ArrowDown')
        await expect(options.nth(next)).toHaveAttribute('data-highlighted', '')
        await page.keyboard.press('Enter')
        await expect(listbox).toHaveCount(0)
        await expect.poll(() => param(page, menu.param)).toBe(write(values[next]))
        await expect(menuValue(page, menu.testid)).toHaveText(labels[next])

        // Esc closes without a pick.
        await key.focus()
        await page.keyboard.press('Enter')
        await expect(listbox).toBeVisible()
        await page.keyboard.press('ArrowDown')
        await page.keyboard.press('Escape')
        await expect(listbox).toHaveCount(0)
        expect(param(page, menu.param)).toBe(write(values[next]))
        await expect(menuValue(page, menu.testid)).toHaveText(labels[next])
      })
    }
  }
})

test('test_primitives_outside_the_shell_stay_unhooked', async ({ page }) => {
  await open(page, 'pixel', '/tasks?view=list&group=none')

  // Inside the shell a teleported listbox and popover carry hooks …
  await page.getByTestId('task-filter-button').click()
  await expect(page.locator('[data-pl-part="popover"]')).toBeVisible()
  await page.getByTestId('list-facet-status').click()
  await expect(page.locator('[role="listbox"][data-pl-part="listbox"]')).toBeVisible()
  await expect(page.locator('[role="listbox"] [data-pl-part="listbox-item"]').first()).toBeVisible()
  await page.keyboard.press('Escape')
  await page.keyboard.press('Escape')
  await expect(page.locator('[data-pl-part="popover"]')).toHaveCount(0)

  // … the sidebar's quick capture, opened over the same themed page, is a
  // ui/Dialog from outside the shell and carries none.
  expect(await plTheme(page)).toBe('pixel')
  await page.getByTestId('quick-capture-fab').click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByTestId('quick-capture-form')).toBeVisible()
  await expect(dialog).not.toHaveAttribute('data-pl-part', /.*/)
  await expect(dialog.locator('[data-pl-part]')).toHaveCount(0)
  await expect(dialog.locator('[data-pl-kicker], [data-pl-hint]')).toHaveCount(0)
  await page.keyboard.press('Escape')
  await expect(dialog).toHaveCount(0)

  // The shell's own create dialog, opened the same way from inside, does.
  await page.keyboard.press('c')
  const composer = page.getByRole('dialog')
  await expect(composer).toHaveAttribute('data-pl-part', 'dialog')
  await expect(composer.locator('[data-pl-part="dialog-head"]')).toHaveCount(1)
})

test('test_choice_fields_under_both_themes', async ({ page }) => {
  for (const theme of ['classic', 'pixel']) {
    await test.step(theme, async () => {
      await open(page, theme, '/tasks?view=list&group=next_step')
      await pickChoice(page, 'task-group', 'none')
      await expect.poll(() => param(page, 'group')).toBe('none')
      await pickChoice(page, 'list-sort', 'updated')
      await expect.poll(() => param(page, 'sort')).toBe('updated:desc')
      await expect(menuValue(page, 'list-sort')).toHaveText('Updated')

      await page.keyboard.press('c')
      const priority = page.getByTestId('task-composer-priority')
      await expect(priority).toHaveAttribute('role', theme === 'pixel' ? 'radiogroup' : 'combobox')
      await pickChoice(page, 'task-composer-priority', 'high')
      if (theme === 'pixel') {
        await expect(priority.locator('[data-value="high"]')).toHaveAttribute('aria-checked', 'true')
      } else {
        await expect(priority).toContainText('High')
      }
      await page.keyboard.press('Escape')
      await expect(page.getByRole('dialog')).toHaveCount(0)

      await page.goto('/features?sort=activity:desc')
      await pickChoice(page, 'features-sort', 'rounds:desc')
      await expect.poll(() => param(page, 'sort')).toBe('rounds:desc')
      await expect(menuValue(page, 'features-sort')).toHaveText('Rounds ↓')
    })
  }
})
