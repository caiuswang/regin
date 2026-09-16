// Shared "what is this session doing NOW?" rule — ONE frontend source for
// every status chip, the delete-warning, and the /live poll cadence
// (SessionsView, SessionListRow, SessionCard, SessionTraceHeader,
// LiveSessionPicker, useLiveTail). Mirrors `_session_active_clause` in
// web/blueprints/trace/sessions.py, which the `active=` filter and the
// `active_count` facet share — the two must move together or a badge
// disagrees with the count above it.
//
// Deliberately NOT unified with the poll-retirement checks in
// SessionTraceView or `_session_has_ended` server-side: those ask "is it safe
// to stop / to drop a queued steer", which must stay conservative, and they
// are a different question from what to render.
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
  // A zero/absent atMs would make the (nowMs - atMs) term the epoch itself.
  if (!clock || !clock.local || !clock.atMs) return nowMs - d.getTime()
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

// A session that never fired SessionEnd (crash, SIGKILL, a worktree deleted
// under it) keeps status='active' forever with no server heartbeat to correct
// it, so the marker alone cannot answer "what is this doing NOW" — the recency
// check applies to a claimed-active row too, not just to an unmarked one.
// 'closed' distinguishes an operator's manual close from a lifecycle end.
export function sessionLiveness(row, nowMs = Date.now(), ageMs = null) {
  if (!row) return 'idle'
  if (row.status === 'ended') {
    return row.ended_reason === 'manual' ? 'closed' : 'ended'
  }
  // parseLocalIso hands back an Invalid Date (truthy!) for a non-matching
  // string, so the guard has to be on the timestamp, not on the object.
  const seen = parseLocalIso(row.last_seen)
  const seenMs = seen ? seen.getTime() : NaN
  // No readable last_seen is not evidence of death: fall back to the marker.
  if (!Number.isFinite(seenMs)) return row.status === 'active' ? 'active' : 'idle'
  const age = ageMs != null ? ageMs : nowMs - seenMs
  // A NEGATIVE age is clock skew, never death — fmtRelativeAge below already
  // reads one as "just now", and the server's `last_seen >= cutoff` counts it
  // active. Flooring it at 0 here would render "idle · just now" and put the
  // badge at odds with the active_count above it.
  return Number.isFinite(age) && age < STALE_FALLBACK_WINDOW_MS ? 'active' : 'idle'
}

export function isActiveSession(row, nowMs = Date.now(), ageMs = null) {
  return sessionLiveness(row, nowMs, ageMs) === 'active'
}

// The liveness rule aged against a server clock — the composition every
// surface that renders a status chip should use.
export function sessionLivenessWithClock(row, clock) {
  return sessionLiveness(row, Date.now(), serverAgeMs(row?.last_seen, clock))
}
