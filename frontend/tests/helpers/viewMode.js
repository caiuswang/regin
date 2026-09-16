/**
 * Picking a trace view mode, whichever chrome the viewport is showing.
 *
 * From lg up the session header collapses the modes into a menu
 * (`view-mode-menu`); below that the compact sticky strip still renders them
 * as `aria-pressed` buttons. Specs that only want "put the pane in Timeline"
 * should say that, not encode which control is on screen — `responsive.spec`
 * and `trace-chrome-redesign` each drive both sides of the threshold in one
 * run.
 */
import { expect } from '@playwright/test'

const menuTrigger = (page) => page.getByTestId('view-mode-menu')
const modeButton = (page, label) =>
  page.getByRole('button', { name: label, exact: true })

// The mode control is the cheapest "the trace chrome has rendered" signal, so
// it doubles as the post-navigation wait.
export async function waitForViewModes(page, timeout = 10_000) {
  // Not `menu.or(button)`: below lg the menu's element is still in the DOM
  // (`hidden lg:inline-flex`), so an `or` locator matches two nodes and trips
  // strict mode before either can be asked whether it is visible.
  await expect.poll(
    async () => (await menuTrigger(page).isVisible())
      || (await modeButton(page, 'Timeline').isVisible()),
    { timeout },
  ).toBe(true)
}

export async function selectViewMode(page, label) {
  await waitForViewModes(page)
  if (await menuTrigger(page).isVisible()) {
    await menuTrigger(page).click()
    await page.getByRole('menuitemradio', { name: label, exact: true }).click()
    // The menu unmounts on a transition; clicking into the pane before it
    // leaves hits the dismiss overlay instead of the row underneath.
    await expect(page.getByRole('menu')).toHaveCount(0)
    return
  }
  await modeButton(page, label).click()
}

export async function expectViewMode(page, label) {
  if (await menuTrigger(page).isVisible()) {
    await expect(menuTrigger(page)).toHaveText(new RegExp(`^${label}`))
    return
  }
  await expect(modeButton(page, label)).toHaveAttribute('aria-pressed', 'true')
}
