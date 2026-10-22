<script setup>
// The project chip and the dropdown hanging off it (design 1d). The chip is the
// one control that carries a scope across all three tabs.
//
// Rows come from `/api/pipeline/projects?sort=attention`, so the order, the
// search match and the "Find among N" count are all the server's.
import { computed, nextTick, ref, watch } from 'vue'
import { PopoverAnchor, PopoverContent, PopoverPortal, PopoverRoot } from 'reka-ui'
import api from '../../api'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import Input from '../ui/Input.vue'
import SegmentBar from './SegmentBar.vue'
import PlGlyph from './PlGlyph.vue'
import AddProjectDialog from '../projects/AddProjectDialog.vue'
import { stageParts } from '../../utils/pipelineFormat'
import { SCOPE_ALL, SCOPE_NONE } from '../../composables/usePipelineScope'
import { MODE_REPO, canAddProjects } from '../../composables/useAddProject'

const props = defineProps({
  scope: { type: String, required: true },
  label: { type: String, required: true },
  compact: { type: Boolean, default: false },
})
const emit = defineEmits(['select'])

const open = ref(false)
const q = ref('')
const items = ref([])
const total = ref(0)
const population = ref(null)
const error = ref('')
const search = ref(null)
const active = ref(0)
const PAGE = 50
let page = 0
let issued = 0

// Paged by the server, not capped: "Load more" asks for the next page of the
// same attention sort, so project 101 is as reachable as project 1.
async function load({ more = false } = {}) {
  const mine = ++issued
  const next = more ? page + 1 : 0
  try {
    const params = new URLSearchParams({ sort: 'attention', size: String(PAGE), page: String(next) })
    if (q.value.trim()) params.set('q', q.value.trim())
    const body = await api.get(`/pipeline/projects?${params}`)
    if (mine !== issued) return
    page = next
    items.value = more ? [...items.value, ...(body.items || [])] : (body.items || [])
    total.value = body.pagination?.total ?? items.value.length
    population.value = body.population ?? null
    error.value = ''
    if (!more) active.value = 0
  } catch (e) {
    if (mine === issued) error.value = e?.message || String(e)
  }
}

const needsAttention = row => row.id != null
  && ((row.features?.by_stage?.escaped || 0) > 0 || (row.defects?.open || 0) > 0)
const groups = computed(() => [
  { label: 'Needs attention', rows: items.value.filter(needsAttention) },
  { label: 'Recent', rows: items.value.filter(r => !needsAttention(r)) },
].filter(g => g.rows.length))
const more = computed(() => Math.max(0, total.value - items.value.length))

// The keyboard walks what is drawn, in drawn order: "All projects", then each
// group's rows. Index 0 is "All projects".
const chipGlyph = computed(() => {
  if (props.scope === SCOPE_ALL) return 'portfolio'
  return props.scope === SCOPE_NONE ? 'project.none' : 'project'
})
const order = computed(() => [SCOPE_ALL, ...groups.value.flatMap(g => g.rows.map(scopeOfRow))])
function scopeOfRow(row) {
  return row.id == null ? SCOPE_NONE : String(row.id)
}
// Classic draws no per-row icon, so these glyphs have no fallback.
function rowGlyph(row) {
  if (needsAttention(row)) return 'defect.wild'
  return row.id == null ? 'project.none' : 'project'
}
const isActive = scope => order.value[active.value] === scope

async function move(delta) {
  const n = order.value.length
  if (!n) return
  active.value = (active.value + delta + n) % n
  await nextTick()
  document.querySelector('.pp-panel .pp-row.is-active')?.scrollIntoView({ block: 'nearest' })
}

function onKeydown(e) {
  if (e.key === 'ArrowDown') { e.preventDefault(); move(1) }
  else if (e.key === 'ArrowUp') { e.preventDefault(); move(-1) }
  else if (e.key === 'Enter') {
    e.preventDefault()
    const scope = order.value[active.value]
    if (scope != null) pick(scope)
  }
}

// "No project" is a bucket, not a project, so it is not one of the N.
const projectCount = computed(() => (population.value == null ? null : Math.max(0, population.value - 1)))

watch(open, async (isOpen) => {
  if (!isOpen) return
  q.value = ''
  load()
  await nextTick()
  search.value?.focus()
})
watch(q, () => load())

function pick(scope) {
  open.value = false
  emit('select', scope)
}

const selected = row => String(row.scope) === props.scope
const manageTo = computed(() => (/^\d+$/.test(props.scope) ? `/projects/${props.scope}` : null))

const canAdd = canAddProjects()
const adding = ref(false)
function startAdd() {
  open.value = false
  adding.value = true
}
// A workspace has no scope of its own yet, so adding one leaves the scope be.
function added(result) {
  if (result.kind === MODE_REPO && result.project) emit('select', String(result.project.id))
}

defineExpose({ open: () => { open.value = true } })
</script>

<template>
  <PopoverRoot v-model:open="open">
    <PopoverAnchor as-child>
      <div class="pp-chip" data-pl-part="chip" :class="{ 'is-scoped': scope !== SCOPE_ALL, 'pp-chip--compact': compact }">
        <Button
          variant="ghost"
          size="sm"
          class="pp-chip__open"
          data-testid="pipeline-project"
          :aria-expanded="open"
          aria-haspopup="dialog"
          @click="open = !open"
        >
          <PlGlyph v-if="!compact" :kind="chipGlyph"><Icon name="folder" :size="13" /></PlGlyph>
          <span class="pp-chip__label">{{ label }}</span>
          <Icon name="chevron-down" :size="12" />
        </Button>
        <Button
          v-if="scope !== SCOPE_ALL && !compact"
          variant="ghost"
          size="sm"
          class="pp-chip__clear"
          aria-label="Clear project scope"
          data-testid="pipeline-project-clear"
          @click="emit('select', SCOPE_ALL)"
        >
          <Icon name="x" :size="12" />
        </Button>
      </div>
    </PopoverAnchor>
    <PopoverPortal>
      <PopoverContent
        side="bottom"
        align="start"
        :side-offset="6"
        class="pp-panel"
        data-pl-part="picker"
        data-pl-hint="↑↓ move · Enter pick · Esc close"
        data-testid="project-picker"
        @open-auto-focus.prevent
      >
        <label class="pp-search">
          <Icon name="search" :size="13" />
          <Input
            ref="search"
            v-model="q"
            type="search"
            class="pp-search__input"
            data-pl-part="input"
            :placeholder="projectCount == null ? 'Find a project' : `Find among ${projectCount} projects`"
            aria-label="Find a project"
            data-testid="project-picker-search"
            @keydown="onKeydown"
          />
          <kbd>p</kbd>
        </label>
        <Button
          variant="ghost"
          class="pp-row pp-row--all"
          data-pl-part="picker-row"
          :class="{ 'is-selected': scope === SCOPE_ALL, 'is-active': isActive(SCOPE_ALL) }"
          :aria-current="isActive(SCOPE_ALL) ? 'true' : undefined"
          data-testid="project-picker-all"
          @click="pick(SCOPE_ALL)"
        >
          <span class="pp-check"><Icon v-if="scope === SCOPE_ALL" name="check" :size="12" /></span>
          <PlGlyph kind="portfolio"><Icon name="layout-grid" :size="13" /></PlGlyph>
          <span class="pp-name">All projects</span>
          <span class="pp-hint">portfolio view</span>
        </Button>
        <p v-if="error" class="pp-error" role="alert">{{ error }}</p>
        <div v-for="g in groups" :key="g.label" class="pp-group">
          <div class="pp-group__label" data-pl-part="picker-group">{{ g.label }}</div>
          <Button
            v-for="row in g.rows"
            :key="row.scope"
            variant="ghost"
            class="pp-row"
            data-pl-part="picker-row"
            :class="{ 'is-selected': selected(row), 'is-active': isActive(scopeOfRow(row)) }"
            :aria-current="isActive(scopeOfRow(row)) ? 'true' : undefined"
            :data-testid="`project-picker-row-${row.scope}`"
            @click="pick(scopeOfRow(row))"
          >
            <span class="pp-check"><Icon v-if="selected(row)" name="check" :size="12" /></span>
            <PlGlyph :kind="rowGlyph(row)" />
            <span class="pp-name">{{ row.name || (row.id == null ? 'No project' : `project #${row.id}`) }}</span>
            <span class="pp-bar"><SegmentBar :parts="stageParts(row.features?.by_stage)" :height="4" /></span>
            <span v-if="row.defects?.open" class="pp-open">{{ row.defects.open }} open</span>
            <span v-else class="pp-none">—</span>
          </Button>
        </div>
        <Button
          v-if="more"
          variant="ghost"
          size="sm"
          class="pp-more"
          data-testid="project-picker-more"
          @click="load({ more: true })"
        >{{ more }} more</Button>
        <p v-if="!error && !items.length && q" class="pp-empty">No project matches “{{ q }}”.</p>
        <router-link
          v-if="manageTo"
          :to="manageTo"
          class="pp-row pp-row--manage focus-visible:outline-2 focus-visible:outline-blue-500"
          data-pl-part="picker-row"
          data-testid="project-picker-manage"
          @click="open = false"
        >
          <span class="pp-check" />
          <Icon name="settings" :size="13" />
          <span class="pp-name">Project settings…</span>
        </router-link>
        <Button
          v-if="canAdd"
          variant="ghost"
          class="pp-row pp-row--add"
          data-pl-part="picker-row"
          data-testid="project-picker-add"
          @click="startAdd"
        >
          <span class="pp-check" />
          <span class="pp-name">+ Add project…</span>
        </Button>
      </PopoverContent>
    </PopoverPortal>
  </PopoverRoot>
  <AddProjectDialog v-if="canAdd" v-model:open="adding" @added="added" />
</template>

<style scoped>
.pp-chip {
    display: inline-flex; align-items: center; min-width: 0;
    border: 1px solid var(--color-border); border-radius: var(--radius-lg);
    background: var(--color-surface); color: var(--color-fg);
}
.pp-chip.is-scoped { border-color: var(--color-info); color: var(--color-info); }
.pp-chip .pp-chip__open {
    gap: 0.4rem; min-width: 0; height: 32px;
    padding: 0 0.55rem; font-size: 13px; font-weight: 500; color: inherit;
}
.pp-chip__label { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 14rem; }
.pp-chip .pp-chip__clear {
    height: 32px; padding: 0 0.45rem; color: inherit; border-radius: 0;
    border-left: 1px solid var(--color-border);
}
.pp-chip--compact { border: 0; background: none; color: var(--color-fg-muted); }
.pp-chip--compact .pp-chip__open { padding: 0; font-size: 12.5px; height: 24px; }
</style>

<!-- Not scoped: Reka portals the panel to <body>, beyond scoped data-v. -->
<style>
.pp-panel {
    z-index: var(--z-popover);
    width: min(21rem, calc(100vw - 1.5rem));
    max-height: min(28rem, 70vh); overflow-y: auto;
    background: var(--color-surface); color: var(--color-fg);
    border: 1px solid var(--color-border); border-radius: var(--radius-xl);
    box-shadow: var(--ds-shadow-lg);
    padding: 0 0 0.35rem;
}
.pp-search {
    display: flex; align-items: center; gap: 0.5rem;
    padding: 0.6rem 0.75rem; border-bottom: 1px solid var(--color-border);
    color: var(--color-fg-muted);
}
.pp-search .pp-search__input {
    flex: 1; min-width: 0; height: 28px; background: none; border: 0;
    box-shadow: none; padding: 0; font-size: 13px; color: var(--color-fg);
}
.pp-search kbd, .pp-panel kbd {
    border: 1px solid var(--color-border); border-radius: var(--radius-sm);
    padding: 0 0.3rem; font-size: 10.5px; font-family: var(--font-mono, monospace);
    color: var(--color-fg-muted);
}
.pp-panel .pp-row {
    display: flex; align-items: center; justify-content: flex-start; gap: 0.5rem; width: 100%;
    height: auto; padding: 0.45rem 0.75rem; min-height: 36px; text-align: left;
    font-size: 13px; font-weight: 400; color: var(--color-fg); border-radius: 0;
}
.pp-panel .pp-row:hover { background: var(--color-surface-2); }
.pp-panel .pp-row.is-active { box-shadow: inset 2px 0 0 var(--color-primary); background: var(--color-surface-2); }
.pp-panel .pp-row.is-selected { background: var(--color-info-soft); font-weight: 600; }
.pp-row--all { border-bottom: 1px solid var(--color-border); }
.pp-panel .pp-row--manage { border-top: 1px solid var(--color-border); margin-top: 0.35rem; text-decoration: none; color: var(--color-fg-muted); }
.pp-panel .pp-row--add { border-top: 1px solid var(--color-border); color: var(--color-fg-muted); }
.pp-panel .pp-row--manage + .pp-row--add { border-top: 0; }
.pp-panel .pp-more { width: 100%; justify-content: flex-start; padding-left: 2.1rem; font-size: 12px; color: var(--color-fg-muted); }
.pp-check { width: 12px; flex: none; color: var(--color-info); }
.pp-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pp-hint { font-size: 11.5px; color: var(--color-fg-faint); }
.pp-bar { width: 4rem; flex: none; }
.pp-open { width: 3.5rem; text-align: right; font-size: 11.5px; font-weight: 600; color: var(--color-warning-strong); }
.pp-none { width: 3.5rem; text-align: right; color: var(--color-fg-faint); }
.pp-group__label {
    padding: 0.55rem 0.75rem 0.2rem; font-size: 10.5px; font-weight: 600;
    letter-spacing: 0.06em; text-transform: uppercase; color: var(--color-fg-faint);
}
.pp-error, .pp-empty { padding: 0.5rem 0.75rem; font-size: 12px; color: var(--color-fg-muted); }
</style>
