<script setup>
// Every feature regin knows — a way in, not a leaderboard.
//
// The population is the `features` table, so this list has no membership rule
// of its own to disagree with the ledger's. It used to be a union of four
// string sources, which is how a shipped feature could be missing from it.
//
// The header counters describe the POPULATION, never the match, so a search
// reads "3 of 25 features" rather than implying it has shown everything.
//
// **Every narrowing is in the URL.** Search, stage, sort and page are read from
// `route.query` and written back by navigation — the view holds no filter state
// of its own. That is the single rule the whole consistency story rests on
// (spec 73, R1): a narrowed list is a link, and the controls above the rows
// always describe the rows below them. It also means the server does every
// narrowing: `GET /api/features` is an offset page with facets and seven sort
// keys, so nothing here filters or sorts a loaded array.
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { usePipelineTabActive } from '../composables/usePipelineHeader'
import { scopeParam } from '../composables/usePipelineScope'
import { usePage } from '../composables/usePage'
import { listStatusState, useListQuery } from '../composables/useListQuery'
import { useTaskShortcuts } from '../composables/useTaskShortcuts'
import api from '../api'
import Button from '../components/ui/Button.vue'
import Icon from '../components/ui/Icon.vue'
import ListStatus from '../components/ListStatus.vue'
import PageControls from '../components/PageControls.vue'
import PipelineListToolbar from '../components/PipelineListToolbar.vue'
import FeatureIndexTable from '../components/features/FeatureIndexTable.vue'
import NewFeatureDialog from '../components/features/NewFeatureDialog.vue'
import { money, num, percent } from '../utils/ledgerFormat'

const route = useRoute()
const router = useRouter()
const isCurrentTab = usePipelineTabActive()

// `lib/features/index.py::SORT_KEYS`, in the order the toolbar offers them.
// Restated here rather than fetched because a select whose options arrive late
// renders a control with nothing in it; the server is still the authority, and
// it falls back to its own default rather than erroring on a key it dropped.
const SORT_OPTIONS = [
  { value: 'activity', label: 'Last activity' },
  { value: 'rounds', label: 'Rounds' },
  { value: 'open_defects', label: 'Open defects' },
  { value: 'escapes', label: 'Escapes' },
  { value: 'cost', label: 'Cost' },
  { value: 'stage', label: 'Stage' },
  { value: 'created', label: 'Created' },
]

// `lib/features/events.py::STAGES`. An unknown value is a 400 from the server,
// not a silently dropped filter, so this list and that tuple must agree.
const STAGE_OPTIONS = [
  { value: '', label: 'Any stage' },
  { value: 'spec', label: 'spec' },
  { value: 'building', label: 'building' },
  { value: 'shipped', label: 'shipped' },
  { value: 'escaped', label: 'escaped' },
  { value: 'abandoned', label: 'abandoned' },
]

const list = useListQuery({
  defaults: { q: '', stage: '', sort: 'created:desc' },
  facets: ['stage'],
})
const { state } = list

const {
  items, extras, loading, error, loaded, population, applied,
  page, pageSize, total, pageCount, hasNext, hasPrev, load,
} = usePage({
  path: '/features',
  buildQuery: () => ({
    q: state.value.q,
    stage: state.value.stage,
    sort: state.value.sort,
    // Shell-owned: the URL spells the scope `project`, this endpoint spells it
    // `project_id`, and `all` is the absence of a scope the server refuses.
    project_id: scopeParam(route.query.project),
  }),
})

// The toolbar renders what the server applied, not what was asked for.
const appliedSort = computed(() => applied.value?.sort || state.value.sort)
const sortKey = computed(() => appliedSort.value.split(':')[0])
const sortDir = computed(() => (appliedSort.value.split(':')[1] === 'asc' ? 'asc' : 'desc'))

const cov = computed(() => extras.value.coverage)
const unattributed = computed(() => extras.value.unattributed_defects || 0)
const status = computed(() => listStatusState({
  loaded: loaded.value, error: error.value, items: items.value,
  total: total.value, population: population.value,
}))

function gotoPage(index) {
  const target = Math.max(0, Math.min(pageCount.value - 1, index))
  list.patch({ page: String(target) })
}

async function reload() {
  page.value = state.value.page
  pageSize.value = state.value.size
  await load()
}
// Keyed on the query string, so a route change elsewhere does not re-read.
watch(() => (route.path === '/features' ? JSON.stringify(route.query) : null),
  (key) => { if (key != null) reload() }, { immediate: true })

const toolbar = ref(null)

// The create endpoint is editor-level, so the control is too. Read off the
// stored role rather than a field in the payload, which would make the
// population's shape depend on who asked for it.
const canWrite = computed(() => api.getStoredUser()?.role !== 'viewer')
const declaring = ref(false)

// Onto the new feature's own page, not back to the list: everything the
// operator does next — finish the spec, attribute a session, ship — is there,
// and the row they just made is the only one they are looking for.
function declared(ledger) {
  if (ledger?.id) router.push(`/features/${ledger.id}`)
}

useTaskShortcuts({
  '/': () => toolbar.value?.focusSearch(),
  c: () => { if (canWrite.value) declaring.value = true },
})
</script>

<template>
  <div class="space-y-4">
    <!-- Only the create action rides up into the shell's header; the list's
         controls sit in the body, under the tab strip, as on every tab. -->
    <Teleport v-if="isCurrentTab && canWrite" defer to="#pipeline-actions">
      <Button variant="primary" data-testid="declare-feature" @click="declaring = true">
        <Icon name="plus" :size="12" />
        New feature
        <kbd class="fv-kbd">c</kbd>
      </Button>
    </Teleport>

    <PipelineListToolbar
      ref="toolbar"
      noun="feature"
      :q="state.q"
      search-label="Search features by title, spec or tracker reference"
      search-placeholder="Search title, spec or reference…"
      :facets="[{ key: 'stage', label: 'Filter by stage',
                  value: state.stage, options: STAGE_OPTIONS }]"
      :sort-options="SORT_OPTIONS"
      :sort-key="sortKey"
      :sort-dir="sortDir"
      :total="loaded && !error ? total : null"
      :population="population"
      :dirty-count="list.dirtyCount.value"
      @patch="list.patch"
      @reset="list.reset"
    />

    <NewFeatureDialog
      v-model:open="declaring"
      :scope="scopeParam(route.query.project)"
      @declared="declared"
    />

    <div v-if="cov" class="counters">
      <span>
        <b>{{ cov.declared_sessions }}</b> of {{ num(cov.all_sessions) }}
        sessions attributed
        ({{ percent(cov.declared_sessions, cov.all_sessions) }})
        · <b>{{ money(cov.declared_usd) }}</b> of {{ money(cov.all_usd) }}
        ({{ percent(cov.declared_usd, cov.all_usd) }})
      </span>
      <!-- Both this number and `/defects?scope=unattributed` read
           `store.unattributed()`, so they cannot disagree. -->
      <router-link
        v-if="unattributed" class="counter-gap"
        :to="{ path: '/defects', query: { scope: 'unattributed' } }"
      >
        <Icon name="alert-triangle" :size="11" />
        <b>{{ unattributed }}</b> defects blamed on no feature
      </router-link>
    </div>

    <ListStatus
      :state="status"
      noun="feature"
      :total="total"
      :page="page"
      :dirty-count="list.dirtyCount.value"
      :error="error || ''"
      empty-text="No features yet. Declaring one is the only way in."
      @retry="load"
      @clear="list.reset"
      @first-page="list.patch({ page: '0' })"
    >
      <template #create>
        <Button v-if="canWrite" variant="secondary" size="sm" @click="declaring = true">
          New feature
        </Button>
      </template>
    </ListStatus>

    <div v-if="status === 'rows'" :class="{ 'is-busy': loading }">
      <FeatureIndexTable
        :rows="items"
        :sort-key="sortKey"
        :sort-dir="sortDir"
        @sort="list.setSort"
      />
    </div>

    <PageControls
      v-if="loaded && total > pageSize"
      :page="page"
      :page-count="pageCount"
      :total="total"
      :size="pageSize"
      :has-next="hasNext"
      :has-prev="hasPrev"
      :loading="loading"
      @prev="gotoPage(page - 1)"
      @next="gotoPage(page + 1)"
      @goto="gotoPage"
      @set-size="n => list.patch({ size: String(n) })"
    />
  </div>
</template>

<style scoped>
.counters {
    display: flex; flex-wrap: wrap; align-items: center; gap: 0.25rem 1.25rem;
    border: 1px solid var(--color-border); border-radius: var(--radius-xl);
    background: var(--color-slate-50); padding: 0.5rem 0.9rem;
    font-size: 11.5px; color: var(--color-fg-muted);
}
.counters b { color: var(--color-fg); font-variant-numeric: tabular-nums; }
.counter-gap {
    display: inline-flex; align-items: center; gap: 0.3rem;
    color: var(--color-amber-800); text-decoration: none;
}
.counter-gap:hover { text-decoration: underline; }
.counter-gap:focus-visible { outline: 2px solid var(--color-blue-500); }
.is-busy { opacity: 0.6; }
.fv-kbd {
    margin-left: 0.25rem; padding: 0 0.25rem; font-size: 0.6875rem;
    font-family: var(--font-mono, ui-monospace, monospace);
    border: 1px solid currentColor; border-radius: var(--radius-sm, 0.25rem);
    opacity: 0.7;
}
</style>
