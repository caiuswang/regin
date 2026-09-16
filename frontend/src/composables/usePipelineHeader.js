import { shallowRef, computed, onScopeDispose } from 'vue'
import { useRoute } from 'vue-router'

// The Pipeline shell renders one identity — eyebrow, title, the tab strip —
// above three sibling views, and each tab's subtitle is a fact only the
// mounted child can state. Module scope rather than provide/inject for the
// same reason as `useTraceHeader`: the shell's <router-view> mounts the child
// as a SIBLING of the header, so the header is not in the child's injection
// path.
//
// The slot carries the path of the view that wrote it. The tabs are kept
// alive across a switch, so `onScopeDispose` no longer fires when a tab
// leaves — a lifecycle hook cannot say whose caption is on screen, but an
// owner can, and it stays right whatever order mount and teardown run in.
const slot = shallowRef(null)

export function usePipelineHeader(activePath) {
  return computed(() => {
    const current = slot.value
    if (!current) return null
    const path = typeof activePath === 'function' ? activePath() : activePath
    if (path != null && current.owner !== path) return null
    return current
  })
}

/**
 * Is the tab this view was mounted for the one on screen?
 *
 * The tabs are kept alive, so a leaving view is deactivated, never unmounted
 * — and a `<Teleport>`'s content survives deactivation in place. Its own path
 * is what tells a view to stop teleporting; a lifecycle hook cannot, because
 * the hook that would fire never does.
 */
export function usePipelineTabActive() {
  const route = useRoute()
  const owner = route.path
  return computed(() => route.path === owner)
}

/**
 * Called by the child view. Patches merge, so a view can republish without
 * restating what it published before — but a patch from a different view
 * replaces the slot rather than merging into its predecessor's.
 */
export function usePipelineHeaderPublisher() {
  const route = useRoute()
  const owner = route.path

  onScopeDispose(() => {
    if (slot.value?.owner === owner) slot.value = null
  })

  return (patch) => {
    const base = slot.value?.owner === owner ? slot.value : { owner }
    slot.value = { ...base, ...patch, owner }
  }
}
