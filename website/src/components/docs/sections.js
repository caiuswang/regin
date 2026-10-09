import { inject, onBeforeUnmount, provide, shallowRef, watchEffect } from 'vue'
import { HEADER_OFFSET, scrollBehaviour } from '../../router.js'

const KEY = Symbol('doc-sections')

// The page card renders STAGE SELECT for the view rendered inside it, so the
// view hands its h2 sections up. Owner-tagged because on a route change the
// next view can register before the previous one has unmounted.
export function provideDocSections() {
  const sections = shallowRef([])
  let owner = null
  provide(KEY, {
    set(by, list) { owner = by; sections.value = list },
    clear(by) { if (owner === by) { owner = null; sections.value = [] } },
  })
  return sections
}

export function useDocSections(getList) {
  const registry = inject(KEY, null)
  if (!registry) return
  const me = Symbol('view')
  watchEffect(() => registry.set(me, getList()))
  onBeforeUnmount(() => registry.clear(me))
}

export function sectionNumber(i) {
  return String(i + 1).padStart(2, '0')
}

export function jumpTo(id) {
  const el = document.getElementById(id)
  if (!el) return
  const top = el.getBoundingClientRect().top + window.scrollY - HEADER_OFFSET
  window.scrollTo({ top, behavior: scrollBehaviour() })
}
