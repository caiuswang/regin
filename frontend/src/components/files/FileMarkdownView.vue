<script setup>
// Rendered markdown: marked → DOMPurify → v-html (I2). Relative references
// stay inside the file's root: an image is fetched as a blob through the same
// source (with the auth header an <img src> cannot send), and a link navigates
// the viewer instead of the SPA. The notebook view passes `text` for its
// markdown cells, so they resolve against the notebook's own directory.
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import api from '../../api'
import { rawUrl } from './source'
import { resolveRelative, sanitizeMarkdown, splitFrontmatter } from './render'

const props = defineProps({
  meta: { type: Object, required: true },
  source: { type: Object, required: true },
  lines: { type: Object, default: null },
  text: { type: String, default: null },
})
const emit = defineEmits(['failed', 'navigate'])

const fetched = ref(null)
const error = ref('')
const container = ref(null)
const body = computed(() => (props.text != null ? props.text : fetched.value))
// Only a whole file has frontmatter; a notebook cell's leading `---` is a rule.
const parts = computed(() =>
  props.text != null ? { frontmatter: null, body: props.text } : splitFrontmatter(fetched.value),
)
const html = computed(() => (body.value == null ? '' : sanitizeMarkdown(parts.value.body)))

let blobUrls = []
let paint = 0
let disposed = false

function revokeAll() {
  for (const u of blobUrls) URL.revokeObjectURL(u)
  blobUrls = []
}

async function loadImage(img, id) {
  const target = resolveRelative(props.source, img.getAttribute('data-fv-src'))
  if (!target) {
    img.setAttribute('data-fv-refused', 'true')
    return
  }
  try {
    const url = await api.getBlobUrl(rawUrl(target.source))
    if (disposed || id !== paint) {
      URL.revokeObjectURL(url)
      return
    }
    blobUrls.push(url)
    img.src = url
  } catch {
    img.setAttribute('data-fv-refused', 'true')
  }
}

async function paintImages() {
  const id = ++paint
  revokeAll()
  await nextTick()
  if (!container.value) return
  for (const img of container.value.querySelectorAll('img[data-fv-src]')) loadImage(img, id)
}

function openImage(img) {
  const ref = img.getAttribute('data-fv-src')
  if (ref) {
    const target = resolveRelative(props.source, ref)
    if (target) emit('navigate', target.source, '')
  } else if (/^https?:/i.test(img.getAttribute('src') || '')) {
    window.open(img.getAttribute('src'), '_blank', 'noopener,noreferrer')
  }
}

function onClick(event) {
  const el = event.target instanceof Element ? event.target : null
  const a = el?.closest('a')
  if (a) {
    if (!a.hasAttribute('data-fv-href')) return
    event.preventDefault()
    const target = resolveRelative(props.source, a.getAttribute('data-fv-href'))
    if (target) emit('navigate', target.source, target.anchor)
  } else if (el?.tagName === 'IMG' && !el.hasAttribute('data-fv-refused')) {
    openImage(el)
  }
}

onMounted(async () => {
  if (props.text != null) return
  try {
    fetched.value = await api.getText(rawUrl(props.source))
  } catch (err) {
    error.value = err.message || 'The file could not be loaded.'
    emit('failed', error.value)
  }
})

watch(html, paintImages, { immediate: true })
// Delegated, so links drawn by v-html need no per-node binding.
watch(container, (el, old) => {
  old?.removeEventListener('click', onClick)
  el?.addEventListener('click', onClick)
})

onBeforeUnmount(() => {
  disposed = true
  revokeAll()
})
</script>

<template>
  <div class="fmd" :class="{ 'fmd-page': text == null }" data-testid="file-markdown-view">
    <p v-if="error" class="fmd-note fmd-error" role="alert">{{ error }}</p>
    <p v-else-if="body == null" class="fmd-note" role="status">Loading…</p>
    <template v-else>
      <pre v-if="parts.frontmatter" class="fmd-frontmatter" data-testid="file-markdown-frontmatter"><code>{{ parts.frontmatter }}</code></pre>
      <!-- eslint-disable-next-line vue/no-v-html -- sanitized by DOMPurify in sanitizeMarkdown -->
      <div ref="container" class="pattern-content fmd-body" v-html="html" />
    </template>
  </div>
</template>

<style scoped>
.fmd-page {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 1rem 1.5rem;
}
.fmd-note { padding: 1rem 1.25rem; font-size: 13px; color: var(--color-fg-muted); }
.fmd-error { color: var(--color-danger); }
.fmd-body :deep(img) { max-width: 100%; }
.fmd-body :deep(img:not([data-fv-refused])) { cursor: zoom-in; }
.fmd-body :deep(a img) { cursor: pointer; }
.fmd-frontmatter {
  margin: 0 0 1rem;
  padding: 0.5rem 0.75rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: var(--color-surface-2);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  color: var(--color-fg-muted);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
