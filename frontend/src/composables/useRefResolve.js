// The live items behind a body's references (D4): one `GET /api/refs/resolve`
// per 100 distinct references, asked when the set of references changes and
// never on a timer (C4, I8). A body that cites nothing asks nothing.
//
// Keyed on the set rather than on the text: the ledger re-seats its body on
// every pipeline event and an image binding rewrites the markdown it renders,
// and neither changes what the chips name.
import { shallowRef, toValue, watch } from 'vue'
import api from '../api'

export const RESOLVE_BATCH = 100

function batches(refs) {
  const out = []
  for (let i = 0; i < refs.length; i += RESOLVE_BATCH) out.push(refs.slice(i, i + RESOLVE_BATCH))
  return out
}

// A failed request answers nothing: its chips keep the label the body stored,
// which is what they read before any answer, rather than claiming "not found".
async function ask(refs, project) {
  const params = new URLSearchParams()
  for (const ref of refs) params.append('ref', ref)
  if (project !== null && project !== undefined) params.set('project_id', String(project))
  try {
    const res = await api.get(`/refs/resolve?${params}`)
    return res?.refs ?? {}
  } catch {
    return {}
  }
}

/**
 * `{ items }` — ref → `RefItem`, or null when the target does not exist; a ref
 * still in flight is absent. `refs` is a getter of `kind:key` strings,
 * duplicates allowed; `project` a getter of the project whose repo `file:`
 * references are looked up in.
 */
export function useRefResolve(refs, project = () => null) {
  const items = shallowRef({})
  let seq = 0

  const wanted = () => [...new Set(toValue(refs))]

  async function resolve() {
    const mine = ++seq
    const asked = wanted()
    if (!asked.length) return
    const scope = toValue(project)
    const answers = await Promise.all(batches(asked).map((chunk) => ask(chunk, scope)))
    if (mine !== seq) return
    items.value = Object.assign({}, items.value, ...answers)
  }

  // Refs hold no whitespace, so a newline-joined list is an exact key.
  watch(() => `${toValue(project) ?? ''}\n${wanted().join('\n')}`, resolve, { immediate: true })

  return { items }
}
