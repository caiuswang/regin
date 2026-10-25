<script>
let instances = 0
let libs = null
</script>

<script setup>
// A ```mermaid block drawn as its diagram — in the read view and as the rich
// face's preview, so what the writer previews is what the page shows.
//
// mermaid arrives as its own chunk on the first diagram. Renders overlap as
// the source changes; each carries a `renderId`, and only the latest one
// lands, so a slow render never paints over a newer one. A source mermaid
// cannot parse shows the source and mermaid's message, and writes nothing.
import { computed, onBeforeUnmount, ref, watch } from 'vue'

const props = defineProps({
  source: { type: String, default: '' },
  // The read view falls back to the source on an error; the rich face's
  // preview sits under the editable source and shows the message alone.
  showSource: { type: Boolean, default: true },
})

const theme = ref(document.documentElement.dataset.theme === 'dark' ? 'dark' : 'default')
const observer = new MutationObserver(() => {
  theme.value = document.documentElement.dataset.theme === 'dark' ? 'dark' : 'default'
})
observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
onBeforeUnmount(() => observer.disconnect())

const state = ref({ kind: 'loading' })
const empty = computed(() => !props.source.trim())
// Not `useId()`: every diagram is mounted as a root of its own (`render()` in
// the read view, a node view on the rich face), where it hands out the same
// id each time. mermaid scopes the SVG's styles and markers by this id and
// removes any element already holding it, so two diagrams must never share one.
const baseId = `mmd-${++instances}`
let renderId = 0

function load() {
  libs ??= Promise.all([import('mermaid'), import('dompurify')]).then(([m, p]) => ({
    mermaid: m.default,
    purifier: p.default(window),
  }))
  return libs
}

function clean(purifier, svg) {
  return purifier.sanitize(svg, {
    USE_PROFILES: { svg: true, svgFilters: true, html: true },
    ADD_TAGS: ['foreignObject'],
    // A flowchart's labels are HTML inside <foreignObject>; DOMPurify lets
    // HTML into SVG only at a declared integration point.
    HTML_INTEGRATION_POINTS: { foreignobject: true },
  })
}

const firstLine = (err) => String(err?.message ?? err ?? 'Unknown error').split('\n')[0]

async function draw() {
  const id = ++renderId
  if (empty.value) return
  if (state.value.kind !== 'rendered') state.value = { kind: 'loading' }
  const source = props.source
  try {
    const { mermaid, purifier } = await load()
    mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', theme: theme.value, suppressErrorRendering: true })
    await mermaid.parse(source)
    const { svg } = await mermaid.render(`${baseId}-${id}`, source)
    if (id === renderId) state.value = { kind: 'rendered', svg: clean(purifier, svg), renderId: id }
  } catch (err) {
    if (id === renderId) state.value = { kind: 'error', message: firstLine(err), source }
  }
}

watch(() => [props.source, theme.value], draw, { immediate: true })
onBeforeUnmount(() => { renderId += 1 })
</script>

<template>
  <div
    class="md-mermaid"
    :data-state="empty ? 'empty' : state.kind"
    :data-theme="theme"
    contenteditable="false"
  >
    <p v-if="empty" class="md-mermaid-note">Type a mermaid diagram</p>
    <div v-else-if="state.kind === 'rendered'" class="md-mermaid-svg" v-html="state.svg" />
    <template v-else-if="state.kind === 'error'">
      <pre v-if="showSource" class="md-mermaid-source"><code>{{ state.source }}</code></pre>
      <p class="md-mermaid-error" role="status">
        <strong>Diagram did not render</strong> — {{ state.message }}
      </p>
    </template>
    <pre v-else-if="showSource" class="md-mermaid-source" aria-busy="true"><code>{{ source }}</code></pre>
    <p v-else class="md-mermaid-note" aria-busy="true">Rendering…</p>
  </div>
</template>

<style scoped>
.md-mermaid { margin: 0 0 1rem; }
.md-mermaid-svg { display: flex; justify-content: center; overflow-x: auto; }
.md-mermaid-svg :deep(svg) { max-width: 100%; height: auto; }
.md-mermaid-note { margin: 0; font-size: 0.75rem; color: var(--color-fg-faint); }
.md-mermaid-error { margin: 0.25rem 0 0; font-size: 0.75rem; color: var(--color-danger, var(--color-red-600)); }
</style>
