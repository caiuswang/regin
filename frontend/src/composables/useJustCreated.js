import { inject, nextTick, provide, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useFlash } from './useFlash'

// The row a list just created, shared by the view with whichever row
// component its body renders (a list row, a next-step row, a board card, a
// feature track or card), so each marks itself `data-just-created` without
// the body knowing.
const KEY = Symbol('just-created')

/**
 * Provided by a list view. `land(id, reload)` re-reads the rows, then marks
 * the new one, and — because only the DOM knows whether a row survived
 * the filters, the grouping and the page — then looks for the mark. A row on
 * screen is scrolled to; one the list hides is offered through a flash whose
 * Show calls `reveal` to widen the list, then re-reads and marks it.
 *
 * The mark is cleared by the next route change, which is every filter, sort,
 * page and navigation on these lists — never by a timer.
 *
 * @param {object} opts
 * @param {import('vue').Ref<HTMLElement|null>} opts.root  the view's root element
 * @param {string} opts.noun  `Task` / `Feature`, for the flash
 * @param {() => Promise<void>} opts.reveal  widen the list (navigate)
 * @param {() => Promise<void>} opts.reload  re-read the rows once widened
 */
export function provideJustCreated({ root, noun, reveal, reload: reloadAfterReveal }) {
  const route = useRoute()
  const { flash } = useFlash()
  const id = ref(null)
  provide(KEY, id)
  watch(() => route.fullPath, () => { id.value = null })

  async function land(newId, reload, { offerShow = true } = {}) {
    // A reply that arrives after the reader moved the list belongs to a list
    // no longer on screen.
    const at = route.fullPath
    await reload()
    await nextTick()
    if (route.fullPath !== at || newId == null) return
    id.value = Number(newId)
    await nextTick()
    const row = root.value?.querySelector('[data-just-created]')
    if (row) {
      row.scrollIntoView({ block: 'nearest' })
      return
    }
    id.value = null
    if (!offerShow) return
    flash(`${noun} #${newId} is hidden by current filters or paging`, 'info', {
      link: { label: 'Show', action: () => show(newId) },
    })
  }

  async function show(newId) {
    await reveal()
    await nextTick()
    await land(newId, reloadAfterReveal, { offerShow: false })
  }

  return { id, land }
}

/** Read by a row component: a ref of the id just created, or null. */
export function useJustCreated() {
  return inject(KEY, ref(null))
}
