<script setup>
/**
 * TopicComparisonCard — the full cross-revision comparison for ONE proposed
 * topic: wiki content, reference files, and graph metadata (aliases, edges,
 * globs, scalar fields). A proposal can hold several topics, so the compare
 * page renders one card per topic id present in either revision.
 */
import { computed } from 'vue'
import { diffTopicRefs, diffTopicMeta, hasMetaChange, formatRef, formatEdge } from '../../utils/topicDiff'
import WikiContentDiff from './WikiContentDiff.vue'
import WordDiffInline from './WordDiffInline.vue'
import AddRemoveList from './AddRemoveList.vue'

const props = defineProps({
  before: { type: Object, default: null },
  after: { type: Object, default: null },
  topicId: { type: String, required: true },
  beforeLabel: { type: String, default: 'Base' },
  afterLabel: { type: String, default: 'Compare' },
})

const label = computed(() => props.after?.label || props.before?.label || props.topicId)
const presence = computed(() => {
  if (!props.before) return { tone: 'add', text: 'added in this revision' }
  if (!props.after) return { tone: 'remove', text: 'removed in this revision' }
  return null
})

const refs = computed(() => diffTopicRefs(props.before, props.after))
const refAdds = computed(() => refs.value.adds.map(formatRef))
const refRemoves = computed(() => refs.value.removes.map(formatRef))
const refCount = computed(() => (props.after?.refs || props.before?.refs || []).length)

const meta = computed(() => diffTopicMeta(props.before, props.after))
const metaChanged = computed(() => hasMetaChange(meta.value))
</script>

<template>
  <article class="tcc" data-testid="topic-comparison-card">
    <header class="tcc__head">
      <code class="tcc__id">{{ topicId }}</code>
      <span class="tcc__label">{{ label }}</span>
      <span v-if="presence" class="tcc__badge" :class="`tcc__badge--${presence.tone}`">{{ presence.text }}</span>
    </header>

    <section class="tcc__section">
      <div class="tcc__section-label">Wiki content</div>
      <WikiContentDiff
        :before="before?.wiki || ''"
        :after="after?.wiki || ''"
        :before-label="beforeLabel"
        :after-label="afterLabel"
      />
    </section>

    <section class="tcc__section">
      <div class="tcc__section-label">Reference files</div>
      <AddRemoveList
        :adds="refAdds"
        :removes="refRemoves"
        :unchanged-note="`${refCount} reference file${refCount === 1 ? '' : 's'} — unchanged`"
      />
    </section>

    <section class="tcc__section">
      <div class="tcc__section-label">Graph metadata</div>
      <p v-if="!metaChanged" class="tcc__empty">No metadata changes.</p>
      <div v-else class="tcc__meta">
        <dl v-if="meta.scalars.length" class="tcc__scalars">
          <div v-for="(s, i) in meta.scalars" :key="'sc-' + i" class="tcc__scalar">
            <dt>{{ s.field }}</dt>
            <dd>
              <span v-if="!s.before && !s.after" class="tcc__empty-val">—</span>
              <WordDiffInline v-else :before="s.before || ''" :after="s.after || ''" />
            </dd>
          </div>
        </dl>
        <AddRemoveList title="Aliases" :adds="meta.aliases.adds" :removes="meta.aliases.removes" />
        <AddRemoveList title="Edges" :adds="meta.edges.adds.map(formatEdge)" :removes="meta.edges.removes.map(formatEdge)" />
        <AddRemoveList title="Include globs" :adds="meta.includeGlobs.adds" :removes="meta.includeGlobs.removes" />
        <AddRemoveList title="Exclude globs" :adds="meta.excludeGlobs.adds" :removes="meta.excludeGlobs.removes" />
      </div>
    </section>
  </article>
</template>

<style scoped>
.tcc {
  border: 1px solid var(--color-slate-200);
  border-radius: 0.75rem;
  background: var(--color-surface);
  overflow: hidden;
}
.tcc__head {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
  padding: 0.625rem 0.875rem;
  background: var(--color-surface-2);
  border-bottom: 1px solid var(--color-slate-200);
}
.tcc__id {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 0.75rem;
  padding: 0.0625rem 0.375rem;
  background: var(--color-surface-3);
  border-radius: 0.375rem;
  color: var(--color-slate-700);
}
.tcc__label { font-size: 0.8125rem; font-weight: 600; color: var(--color-slate-900); }
.tcc__badge {
  font-size: 0.6875rem;
  font-weight: 600;
  padding: 0.0625rem 0.5rem;
  border-radius: 999px;
}
.tcc__badge--add { background: var(--color-emerald-50); color: var(--color-emerald-700); }
.tcc__badge--remove { background: var(--color-red-50); color: var(--color-red-700); }

.tcc__section {
  padding: 0.875rem;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  border-top: 1px solid var(--color-slate-100);
}
.tcc__section:first-of-type { border-top: none; }
.tcc__section-label {
  font-size: 0.6875rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--color-slate-500);
}
.tcc__empty { margin: 0; font-size: 0.75rem; font-style: italic; color: var(--color-slate-400); }
.tcc__meta { display: flex; flex-direction: column; gap: 0.75rem; }

.tcc__scalars { display: flex; flex-direction: column; gap: 0.375rem; margin: 0; }
.tcc__scalar { display: grid; grid-template-columns: 6rem 1fr; gap: 0.5rem; align-items: baseline; font-size: 0.75rem; }
.tcc__scalar dt { font-weight: 500; color: var(--color-slate-500); text-transform: capitalize; }
.tcc__scalar dd { margin: 0; display: flex; align-items: center; gap: 0.375rem; flex-wrap: wrap; }
.tcc__empty-val { color: var(--color-slate-300); }
</style>
