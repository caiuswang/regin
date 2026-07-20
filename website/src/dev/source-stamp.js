/**
 * Stamp each component's root element with the SFC that rendered it, as
 * `data-component="src/views/HomeView.vue"`.
 *
 * Gives the grab overlay a direct element→file mapping, so an agent reads the
 * owning file instead of grepping for a string it hopes is unique.
 *
 * `__file` is set by @vitejs/plugin-vue in dev only, and this module is
 * imported dynamically behind `import.meta.env.DEV`, so none of it reaches a
 * production bundle.
 *
 * vite-plugin-vue-inspector was evaluated for this and rejected: v7 resolves
 * locations through source maps and stamps `data-v-inspector` on only a
 * handful of fallback nodes (measured: 5 across the whole /memory page).
 */

const MAX_ROOTS = 4

/**
 * Root element(s) of a component instance.
 *
 * A single-root SFC exposes `$el` directly. A **fragment root** (multiple
 * top-level nodes, or a leading comment) sets `$el` to a placeholder text
 * node instead — that case silently skipped stamping for every view built
 * that way, so walk the render tree and take the first real element on each
 * top-level branch.
 */
function rootElements(vm) {
  const direct = vm.$el
  if (direct && direct.nodeType === 1) return [direct]

  const out = []
  const visit = (vnode) => {
    if (!vnode || out.length >= MAX_ROOTS) return
    // A Teleport renders its content elsewhere in the DOM; stamping that
    // content would attribute a modal to whatever component happened to
    // declare it.
    if (vnode.type && vnode.type.__isTeleport) return
    if (vnode.el && vnode.el.nodeType === 1) { out.push(vnode.el); return }
    if (vnode.component) { visit(vnode.component.subTree); return }
    if (Array.isArray(vnode.children)) vnode.children.forEach(visit)
  }
  visit(vm.$ && vm.$.subTree)
  return out
}

function stamp(vm) {
  const file = vm.$options.__file
  if (!file) return
  // `__file` is absolute; the repo-relative tail is what an agent can hand
  // straight to Read/Edit.
  const rel = file.replace(/^.*?\/website\//, '')
  for (const el of rootElements(vm)) {
    if (!el.hasAttribute('data-component')) el.setAttribute('data-component', rel)
  }
}

export function installSourceStamp(app) {
  // `updated` matters as much as `mounted`: a view whose root is a
  // `v-if="loading"` branch stamps its placeholder, then swaps that element
  // out when data arrives — losing the stamp. Re-stamping on every update
  // keeps the mapping alive across re-renders.
  app.mixin({
    mounted() { stamp(this) },
    updated() { stamp(this) },
  })
}
