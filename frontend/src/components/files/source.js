// A file is addressed by its source's query parameters, the same ones
// /api/files/* takes and the /files URL carries (I8): `project=&path=[&rev=]`,
// `session=&path=[&rev=]` or `session=&span=`. Keeping one shape for the URL,
// the API and the viewer is what lets a later tree only rewrite the URL.
const SOURCE_KEYS = ['project', 'session', 'span', 'path', 'rev']

export function sourceFromQuery(query) {
  const source = {}
  for (const key of SOURCE_KEYS) {
    const value = query?.[key]
    const one = Array.isArray(value) ? value[0] : value
    if (one != null && one !== '') source[key] = String(one)
  }
  if (!source.project && !source.session) return null
  return source
}

export function sourceQueryString(source, extra = {}) {
  const params = new URLSearchParams()
  for (const key of SOURCE_KEYS) {
    if (source?.[key] != null && source[key] !== '') params.set(key, source[key])
  }
  for (const [key, value] of Object.entries(extra)) params.set(key, value)
  return params.toString()
}

export function rawUrl(source, extra) {
  return `/files/raw?${sourceQueryString(source, extra)}`
}

export function metaUrl(source) {
  return `/files/meta?${sourceQueryString(source)}`
}

// `#L10` or `#L10-L20` (GitHub's form; `#L10-20` is accepted too).
export function parseLineAnchor(hash) {
  const m = /^#?L(\d+)(?:-L?(\d+))?$/.exec(hash || '')
  if (!m) return null
  const a = Number(m[1])
  const b = m[2] ? Number(m[2]) : a
  if (a < 1 || b < 1) return null
  return { start: Math.min(a, b), end: Math.max(a, b) }
}

export function formatBytes(n) {
  if (!Number.isFinite(n)) return ''
  if (n < 1024) return `${n} B`
  const units = ['KiB', 'MiB', 'GiB', 'TiB']
  let v = n / 1024
  let i = 0
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return `${v.toFixed(v < 10 ? 1 : 0)} ${units[i]}`
}

// A span names the trace that stored it, which is not always the trace the
// feed belongs to (a subagent's rows are served under its own scope), and the
// server looks a span up by that stored trace.
export function spanPathSource(span) {
  const path = span?.attributes?.file_path
  return span?.trace_id && typeof path === 'string' && path
    ? { session: span.trace_id, path }
    : null
}

// Only a Read whose content was captured: most Reads record none, and the
// span source then has nothing to serve but a 404 the viewer reads as "this
// file does not exist". The structural map strips `content` too, so a row
// whose body has not loaded yet offers nothing rather than a wrong answer.
export function readSpanSource(span) {
  return span?.name === 'tool.Read' && span.trace_id && span.span_id
    && typeof span.attributes?.content === 'string'
    ? { session: span.trace_id, span: span.span_id }
    : null
}
