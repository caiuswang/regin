<script setup>
// "Projects" (1a, scope all): needs attention first — escapes, then open
// defects, then activity. Quiet and completed projects collapse to one line
// each. Searching or re-sorting reads `/api/pipeline/projects`; the default
// view is the Overview's own `projects` panel.
import { computed, ref, watch } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import Input from '../ui/Input.vue'
import SegmentBar from './SegmentBar.vue'
import { projectStatusLine, stageParts } from '../../utils/pipelineFormat'
import { SCOPE_NONE } from '../../composables/usePipelineScope'

const props = defineProps({
  panel: { type: Object, required: true },
})
const emit = defineEmits(['pick', 'all'])

const ROWS = 5
const SORTS = [
  { value: 'attention', label: 'Needs attention' },
  { value: 'activity', label: 'Recent' },
  { value: 'name', label: 'A–Z' },
]

const q = ref('')
const sort = ref('attention')
const listed = ref(null)
const error = ref('')
let issued = 0

const custom = computed(() => q.value.trim() !== '' || sort.value !== 'attention')

// `/api/pipeline/projects` rows carry the full rollup; reduce them to the
// panel row's shape so one template draws both.
function toPanelRow(item) {
  return {
    id: item.id, scope: item.scope, name: item.name || (item.id == null ? 'No project' : `project #${item.id}`),
    escaped: item.features?.by_stage?.escaped || 0,
    last_activity: item.last_activity,
    stage_bar: item.features?.by_stage || {},
    features: Object.entries(item.features?.by_stage || {})
      .filter(([k]) => k !== 'abandoned').reduce((n, [, v]) => n + v, 0),
    open_tasks: ['idea', 'armed', 'queued', 'running']
      .reduce((n, k) => n + (item.tasks?.by_status?.[k] || 0), 0),
    open_defects: item.defects?.open || 0,
  }
}

async function load() {
  if (!custom.value) {
    listed.value = null
    return
  }
  const mine = ++issued
  try {
    const params = new URLSearchParams({ sort: sort.value, size: String(ROWS) })
    if (q.value.trim()) params.set('q', q.value.trim())
    const body = await api.get(`/pipeline/projects?${params}`)
    if (mine !== issued) return
    listed.value = { rows: (body.items || []).map(toPanelRow), matched: body.pagination?.total ?? 0 }
    error.value = ''
  } catch (e) {
    if (mine === issued) error.value = e?.message || String(e)
  }
}
watch([q, sort], load)

const rows = computed(() => (listed.value ? listed.value.rows : props.panel.rows))
const shown = computed(() => rows.value.length)
const of = computed(() => (listed.value ? listed.value.matched : props.panel.total))

const search = ref(null)
defineExpose({ focusSearch: () => search.value?.focus() })
</script>

<template>
  <section class="pl-panel pj" data-testid="projects-panel" aria-labelledby="pj-title">
    <header class="pl-panel__head pj-head">
      <h2 id="pj-title" class="pl-panel__title">Projects</h2>
      <span class="pl-panel__sub">Needs attention first — escapes, then open defects, then activity</span>
      <span class="pj-spacer" />
      <Input
        ref="search"
        v-model="q"
        type="search"
        class="pj-search"
        :placeholder="`Find among ${panel.total} projects`"
        aria-label="Find a project"
        data-testid="projects-search"
      />
      <div class="segmented pj-sort" role="group" aria-label="Sort projects">
        <Button
          v-for="s in SORTS"
          :key="s.value"
          variant="ghost"
          size="sm"
          class="segmented-item pj-sort__item"
          :class="{ 'is-active': sort === s.value }"
          :aria-pressed="sort === s.value"
          :data-testid="`projects-sort-${s.value}`"
          @click="sort = s.value"
        >{{ s.label }}</Button>
      </div>
    </header>
    <p v-if="error" class="pl-panel__refusal" role="alert">{{ error }}</p>
    <p v-if="!rows.length" class="pl-panel__empty" data-testid="projects-empty">
      {{ custom ? 'No project matches.' : 'Every project is quiet — nothing open anywhere.' }}
    </p>
    <ul v-else>
      <li v-for="row in rows" :key="row.scope ?? row.id" class="pj-row" :data-testid="`projects-row-${row.scope ?? row.id}`">
        <Button
          variant="ghost"
          class="pj-pick"
          :data-testid="`projects-pick-${row.scope ?? row.id}`"
          @click="emit('pick', row.id == null ? SCOPE_NONE : String(row.id))"
        >
          <span class="pj-name">{{ row.name }}</span>
          <span class="pj-status">{{ row.id == null ? 'Unscoped work' : projectStatusLine(row) }}</span>
        </Button>
        <SegmentBar class="pj-bar" :parts="stageParts(row.stage_bar)" :height="8" />
        <span class="pj-fig">{{ row.features }} features</span>
        <span class="pj-fig">{{ row.open_tasks }} open tasks</span>
        <span class="pj-fig" :class="{ 'is-warn': row.open_defects }">{{ row.open_defects }} open defects</span>
      </li>
    </ul>
    <footer class="pl-panel__foot" data-testid="projects-foot">
      <span>Showing {{ shown }} of {{ of }}</span>
      <template v-if="!custom">
        <span v-if="panel.quiet"><span class="pj-dot tone-idle" /> {{ panel.quiet }} quiet — no open work, collapsed</span>
        <span v-if="panel.completed_hidden"><span class="pj-dot tone-success" /> {{ panel.completed_hidden }} completed, hidden</span>
      </template>
      <span class="pj-spacer" />
      <span class="pj-legend">
        <span v-for="p in stageParts({})" :key="p.key"><span class="pj-dot" :class="`tone-${p.tone}`" /> {{ p.key }}</span>
      </span>
      <Button variant="link" size="sm" class="pj-all" data-testid="projects-all" @click="emit('all')">
        All projects →
      </Button>
    </footer>
  </section>
</template>

<style scoped>
.pj-head { align-items: center; }
.pj-spacer { flex: 1; }
.pj-head .pj-search { width: 13rem; height: 32px; }
.pj-sort { margin: 0; }
.pj-row {
    display: grid; align-items: center; gap: 1rem;
    grid-template-columns: minmax(8rem, 12rem) minmax(6rem, 1fr) auto auto auto;
    padding: 0.6rem 1.1rem; border-top: 1px solid var(--color-border-subtle);
}
.pj-row .pj-pick {
    display: flex; flex-direction: column; align-items: flex-start; justify-content: center; gap: 0;
    min-width: 0; height: auto; padding: 0.15rem 0.3rem; margin-left: -0.3rem; text-align: left;
}
.pj-sort .pj-sort__item { height: 28px; }
.pj-name { font-size: 13.5px; font-weight: 600; color: var(--color-fg); max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pj-pick:hover .pj-name { text-decoration: underline; }
.pj-status { font-size: 11.5px; color: var(--color-fg-muted); }
.pj-fig { font-size: 12.5px; color: var(--color-fg-muted); white-space: nowrap; text-align: right; font-variant-numeric: tabular-nums; }
.pj-fig.is-warn { color: var(--color-warning-strong); font-weight: 600; }
.pj-dot { display: inline-block; width: 7px; height: 7px; border-radius: 999px; margin-right: 0.2rem; }
.pj-legend { display: inline-flex; gap: 0.7rem; color: var(--color-fg-faint); }
.pl-panel__foot .pj-all { font-size: 12.5px; }

@media (max-width: 1023.98px) {
    .pj-row { grid-template-columns: minmax(0, 1fr) auto; }
    .pj-bar { grid-column: 1 / -1; order: 3; }
    .pj-fig:not(.is-warn) { display: none; }
    .pj-head .pj-search { width: 100%; }
    .pj-legend { display: none; }
}
</style>
