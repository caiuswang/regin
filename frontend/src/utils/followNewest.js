/**
 * Wrap a latest-wins read so a superseded call resolves with the call that
 * superseded it. The read itself still drops a stale answer; what changes is
 * the promise: `await load()` then means "the newest read asked for has
 * landed", which a caller inspecting the rendered rows depends on.
 *
 * @template {(...args: any[]) => Promise<void>} F
 * @param {F} read  never rejects (the reads catch into their own `error`)
 * @returns {F}
 */
export function followNewest(read) {
  let newest = null
  return function load(...args) {
    const mine = read(...args).then(() => (newest === mine ? undefined : newest))
    newest = mine
    return mine
  }
}
