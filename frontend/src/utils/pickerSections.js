// Splits the picker's server-ordered project rows into workspace sections
// (header + its member rows, in server order) and the rows no workspace holds.
// A repo under two nested workspace roots is listed once, under the first by
// name, so one scope never owns two rows in the keyboard order.
export function workspaceSections(workspaces, items, q = '') {
  const term = q.trim().toLowerCase()
  const owner = new Map()
  for (const ws of workspaces) {
    for (const m of ws.members || []) if (!owner.has(m.id)) owner.set(m.id, ws.id)
  }
  const sections = workspaces
    .map(ws => ({ ws, rows: items.filter(r => r.id != null && owner.get(r.id) === ws.id) }))
    .filter(s => s.rows.length || !term || s.ws.name.toLowerCase().includes(term))
  const loose = items.filter(r => r.id == null || !owner.has(r.id))
  return { sections, loose }
}
