<script setup>
// A fenced block in the read view. It shows the text at once and colours it
// when the highlighter chunk arrives, so a page without code never fetches it.
import { ref, watch } from 'vue'
import 'highlight.js/styles/github-dark.css'

const props = defineProps({
  code: { type: String, default: '' },
  language: { type: String, default: null },
})

const html = ref(null)
let asked = 0

watch(() => [props.code, props.language], async () => {
  const id = ++asked
  html.value = null
  if (!props.language) return
  const { highlightHtml, isHighlighted } = await import('../../editor/highlight.js')
  if (id !== asked || !isHighlighted(props.language)) return
  html.value = highlightHtml(props.code, props.language)
}, { immediate: true })
</script>

<template>
  <pre class="md-code" :data-language="language || null"><code
    v-if="html !== null"
    :class="['hljs', `language-${language}`]"
    v-html="html"
  /><code v-else :class="language ? `language-${language}` : null">{{ code }}</code></pre>
</template>
