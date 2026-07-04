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

export function isActiveSession(row, nowMs = Date.now()) {
  if (!row) return false
  if (row.status === 'active') return true
  if (row.status === 'ended') return false
  const d = parseLocalIso(row.last_seen)
  if (!d) return false
  const age = nowMs - d.getTime()
  return age >= 0 && age < STALE_FALLBACK_WINDOW_MS
}
