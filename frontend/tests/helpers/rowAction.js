// A task row's secondary actions sit behind its ⋯ menu, which Reka portals to
// <body> — so the item is looked up on the page, not inside the row.
export async function rowAction(row, testid) {
  await row.getByTestId('task-row-menu').click()
  await row.page().getByRole('menu').getByTestId(testid).click()
}
