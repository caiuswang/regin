// A task row offers a verb either as its primary action (`task-action-<verb>`)
// or inside its ⋯ menu (`task-verb-<verb>`), by state — the server decides
// which (lib/tasks/verbs.py). The menu is portalled to <body>, so its item is
// looked up on the page, not inside the row.
export async function rowAction(row, verb) {
  const primary = row.getByTestId(`task-action-${verb}`)
  const menu = row.getByTestId('task-menu')
  // count() does not wait: before the row renders it reads 0 and would send
  // a primary verb to the menu, where it is never offered.
  await primary.or(menu).first().waitFor()
  if (await primary.count()) {
    await primary.click()
    return
  }
  await menu.click()
  await row.page().getByRole('menu').getByTestId(`task-verb-${verb}`).click()
}

// lib/tasks/verbs.py's table, for rows a spec serves itself: a mocked row
// without `action`/`menu` would render no controls at all.
const PRIMARY = { idea: 'arm', armed: 'run', running: 'watch', failed: 'retry' }
const MENU = {
  idea: ['edit', 'deps', 'mark_done', 'archive', 'delete'],
  armed: ['disarm', 'arm', 'edit', 'deps', 'mark_done', 'archive', 'delete'],
  running: ['interrupt'],
  failed: ['arm', 'mark_done', 'archive', 'delete'],
  done: ['reopen', 'archive'],
  archived: ['restore', 'delete'],
}
export function verbsFor(status) {
  return { action: PRIMARY[status] ?? null, menu: MENU[status] ?? [] }
}
