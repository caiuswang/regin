// Shared "is this session active?" rule — ONE frontend source for the
// green badge, delete-warning, and the /live poll cadence (SessionsView,
// SessionRow, useLiveTail). Mirrors the server's rule: status='active' is
// active, status='ended' is not, anything else (unset, unknown future
// values) falls back to the last-seen recency check.
export const STALE_FALLBACK_WINDOW_MS = 10 * 60 * 1000

// Backend timestamps are naive local ISO ("2026-07-04T09:56:24.990768").
// Parse them explicitly as LOCAL time — engine-dependent Date() parsing of
// naive strings has burned this codebase before. Falls back to Date(iso)
// for anything non-matching (already-zoned strings).
export function parseLocalIso(iso) {
  if (!iso) return null
  // A string carrying an EXPLICIT zone (trailing Z or ±hh:mm — e.g. spans
  // ingested verbatim from a client's toISOString) must parse natively;
  // the manual local parse below is only for naive backend timestamps.
  if (/(?:Z|[+-]\d{2}:?\d{2})$/.test(iso)) return new Date(iso)
  const m = iso.match(/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?/)
  if (!m) return new Date(iso)
  const ms = m[7] ? parseInt(m[7].slice(0, 3).padEnd(3, '0'), 10) : 0
  return new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6], ms)
}

// Age of a backend timestamp measured server−server, so a viewer whose
// browser sits in a different timezone than the host never reads a naive
// host-local stamp as future. `clock` is `{ local, utc, atMs }`: the
// envelope's server_now / server_now_utc plus the client Date.now() captured
// when that envelope landed. The anchor is picked to MATCH the row's format
// (zoned stamp → zoned anchor), so the parse-mode offset cancels; the
// (now − atMs) term keeps ages advancing while the page sits unrefreshed.
export function serverAgeMs(iso, clock, nowMs = Date.now()) {
  const d = parseLocalIso(iso)
  if (!d) return null
  if (!clock || !clock.local) return nowMs - d.getTime()
  const zoned = /(?:Z|[+-]\d{2}:?\d{2})$/.test(iso)
  const anchor = parseLocalIso(zoned ? clock.utc : clock.local)
  if (!anchor) return nowMs - d.getTime()
  return (anchor.getTime() - d.getTime()) + (nowMs - clock.atMs)
}

// Relative "time ago" for an age in ms (pair with serverAgeMs so the age is
// timezone-safe). Negative ages only ever mean clock skew — render the
// closest honest label.
export function fmtRelativeAge(ms) {
  if (ms == null) return '-'
  if (ms < 0) return 'just now'
  const sec = Math.floor(ms / 1000)
  if (sec < 60) return `${sec}s ago`
  const min = Math.floor(sec / 60)
  if (min < 60) return `${min}m ago`
  const hr = Math.floor(min / 60)
  if (hr < 24) return `${hr}h ago`
  const day = Math.floor(hr / 24)
  if (day < 30) return `${day}d ago`
  const mo = Math.floor(day / 30)
  if (mo < 12) return `${mo}mo ago`
  return `${Math.floor(mo / 12)}y ago`
}

export function isActiveSession(row, nowMs = Date.now(), ageMs = null) {
  if (!row) return false
  if (row.status === 'active') return true
  if (row.status === 'ended') return false
  const d = parseLocalIso(row.last_seen)
  if (!d) return false
  const age = ageMs != null ? ageMs : nowMs - d.getTime()
  return age >= 0 && age < STALE_FALLBACK_WINDOW_MS
}

// The active rule with its recency fallback aged against a server clock —
// the one composition every list surface should use when it has an envelope
// clock in hand.
export function isActiveWithClock(row, clock) {
  return isActiveSession(row, Date.now(), serverAgeMs(row?.last_seen, clock))
}
