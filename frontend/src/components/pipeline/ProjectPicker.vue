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
import { stageParts } from '../../utils/pipelineFormat'
import { SCOPE_ALL, SCOPE_NONE } from '../../composables/usePipelineScope'

const props = defineProps({
  scope: { type: String, required: true },
  label: { type: String, required: true },
  compact: { type: Boolean, default: false },
})
const emit = defineEmits(['select'])

const open = ref(false)
const q = ref('')
const items = ref([])
const population = ref(null)
const error = ref('')
const search = ref(null)
let issued = 0

async function load() {
  const mine = ++issued
  try {
    const params = new URLSearchParams({ sort: 'attention', size: '100' })
    if (q.value.trim()) params.set('q', q.value.trim())
    const body = await api.get(`/pipeline/projects?${params}`)
    if (mine !== issued) return
    items.value = body.items || []
    population.value = body.population ?? null
    error.value = ''
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

// "No project" is a bucket, not a project, so it is not one of the N.
const projectCount = computed(() => (population.value == null ? null : Math.max(0, population.value - 1)))

watch(open, async (isOpen) => {
  if (!isOpen) return
  q.value = ''
  load()
  await nextTick()
  search.value?.focus()
})
watch(q, load)

function pick(scope) {
  open.value = false
  emit('select', scope)
}

const selected = row => String(row.scope) === props.scope

defineExpose({ open: () => { open.value = true } })
</script>

<template>
  <PopoverRoot v-model:open="open">
    <PopoverAnchor as-child>
      <div class="pp-chip" :class="{ 'is-scoped': scope !== SCOPE_ALL, 'pp-chip--compact': compact }">
        <Button
          variant="ghost"
          size="sm"
          class="pp-chip__open"
          data-testid="pipeline-project"
          :aria-expanded="open"
          aria-haspopup="dialog"
          @click="open = !open"
        >
          <Icon v-if="!compact" name="folder" :size="13" />
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
            :placeholder="projectCount == null ? 'Find a project' : `Find among ${projectCount} projects`"
            aria-label="Find a project"
            data-testid="project-picker-search"
          />
          <kbd>p</kbd>
        </label>
        <Button
          variant="ghost"
          class="pp-row pp-row--all"
          :class="{ 'is-selected': scope === SCOPE_ALL }"
          data-testid="project-picker-all"
          @click="pick(SCOPE_ALL)"
        >
          <span class="pp-check"><Icon v-if="scope === SCOPE_ALL" name="check" :size="12" /></span>
          <Icon name="layout-grid" :size="13" />
          <span class="pp-name">All projects</span>
          <span class="pp-hint">portfolio view</span>
        </Button>
        <p v-if="error" class="pp-error" role="alert">{{ error }}</p>
        <div v-for="g in groups" :key="g.label" class="pp-group">
          <div class="pp-group__label">{{ g.label }}</div>
          <Button
            v-for="row in g.rows"
            :key="row.scope"
            variant="ghost"
            class="pp-row"
            :class="{ 'is-selected': selected(row) }"
            :data-testid="`project-picker-row-${row.scope}`"
            @click="pick(row.id == null ? SCOPE_NONE : String(row.id))"
          >
            <span class="pp-check"><Icon v-if="selected(row)" name="check" :size="12" /></span>
            <span class="pp-name">{{ row.name || (row.id == null ? 'No project' : `project #${row.id}`) }}</span>
            <span class="pp-bar"><SegmentBar :parts="stageParts(row.features?.by_stage)" :height="4" /></span>
            <span v-if="row.defects?.open" class="pp-open">{{ row.defects.open }} open</span>
            <span v-else class="pp-none">—</span>
          </Button>
        </div>
        <p v-if="!error && !items.length && q" class="pp-empty">No project matches “{{ q }}”.</p>
      </PopoverContent>
    </PopoverPortal>
  </PopoverRoot>
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
.pp-panel .pp-row.is-selected { background: var(--color-info-soft); font-weight: 600; }
.pp-row--all { border-bottom: 1px solid var(--color-border); }
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
