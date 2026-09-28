// The confirm copy for a feature's lifecycle verbs. Every fact in it is the
// server's (`ship_check`, the stage): this file words them, it decides nothing.

function list(label, rows, word) {
  if (!rows?.length) return null
  return `${label}: ` + rows.map(r => `#${r.id} ${word(r)}`).join(', ')
}

export function shipMessage(title, check) {
  const lines = [
    ...(check?.warnings || []),
    list('Open tasks', check?.open_tasks, t => `(${t.status})`),
    list('Open defects', check?.open_defects, d => `(${(d.symptom || '').slice(0, 60)})`),
  ].filter(Boolean)
  const body = lines.length ? lines.join('\n') : 'No open tasks or defects.'
  return `Claim "${title}" shipped?\n\n${body}`
}

export function reopenMessage(title, stage) {
  return stage === 'abandoned'
    ? `Reopen "${title}"?\n\nIt goes back to spec — no round is counted.`
    : `Reopen "${title}"?\n\nIt goes back to building, and this counts +1 round.`
}
