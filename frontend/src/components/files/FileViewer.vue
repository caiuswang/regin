<script setup>
// One viewer, mounted by the /files page and by the preview drawer (D8).
// It fetches the meta once per source, then hands the meta to whichever
// renderer the registry names for `meta.preview` (I6) — it never looks at
// the content itself (I7). The view mode, diff layout and line anchor are the
// mount's state, not the viewer's: /files keeps them in its URL (I8).
import { computed, defineAsyncComponent, ref, watch } from 'vue'
import api from '../../api'
import FileActions from './FileActions.vue'
import { rendererFor } from './registry'
import { formatBytes, metaUrl, parseLineAnchor, sourceQueryString } from './source'

const DiffView = defineAsyncComponent(() => import('./FileDiffView.vue'))

const props = defineProps({
  source: { type: Object, default: null },
  anchor: { type: String, default: '' },
  view: { type: String, default: '' },
  layout: { type: String, default: '' },
})
const emit = defineEmits(['navigate', 'update:view', 'update:layout', 'update:anchor'])

// Axis A of the spec's lifecycle.
const STATUS_BY_HTTP = { 403: 'denied', 404: 'missing' }
const MESSAGES = {
  denied: 'You do not have access to this file, or it lies outside its root.',
  missing: 'This file does not exist.',
  failed: 'The file could not be read.',
}

const status = ref('loading')
const meta = ref(null)
const errorDetail = ref('')
const selection = ref(null)
const sourceKey = computed(() => (props.source ? sourceQueryString(props.source) : ''))
const lines = computed(() => parseLineAnchor(props.anchor))
const renderer = computed(() => (meta.value ? rendererFor(meta.value.preview) : null))
const hasRendered = computed(() => !!renderer.value?.source)

// Axis B. A requested view the file cannot take (a diff of a clean file, the
// source of a kind with none) falls back to the kind's default view.
const effectiveView = computed(() => {
  if (props.view === 'diff' && meta.value?.diffable) return 'diff'
  if (props.view === 'source' && hasRendered.value) return 'source'
  return ''
})
const active = computed(() => {
  if (effectiveView.value === 'diff') return DiffView
  if (effectiveView.value === 'source') return renderer.value.source
  return renderer.value?.component
})
const viewProps = computed(() => (effectiveView.value === 'diff' ? { layout: props.layout } : {}))

let requestId = 0

async function load() {
  const id = ++requestId
  meta.value = null
  errorDetail.value = ''
  if (!props.source) {
    status.value = 'failed'
    errorDetail.value = 'No file selected.'
    return
  }
  status.value = 'loading'
  try {
    const m = await api.get(metaUrl(props.source))
    if (id !== requestId) return
    meta.value = m
    status.value = rendererFor(m.preview).mode
  } catch (err) {
    if (id !== requestId) return
    status.value = STATUS_BY_HTTP[err.status] || 'failed'
    errorDetail.value = err.message || ''
  }
}

// The meta can be fine and the bytes still unreadable (deleted or replaced
// between the two requests): the renderer reports it, and the viewer leaves
// `ready` for `failed` like any other read error.
function onRendererFailed(detail) {
  status.value = 'failed'
  errorDetail.value = detail || ''
}

// A relative link inside a rendered file; the mount decides where it goes.
function forwardNavigate(next, nextAnchor) {
  emit('navigate', next, nextAnchor)
}

watch(sourceKey, load, { immediate: true })
watch([sourceKey, effectiveView, () => props.anchor], () => { selection.value = null })
</script>

<template>
  <section class="fv" :data-status="status" :data-view="effectiveView || 'default'" data-testid="file-viewer">
    <header v-if="meta" class="fv-head">
      <div class="fv-title">
        <span class="fv-name" :title="meta.abs_path || meta.name" data-testid="file-viewer-name">{{ meta.name }}</span>
        <span class="fv-sub">{{ formatBytes(meta.size) }} · {{ meta.mime }}</span>
        <span class="fv-spacer" />
        <slot name="actions" :meta="meta" />
      </div>
      <FileActions
        v-if="status !== 'loading'"
        :meta="meta"
        :source="source"
        :view="effectiveView"
        :layout="layout"
        :has-rendered="hasRendered"
        :lines="selection || lines"
        @view="emit('update:view', $event)"
        @layout="emit('update:layout', $event)"
        @anchor="emit('update:anchor', $event)"
        @navigate="forwardNavigate"
      />
    </header>

    <div class="fv-body">
      <p v-if="status === 'loading'" class="fv-note" role="status">Loading…</p>
      <div v-else-if="status === 'denied' || status === 'missing' || status === 'failed'"
           class="fv-note fv-error" role="alert" :data-testid="`file-viewer-${status}`">
        <p>{{ MESSAGES[status] }}</p>
        <p v-if="errorDetail" class="fv-detail">{{ errorDetail }}</p>
      </div>
      <component
        :is="active"
        v-else-if="active"
        :key="`${sourceKey}|${effectiveView}`"
        v-bind="viewProps"
        :meta="meta"
        :source="source"
        :lines="lines"
        @failed="onRendererFailed"
        @navigate="forwardNavigate"
        @select="selection = $event"
      />
    </div>
  </section>
</template>

<style scoped>
.fv {
  display: flex;
  flex-direction: column;
  min-height: 0;
  height: 100%;
  background: var(--color-surface);
  color: var(--color-fg);
}
.fv-head {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
  min-width: 0;
  padding: 0.5rem 0.85rem;
  border-bottom: 1px solid var(--color-border);
}
.fv-title {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  min-width: 0;
}
.fv-name {
  font-family: var(--font-mono, monospace);
  font-size: 13px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.fv-sub {
  flex-shrink: 0;
  font-size: 12px;
  color: var(--color-fg-muted);
}
.fv-spacer { flex: 1; }
.fv-body {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.fv-note {
  padding: 1.25rem;
  font-size: 13px;
  color: var(--color-fg-muted);
}
.fv-error p:first-child { color: var(--color-fg); font-weight: 600; }
.fv-detail { margin-top: 0.25rem; font-family: var(--font-mono, monospace); font-size: 12px; }
</style>
