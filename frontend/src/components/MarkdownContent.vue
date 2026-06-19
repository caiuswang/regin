<script setup>
import { computed, ref, watch, nextTick, onMounted } from 'vue'
import { marked } from 'marked'

const props = defineProps({
  markdown: { type: String, default: '' },
  concealedTexts: { type: Array, default: () => [] },
})

const html = computed(() => marked(props.markdown || '', { breaks: false }))
const container = ref(null)

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

function resetAndConceal() {
  if (container.value) container.value.innerHTML = html.value
  nextTick(applyConceal)
}

onMounted(() => nextTick(applyConceal))
watch(html, () => nextTick(applyConceal))
watch(() => props.concealedTexts, resetAndConceal)
</script>

<template>
  <div ref="container" class="pattern-content" v-html="html"></div>
</template>

<style scoped>
.conceal-arrow { display:inline-block; width:1em; font-size:0.6em; color:var(--color-gray-400); vertical-align:middle; transition:transform 0.15s; margin-right:4px; }
:deep(details.concealed-section > summary) { list-style:none; }
:deep(details.concealed-section > summary::-webkit-details-marker) { display:none; }
</style>
