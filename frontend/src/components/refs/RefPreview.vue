<script setup>
// The `@` menu's preview pane: the highlighted row's full title, where it
// belongs, and the opening of its body — enough to tell two "verify" steps
// apart before one is written into the body.
//
// It is read-only inside a menu that never takes focus: a link in the body
// would navigate away from a half-typed draft, so a click on one does nothing.
import { computed } from 'vue'
import Icon from '../ui/Icon.vue'
import MarkdownContent from '../MarkdownContent.vue'
import { KIND_LABELS } from '../../composables/useRefMentions.js'
import { REF_GLYPHS, shortKey } from './refDisplay.js'

const props = defineProps({
  // The highlighted row: `{ type, kind, item? }`, or null.
  row: { type: Object, default: null },
  // `useRefPreview`'s state for that row.
  preview: { type: Object, default: null },
  // The selected project's id: another project's row names its own.
  project: { type: Number, default: null },
})

const answer = computed(() => (props.preview?.ref === props.row?.item?.ref ? props.preview : null))
const item = computed(() => answer.value?.data?.item ?? props.row?.item ?? null)

const placement = computed(() => {
  const it = item.value
  if (!it) return []
  const parts = []
  if (it.project && it.project_id !== props.project) parts.push(it.project)
  if (it.feature_title) parts.push(`F${it.feature_id} ${it.feature_title}`)
  return parts
})

const notice = computed(() => {
  const a = answer.value
  if (!a || a.loading) return 'Loading preview…'
  if (a.failed) return 'Preview unavailable'
  if (!a.data?.item) return 'Not found'
  if (a.data.item.restricted) return 'Sessions are visible to admins only'
  if (!a.data.body) return `This ${KIND_LABELS[a.data.item.kind].toLowerCase()} has no body yet`
  return ''
})

function swallowLinks(event) {
  if (event.target instanceof Element && event.target.closest('a')) event.preventDefault()
}
</script>

<template>
  <aside class="ref-preview" aria-live="polite" data-testid="ref-preview" @click="swallowLinks">
    <p v-if="!row" class="ref-preview-note">Highlight a row to preview it</p>
    <template v-else-if="row.type === 'kind'">
      <p class="ref-preview-head">
        <Icon :name="REF_GLYPHS[row.kind]" :size="13" />
        <span>{{ KIND_LABELS[row.kind] }}</span>
      </p>
      <p class="ref-preview-note">
        Search {{ KIND_LABELS[row.kind].toLowerCase() }}s only — same as typing
        <code>@{{ row.kind }}:</code>
      </p>
    </template>
    <template v-else-if="item">
      <p class="ref-preview-head">
        <Icon :name="REF_GLYPHS[item.kind]" :size="13" />
        <span class="ref-preview-key">{{ item.kind === 'file' ? KIND_LABELS.file : shortKey(item) }}</span>
        <span v-if="item.status" class="ref-preview-status">{{ item.status }}</span>
      </p>
      <h3 class="ref-preview-title" data-testid="ref-preview-title">{{ item.title || shortKey(item) }}</h3>
      <p v-if="placement.length" class="ref-preview-meta" data-testid="ref-preview-meta">
        <span v-for="part in placement" :key="part">{{ part }}</span>
      </p>
      <p v-if="notice" class="ref-preview-note" data-testid="ref-preview-note">{{ notice }}</p>
      <div v-else class="ref-preview-body" data-testid="ref-preview-body">
        <MarkdownContent v-if="answer.data.format === 'markdown'" :markdown="answer.data.body" />
        <pre v-else-if="answer.data.format === 'code'" class="ref-preview-code">{{ answer.data.body }}</pre>
        <p v-else class="ref-preview-text">{{ answer.data.body }}</p>
        <p v-if="answer.data.truncated" class="ref-preview-note">… open it to read the rest</p>
      </div>
    </template>
  </aside>
</template>

<style scoped>
.ref-preview {
    display: flex;
    flex-direction: column;
    gap: 0.375rem;
    min-width: 0;
    min-height: 0;
    overflow-y: auto;
    overscroll-behavior: contain;
    padding: 0.625rem 0.75rem;
    border-left: 1px solid var(--color-border);
    background: var(--color-background);
}
.ref-preview-head {
    display: flex;
    align-items: center;
    gap: 0.375rem;
    font-size: 0.6875rem;
    color: var(--color-fg-subtle);
}
.ref-preview-key { font-family: var(--font-mono, ui-monospace, monospace); }
.ref-preview-status {
    padding: 0 0.375rem;
    border: 1px solid var(--color-border);
    border-radius: 999px;
    color: var(--color-fg-muted);
}
.ref-preview-title {
    margin: 0;
    font-size: 0.875rem;
    font-weight: 600;
    line-height: 1.35;
    overflow-wrap: anywhere;
    color: var(--color-fg);
}
.ref-preview-meta {
    display: flex;
    flex-wrap: wrap;
    gap: 0.25rem 0.75rem;
    font-size: 0.6875rem;
    color: var(--color-fg-muted);
    overflow-wrap: anywhere;
}
.ref-preview-note {
    font-size: 0.75rem;
    color: var(--color-fg-muted);
}
.ref-preview-body {
    min-width: 0;
    padding-top: 0.375rem;
    border-top: 1px solid var(--color-border);
    font-size: 0.75rem;
    line-height: 1.5;
    overflow-wrap: anywhere;
}
.ref-preview-body :deep(a) { cursor: default; }
.ref-preview-body :deep(h1),
.ref-preview-body :deep(h2),
.ref-preview-body :deep(h3) { font-size: 0.8125rem; margin: 0.5rem 0 0.25rem; }
.ref-preview-body :deep(pre),
.ref-preview-code {
    margin: 0;
    font-size: 0.6875rem;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
}
.ref-preview-code { font-family: var(--font-mono, ui-monospace, monospace); }
.ref-preview-text { white-space: pre-wrap; }
</style>
