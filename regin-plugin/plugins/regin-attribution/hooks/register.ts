import type { Register } from 'claude-code'

const TRAILER = /^\s*Claude-Session:/i
const SESSION_LINK = /[ \t]*(?:(?<!\S)Claude-Session:[ \t]*|(?<!\S))https:\/\/claude\.ai\/code\/session_[\w-]+(?:[/?#][^\s.,;:)\]]*)?/gi

export function dropSessionLinks(text: string): string {
  const lines = text.split('\n')
  const kept: string[] = []
  for (const line of lines) {
    if (TRAILER.test(line)) continue
    const stripped = line.replace(SESSION_LINK, '')
    if (stripped === line) kept.push(line)
    else if (stripped.trim()) kept.push(stripped)
  }
  if (kept.length === lines.length && kept.every((line, i) => line === lines[i])) return text
  // A dropped closing line leaves the blank separator that preceded it.
  const ending = /[\r\n]*$/.exec(text)?.[0] ?? ''
  return kept.join('\n').replace(/[\r\n]+$/, '') + ending
}

export const register: Register = (on) => {
  on('attribution.text', async (_$, e, next) => {
    if (e.kind !== 'commit' && e.kind !== 'pr') return next(e)
    const { text } = await next(e)
    return { text: dropSessionLinks(text) }
  })
}
