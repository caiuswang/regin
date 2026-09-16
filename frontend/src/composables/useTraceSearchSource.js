import { ref, shallowRef } from 'vue'

// The bridge between the open trace and the palette.
//
// Module-level state — the same idiom as useFocusMode / useConversationRail —
// because the palette is mounted by AppLayout while the spans and the
// open-a-span moves live in SessionTraceView, which is its *sibling* under
// <router-view>. provide/inject only reaches descendants, so a registry is
// the only way the two meet without threading a prop through the shell.
//
// Exactly one trace can be open at a time, so this holds one source and the
// view clears it on unmount; a stale source would let the palette offer to
// open spans of a session that is no longer on screen.
//
// The source is `{ traceId, spans(), openSpan(id), hydrate(id) }`. `hydrate`
// is the half `openSpan` does first — load a span the client never paginated
// in — without the reader that follows it, so the preview can READ a remote
// hit the local model has never held.
const source = shallowRef(null)
const version = ref(0)

export function registerTraceSearchSource(next) {
  source.value = next
  version.value += 1
  return () => {
    if (source.value === next) {
      source.value = null
      version.value += 1
    }
  }
}

// Resolve one span for a consumer that only has its id. Returns null when no
// trace is open, when the source cannot hydrate, or when the answer arrived
// after the trace it belonged to was replaced.
export async function resolveSearchSpan(spanId) {
  const src = source.value
  if (!src || !spanId) return null
  const find = () => (src.spans?.() || []).find(s => s.span_id === spanId) || null
  const loaded = find()
  if (loaded || !src.hydrate) return loaded
  await src.hydrate(spanId)
  return source.value === src ? find() : null
}

export function useTraceSearchSource() {
  return { source, version }
}
