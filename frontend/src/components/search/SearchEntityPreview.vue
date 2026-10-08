<script setup>
// The preview for a process entity — feature, task, defect or session (R5).
// It renders whichever node the walk is focused on; the walk itself (the
// stack, the picked relation, what ↵ does) is owned by the palette, because
// every key that drives it is trapped there.
import { computed } from 'vue'
import Button from '../ui/Button.vue'
import SearchEntityList from './SearchEntityList.vue'
import SearchEntitySession from './SearchEntitySession.vue'
import { ago } from '../../utils/pipelineFormat.js'

const props = defineProps({
  stack: { type: Array, required: true },
  pickKey: { type: String, default: null },
  actions: { type: Object, default: null },
  copied: { type: Boolean, default: false },
})
const emit = defineEmits(['follow', 'pop-to', 'act', 'go', 'retry'])

const node = computed(() => props.stack[props.stack.length - 1])
const data = computed(() => node.value.data)
const row = computed(() => data.value?.row || null)
const pivoted = computed(() => props.stack.length > 1)

const KIND_LABEL = { feature: 'Feature', task: 'Task', defect: 'Defect', session: 'Session' }

const REL_GLYPH = {
  'Belongs to': '↑', 'Also in': '↑', 'Runs task': '↑', 'Reported in': '←', 'Fixed by': '↓',
}

function featureMeta(d) {
  const p = d.progress || {}
  const defects = `${p.open_defects ?? 0} open ${p.open_defects === 1 ? 'defect' : 'defects'}`
  return `${p.done ?? 0}/${p.tasks ?? 0} done · ${defects}`
}

function sessionMeta(s) {
  if (!s) return ''
  const parts = []
  if (s.turn != null) parts.push(`t${s.turn}`)
  if (s.context_pct != null) parts.push(`ctx ${Math.round(s.context_pct)}%`)
  if (s.last_seen) parts.push(ago(s.last_seen))
  return parts.join(' · ')
}

const meta = computed(() => {
  if (!data.value) return ''
  if (node.value.kind === 'feature') return featureMeta(data.value)
  if (node.value.kind === 'session') return sessionMeta(node.value.session)
  return row.value?.aside || ''
})

// A feature's spec is pages long; the header carries its opening paragraph
// only. A task or defect body is short enough to read whole.
const summary = computed(() => {
  const body = data.value?.body || ''
  if (node.value.kind !== 'feature') return ''
  const para = body.split(/\n\s*\n/).map(p => p.trim())
    .find(p => p && !p.startsWith('#') && !p.startsWith('```'))
  return para || ''
})

const body = computed(() => (node.value.kind === 'feature' ? '' : data.value?.body || ''))

const relations = computed(() => (data.value?.relations || []).map((rel, i) => ({
  key: `rel:${i}`,
  label: rel.rel,
  glyph: REL_GLYPH[rel.rel] || '·',
  none: rel.target?.none || '',
  ref: rel.target?.ref || '',
  title: rel.target?.title || '',
})))

const crumbs = computed(() => props.stack.map((n, i) => ({ key: `${i}:${n.ref}`, ref: n.ref, depth: i })))

const pickedRef = computed(() => node.value.ref)

function follow(key) {
  emit('follow', key)
}
</script>

<template>
  <div class="search-preview entity-preview" :data-kind="node.kind" :data-ref="node.ref">
    <div class="ent-head">
      <nav v-if="pivoted" class="ent-crumbs" aria-label="Walked">
        <span class="ent-crumbs-label">Walked</span>
        <template v-for="(c, i) in crumbs" :key="c.key">
          <span v-if="i" class="ent-crumb-sep" aria-hidden="true">›</span>
          <Button
            variant="ghost"
            size="sm"
            class="ent-crumb"
            :class="{ 'is-current': i === crumbs.length - 1 }"
            :aria-current="i === crumbs.length - 1 ? 'step' : undefined"
            @click="$emit('pop-to', c.depth)"
          >{{ c.ref }}</Button>
        </template>
      </nav>

      <div class="ent-kind">
        <span class="ent-kind-label">{{ KIND_LABEL[node.kind] }}</span>
        <span class="ent-ref">{{ node.ref }}</span>
        <span v-if="row" class="ent-status" :data-bucket="row.status?.bucket">
          <i aria-hidden="true" />{{ row.status?.word }}
        </span>
        <span v-if="meta" class="ent-meta">{{ meta }}</span>
      </div>
      <h2 class="ent-title">{{ row?.title || node.title || node.ref }}</h2>
      <p v-if="summary" class="ent-summary">{{ summary }}</p>

      <div v-if="relations.length" class="ent-relations">
        <Button
          v-for="r in relations"
          :key="r.key"
          variant="ghost"
          size="sm"
          class="ent-rel"
          :class="{ 'is-picked': r.key === pickKey, 'is-none': !!r.none }"
          :disabled="!!r.none"
          :data-pick="r.none ? undefined : r.key"
          @click="follow(r.key)"
        >
          <span class="ent-rel-label"><span aria-hidden="true">{{ r.glyph }}</span> {{ r.label }}</span>
          <template v-if="r.none"><span class="ent-rel-none">– {{ r.none }}</span></template>
          <template v-else>
            <span class="ent-rel-ref">{{ r.ref }}</span>
            <span class="ent-rel-title">{{ r.title }}</span>
          </template>
          <kbd v-if="r.key === pickKey">⌥→</kbd>
        </Button>
      </div>
    </div>

    <div class="ent-body content-scroll">
      <p v-if="node.status === 'loading'" class="ent-note">Loading {{ node.ref }}…</p>
      <div v-else-if="node.status === 'failed'" class="ent-note">
        Couldn't load {{ node.ref }} ·
        <Button variant="link" size="sm" @click="$emit('retry')">retry</Button>
      </div>
      <template v-else>
        <SearchEntitySession v-if="node.kind === 'session'" :node="node" @go="h => $emit('go', h)" />
        <SearchEntityList
          v-for="(list, li) in data.lists"
          :key="list.label"
          :list="list"
          :index="li"
          :pick-key="pickKey"
          @follow="follow"
        />
        <p v-if="body" class="ent-text">{{ body }}</p>
        <section v-if="data.missing_check" aria-label="Missing check">
          <h3 class="ent-kicker">Missing check</h3>
          <p class="ent-text">{{ data.missing_check }}</p>
        </section>
      </template>
    </div>

    <p v-if="pivoted" class="ent-pivot-note" data-testid="pivot-note">
      ↵ acts on {{ pickedRef }} · not the highlighted row
    </p>
    <div v-if="actions" class="ent-actions" role="group" aria-label="Actions">
      <Button variant="primary" size="sm" data-action="primary" @click="$emit('act', 'primary')">
        {{ actions.primary.label }}<kbd>↵</kbd>
      </Button>
      <Button
        size="sm"
        data-action="secondary"
        :disabled="actions.secondary.disabled"
        @click="$emit('act', 'secondary')"
      >
        {{ actions.secondary.label }}<kbd>⌘↵</kbd>
      </Button>
      <Button size="sm" data-action="copy" @click="$emit('act', 'copy')">
        {{ copied ? 'Copied' : actions.copy.label }}
      </Button>
    </div>
  </div>
</template>

<style scoped>
.entity-preview {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  background: var(--color-background);
}

.ent-head {
  flex-shrink: 0;
  padding: 0.875rem 1.375rem 0.875rem;
  border-bottom: 1px solid var(--color-border);
  background: var(--color-surface);
}

.ent-crumbs {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.3125rem;
  margin-bottom: 0.5rem;
}

.ent-crumbs-label,
.ent-kind-label {
  font-size: var(--sp-t-micro);
  line-height: var(--sp-lh-micro);
  font-weight: 700;
  letter-spacing: 0.085em;
  text-transform: uppercase;
  color: var(--color-fg-muted);
}

.ent-crumbs-label { margin-right: 0.25rem; }

.ent-crumb {
  height: auto;
  min-height: 0;
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: var(--sp-t-micro);
  font-weight: 600;
  color: var(--color-fg-muted);
  background: var(--color-surface-2);
  border: 1px solid var(--color-border);
  border-radius: 5px;
  padding: 1px 6px;
  cursor: pointer;
}

.ent-crumb.is-current { color: var(--color-fg); border-color: var(--color-fg-muted); background: var(--color-surface); }

.ent-crumb-sep { color: var(--color-fg-faint); font-size: var(--sp-t-micro); }

.ent-kind { display: flex; align-items: center; flex-wrap: wrap; gap: 0.5rem; }

.ent-ref {
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: var(--sp-t-meta);
  font-weight: 700;
  color: var(--color-fg);
}

.ent-status {
  display: inline-flex;
  align-items: center;
  gap: 0.3125rem;
  font-size: var(--sp-t-micro);
  font-weight: 650;
  color: var(--color-fg-muted);
}

.ent-status i { width: 7px; height: 7px; border-radius: 999px; background: var(--color-fg-faint); }
.ent-status[data-bucket='needs_you'] i { background: var(--color-warning); }
.ent-status[data-bucket='active'] i { background: var(--color-success); }
.ent-status[data-bucket='done'] i { background: var(--color-info); }

.ent-meta {
  margin-left: auto;
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: var(--sp-t-micro);
  color: var(--color-fg-subtle);
  white-space: nowrap;
}

.ent-title {
  margin: 0.4375rem 0 0;
  font-size: var(--sp-t-display);
  line-height: var(--sp-lh-display);
  font-weight: 650;
  letter-spacing: -0.015em;
  color: var(--color-fg);
  overflow-wrap: anywhere;
}

.ent-summary {
  margin: 0.4375rem 0 0;
  font-size: var(--sp-t-body);
  line-height: var(--sp-lh-body);
  color: var(--color-fg-muted);
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.ent-relations { display: grid; gap: 2px; margin-top: 0.625rem; }

.ent-rel {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: 0.5rem;
  height: auto;
  min-width: 0;
  padding: 0.3125rem 0.5rem;
  border: 1px solid transparent;
  border-radius: 8px;
  font-size: var(--sp-t-body);
  color: var(--color-fg);
  text-align: left;
}

.ent-rel.is-picked {
  border-color: var(--color-border-strong);
  background: var(--color-surface);
  box-shadow: 0 1px 2px rgb(0 0 0 / 0.06);
}

.ent-rel.is-none { opacity: 1; cursor: default; }

.ent-rel-label {
  flex-shrink: 0;
  width: 7.5rem;
  font-size: var(--sp-t-kbd);
  font-weight: 700;
  letter-spacing: 0.085em;
  text-transform: uppercase;
  color: var(--color-fg-subtle);
}

.ent-rel-ref {
  flex-shrink: 0;
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: 0.93em;
  font-weight: 600;
}

.ent-rel-title { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.ent-rel-none { color: var(--color-fg-subtle); }

.ent-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 0.875rem 1.375rem 1.25rem;
}

.ent-note { margin: 0; font-size: var(--sp-t-meta); color: var(--color-fg-subtle); }

.ent-kicker {
  display: flex;
  align-items: center;
  gap: 0.4375rem;
  margin: 0.875rem 0 0.375rem;
  font-size: var(--sp-t-micro);
  line-height: var(--sp-lh-micro);
  font-weight: 700;
  letter-spacing: 0.085em;
  text-transform: uppercase;
  color: var(--color-fg-muted);
}

.ent-kicker::after { content: ''; flex: 1; height: 1px; background: var(--color-border); }

.ent-text {
  margin: 0;
  font-size: var(--sp-t-body);
  line-height: var(--sp-lh-body);
  color: var(--color-fg);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.ent-pivot-note {
  flex-shrink: 0;
  margin: 0;
  padding: 0.4375rem 1.375rem;
  border-top: 1px solid var(--color-border);
  background: var(--color-surface);
  font-size: var(--sp-t-kbd);
  font-weight: 700;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--color-fg-muted);
}

.ent-actions {
  flex-shrink: 0;
  display: flex;
  gap: 0.5rem;
  padding: 0.6875rem 1.375rem;
  border-top: 1px solid var(--color-border);
  background: var(--color-surface);
}

kbd {
  flex-shrink: 0;
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: var(--sp-t-kbd);
  background: var(--color-surface-2);
  border: 1px solid var(--color-border);
  color: var(--color-fg-muted);
  padding: 1px 5px;
  border-radius: 4px;
}

.ent-actions [data-action='primary'] kbd {
  background: rgb(255 255 255 / 0.2);
  border-color: transparent;
  color: inherit;
}
</style>
