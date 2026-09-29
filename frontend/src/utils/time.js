// Every Pipeline stamp is UTC once it leaves the server, but most are written
// without a zone (`datetime('now')` → "2026-09-29 08:18:28"), and `new Date()`
// reads a zone-less ISO string as *local*. Days and clock times are printed in
// the viewer's zone; the UTC stamp goes in the `title`.

const ZONED = /(?:[zZ]|[+-]\d\d:?\d\d)$/

/** A server stamp as a Date; a zone-less one is UTC. */
export function parseUtc(iso) {
  if (!iso) return null
  const text = String(iso).trim().replace(' ', 'T')
  const d = new Date(ZONED.test(text) ? text : `${text}Z`)
  return Number.isNaN(d.valueOf()) ? null : d
}

/** "Sep 29" in the viewer's zone. */
export function localDay(iso) {
  const d = parseUtc(iso)
  return d ? d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) : '—'
}

/** "Sep 29, 14:05" in the viewer's zone. */
export function localStamp(iso) {
  const d = parseUtc(iso)
  if (!d) return '—'
  const time = d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
  return `${localDay(iso)}, ${time}`
}

/** "2026-09-29 08:18 UTC" — the unambiguous reading, for a `title`. */
export function utcTitle(iso) {
  const d = parseUtc(iso)
  return d ? `${d.toISOString().slice(0, 16).replace('T', ' ')} UTC` : undefined
}
