// A ChoiceField (task-composer-priority, task-arm-launch) is a select under
// the classic theme and a radiogroup of keys under pixel; a MenuField
// (task-group, list-sort, features-sort, list-facet-*) is a select under
// classic and a key + listbox under pixel. Every option carries `data-value`,
// so a spec picks by value and runs under either theme.
export async function pickChoice(page, testid, value) {
  const field = page.getByTestId(testid)
  await field.waitFor()
  if (await field.getAttribute('role') === 'radiogroup') {
    await field.locator(`[role="radio"][data-value="${value}"]`).click()
    return
  }
  await field.click()
  // The trigger names the listbox it opened, so a second open menu (or a
  // select inside an open dialog) cannot answer for it.
  const controls = await field.getAttribute('aria-controls')
  const listbox = controls ? page.locator(`[id="${controls}"]`) : page.getByRole('listbox')
  await listbox.locator(`[role="option"][data-value="${value}"]`).click()
}

// The current label a MenuField shows, under either theme.
export function menuValue(page, testid) {
  return page.getByTestId(testid).locator('.ds-select-value, [data-pl-part="menu-value"]')
}
