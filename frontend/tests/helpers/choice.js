// A ChoiceField (task-composer-priority, task-arm-launch) is a select under
// the classic theme and a radiogroup of keys under pixel. Both tag each
// option with `data-value`, so a spec picks by value and runs under either.
export async function pickChoice(page, testid, value) {
  const field = page.getByTestId(testid)
  await field.waitFor()
  if (await field.getAttribute('role') === 'radiogroup') {
    await field.locator(`[role="radio"][data-value="${value}"]`).click()
    return
  }
  await field.click()
  await page.getByRole('listbox').locator(`[role="option"][data-value="${value}"]`).click()
}

