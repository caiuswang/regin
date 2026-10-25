import { onBeforeRouteLeave, onBeforeRouteUpdate } from 'vue-router'
import { useConfirm } from './useConfirm'

// An edited draft is not thrown away without asking: Cancel and navigating off
// the page both confirm while the draft differs from what was opened. A jump
// to another anchor on the same page is not leaving it. Closing the tab is
// deliberately not intercepted.
export function useDiscardGuard(dirty) {
  const { confirm } = useConfirm()

  async function confirmDiscard() {
    if (!dirty()) return true
    return confirm('Discard changes?', 'Your edits to this text have not been saved.', true)
  }

  onBeforeRouteLeave(() => confirmDiscard())
  onBeforeRouteUpdate((to, from) => (to.path === from.path ? true : confirmDiscard()))

  return { confirmDiscard }
}
