<script setup>
import { computed, h, ref, render, watch, nextTick, onBeforeUnmount, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { marked } from 'marked'
import RefChip from './refs/RefChip.vue'
import { useRefResolve } from '../composables/useRefResolve.js'
import { parseRefs } from '../utils/refs.js'
import { followChip, mountPoints, withSlots } from '../utils/refRender.js'

const props = defineProps({
  markdown: { type: String, default: '' },
  concealedTexts: { type: Array, default: () => [] },
  // Opt-in (D11): only the spec and task body surfaces draw references as
  // chips. The other consumers, streaming conversation cards among them, keep
  // plain links and never ask the server to resolve anything.
  refs: { type: Boolean, default: false },
  // The project whose repo a `file:` reference is looked up in.
  project: { type: Number, default: null },
})

const source = computed(() => props.markdown || '')
const found = computed(() => (props.refs ? parseRefs(source.value) : []))
const slotTag = Math.random().toString(36).slice(2, 8)
const html = computed(() => marked(
  props.refs ? withSlots(source.value, found.value, slotTag) : source.value,
  { breaks: false }))
const container = ref(null)
const router = useRouter()
const { items } = useRefResolve(
  () => found.value.map((r) => r.ref),
  // Only a file reference is looked up per project; a body without one asks
  // without it.
  () => (found.value.some((r) => r.kind === 'file') ? props.project : null))
let chips = []

function paintChips() {
  for (const { host, at } of chips) {
    const r = found.value[at]
    if (r) render(h(RefChip, { target: r.ref, label: r.label, item: items.value[r.ref] }), host)
  }
}

function dropChips() {
  for (const { host } of chips) render(null, host)
  chips = []
}

function drawChips() {
  dropChips()
  if (!props.refs || !container.value) return
  chips = mountPoints(container.value, found.value, source.value, slotTag)
  paintChips()
}

function onClick(event) {
  if (props.refs) followChip(event, (to) => router.push(to))
}

function applyConceal() {
  if (!container.value || !props.concealedTexts.length) return
  const concealedSet = {}
  props.concealedTexts.forEach(t => { concealedSet[t.toLowerCase()] = true })

  const headings = container.value.querySelectorAll('h2, h3')
  headings.forEach(h => {
    if (!concealedSet[h.textContent.trim().toLowerCase()]) return
    const level = parseInt(h.tagName[1])
    const content = []
    let next = h.nextElementSibling
    while (next) {
      const m = next.tagName && next.tagName.match(/^H(\d)$/)
      if (m && parseInt(m[1]) <= level) break
      content.push(next)
      next = next.nextElementSibling
    }
    const details = document.createElement('details')
    details.className = 'concealed-section'
    details.style.marginBottom = '1em'
    const summary = document.createElement('summary')
    summary.style.cursor = 'pointer'
    summary.style.listStyle = 'none'
    const wrapper = document.createElement(h.tagName)
    wrapper.innerHTML =
      '<span class="conceal-arrow">\u25B6</span>' +
      '<span style="opacity:0.4">' + h.innerHTML + '</span>' +
      '<span style="font-size:0.6em;vertical-align:middle;background:var(--color-amber-400);color:var(--color-amber-900);padding:1px 6px;border-radius:4px;font-weight:500;margin-left:8px">concealed</span>'
    details.addEventListener('toggle', function () {
      const arrow = this.querySelector('.conceal-arrow')
      arrow.textContent = this.open ? '\u25BC' : '\u25B6'
    })
    summary.appendChild(wrapper)
    details.appendChild(summary)
    const body = document.createElement('div')
    body.style.opacity = '0.5'
    content.forEach(el => body.appendChild(el))
    details.appendChild(body)
    h.parentNode.replaceChild(details, h)
  })
}

// Concealing first: it rebuilds headings from their HTML, so a chip mounted
// before it would be left behind in the copy. A body with chips starts over
// from its HTML every time, because the last pass already replaced the
// placeholders the next one looks for.
function settle() {
  if (props.refs && container.value) container.value.innerHTML = html.value
  applyConceal()
  drawChips()
}

function resetAndConceal() {
  if (container.value) container.value.innerHTML = html.value
  nextTick(settle)
}

// Bound here rather than in the template: the body is no clickable surface.
// The chips inside it are the links, each with its own hover and focus.
onMounted(() => {
  container.value?.addEventListener('click', onClick)
  nextTick(settle)
})
// With chips, the text rather than the HTML: an edit to a label alone leaves
// the placeholders, and so `html`, exactly as they were.
watch(() => (props.refs ? source.value : html.value), () => nextTick(settle))
watch(() => props.concealedTexts, resetAndConceal)
watch(items, paintChips)
onBeforeUnmount(dropChips)
</script>

<template>
  <div ref="container" class="pattern-content" v-html="html"></div>
</template>

<style scoped>
.conceal-arrow { display:inline-block; width:1em; font-size:0.6em; color:var(--color-gray-400); vertical-align:middle; transition:transform 0.15s; margin-right:4px; }
:deep(details.concealed-section > summary) { list-style:none; }
:deep(details.concealed-section > summary::-webkit-details-marker) { display:none; }
</style>
