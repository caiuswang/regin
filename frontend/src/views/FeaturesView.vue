<script setup>
// Every feature regin knows, as stage tracks with their defects hanging off
// them, and the triage rail beside them (designs 1c, 2c).
//
// Every narrowing is in the URL — search, stage, sort and page are read from
// `route.query` and written back by navigation — and the server does all of
// it: `GET /api/features` is an offset page with stage-blind stage counts, so
// nothing here filters or counts a loaded array.
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { usePipelineHeaderPublisher, usePipelineTabActive } from '../composables/usePipelineHeader'
import { SCOPE_ALL, SCOPE_NONE, scopeOf, scopeParam } from '../composables/usePipelineScope'
import { usePage } from '../composables/usePage'
import { listStatusState, useListQuery } from '../composables/useListQuery'
import { useTaskShortcuts } from '../composables/useTaskShortcuts'
import { useBreakpoint } from '../composables/useBreakpoint'
import api from '../api'
import Button from '../components/ui/Button.vue'
import Icon from '../components/ui/Icon.vue'
import Input from '../components/ui/Input.vue'
import Select from '../components/ui/Select.vue'
import ListStatus from '../components/ListStatus.vue'
import PageControls from '../components/PageControls.vue'
import PipelineCreateButton from '../components/pipeline/PipelineCreateButton.vue'
import { usePipelineCreate } from '../composables/usePipelineCreate'
import { usePipelineCounts } from '../composables/usePipelineCounts'
import { usePipelineLive } from '../composables/usePipelineEvents'
import PlSlot from '../components/pipeline/PlSlot.vue'
import FeatureTrackTable from '../components/pipeline/FeatureTrackTable.vue'
import FeatureCard from '../components/pipeline/FeatureCard.vue'
import TriageRail from '../components/pipeline/TriageRail.vue'
import TriageSheet from '../components/pipeline/TriageSheet.vue'

const route = useRoute()
const isCurrentTab = usePipelineTabActive()
const publish = usePipelineHeaderPublisher()
const { isMdUp } = useBreakpoint()

// `lib/features/index.py::SORT_KEYS`. Restated rather than fetched because a
// select whose options arrive late renders a control with nothing in it.
const SORT_OPTIONS = [
  { value: 'activity:desc', label: 'Last activity ↓' },
  { value: 'rounds:desc', label: 'Rounds ↓' },
  { value: 'open_defects:desc', label: 'Open defects ↓' },
  { value: 'escapes:desc', label: 'Escapes ↓' },
  { value: 'cost:desc', label: 'Cost ↓' },
  { value: 'created:desc', label: 'Newest ↓' },
]

const list = useListQuery({
  defaults: { q: '', stage: '', sort: 'activity:desc' },
  facets: ['stage'],
  kept: ['triage'],
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
    project_id: scopeParam(route.query.project),
  }),
})

const appliedSort = computed(() => applied.value?.sort || state.value.sort)
const status = computed(() => listStatusState({
  loaded: loaded.value, error: error.value, items: items.value,
  total: total.value, population: population.value,
}))

// Escaped cards lead on a phone: they are the ones owed attention.
const cards = computed(() => [...items.value].sort((a, b) =>
  Number(b.stage === 'escaped') - Number(a.stage === 'escaped')))

function dollars(v) {
  return `$${Math.round(Number(v || 0)).toLocaleString('en-US')}`
}
watch(() => extras.value.attribution, (a) => {
  if (!a) return
  const pct = a.sessions_pct == null ? '—' : `${a.sessions_pct}%`
  publish({ subline: `${pct} of sessions attributed · ${dollars(a.declared_usd)} of ${dollars(a.all_usd)}` })
})

function gotoPage(index) {
  list.patch({ page: String(Math.max(0, Math.min(pageCount.value - 1, index))) })
}

async function reload() {
  page.value = state.value.page
  pageSize.value = state.value.size
  await load()
}
watch(() => (route.path === '/features'
  ? JSON.stringify({ ...route.query, triage: undefined }) : null),
(key) => { if (key != null) reload() }, { immediate: true })

const search = ref(null)
// A search commits on Enter and Escape reverts it, so typing costs no request.
const draft = ref(state.value.q)
// Any navigation drops an unsubmitted term: the box describes the rows below.
watch(() => route.fullPath, () => { draft.value = state.value.q })
const canWrite = computed(() => api.getStoredUser()?.role !== 'viewer')
const { openCreate } = usePipelineCreate()
const { revision } = usePipelineCounts()
const triaging = ref(false)

// The secret stage's HIGH SCORE is the loop's settled totals for this scope,
// read when it opens; the classic theme never asks.
// Only the newest open may write: a reply for a scope since left would put
// that scope's numbers under this one's label.
const secret = ref({ open: false, loop: null, label: '', error: '' })
let secretAsked = 0
async function openSecret() {
  const mine = ++secretAsked
  const scope = scopeOf(route.query)
  const label = scope === SCOPE_ALL ? 'All projects' : scope === SCOPE_NONE ? 'No project' : `project #${scope}`
  secret.value = { open: true, loop: null, label, error: '' }
  try {
    const qs = scope !== SCOPE_ALL ? `?project=${encodeURIComponent(scope)}` : ''
    const body = await api.get(`/pipeline/overview${qs}`)
    if (mine !== secretAsked) return
    secret.value = { ...secret.value, loop: body.loop, label: body.project_head?.name || label }
  } catch (e) {
    if (mine !== secretAsked) return
    secret.value = { ...secret.value, error: e?.message || String(e) }
  }
}
const rail = ref(null)
const railFolded = ref(false)

// A write made elsewhere — a defect reported from the header's "New ▾", a
// capture — moves this tab's rows and its triage queue too.
usePipelineLive(() => Promise.all([reload(), rail.value?.load?.()]))

watch(revision, () => {
  if (route.path !== '/features') return
  reload()
  rail.value?.load?.()
})

useTaskShortcuts({
  '/': () => search.value?.focus(),
  c: () => { if (canWrite.value) openCreate('feature') },
  ']': () => { if (isMdUp.value) railFolded.value = !railFolded.value },
})
</script>

<template>
  <div class="fv">
    <Teleport v-if="isCurrentTab && canWrite" defer to="#pipeline-actions">
      <PipelineCreateButton kind="feature" testid="declare-feature" />
    </Teleport>

    <div class="fv-layout" :class="{ 'fv-layout--folded': isMdUp && railFolded }">
      <div class="fv-main" data-pl-part="panel">
        <div class="fv-toolbar" data-pl-part="panel-head" data-pl-chapter="3-1" data-pl-title="Features">
          <PlSlot
            name="FeatureStageTabs"
            :counts="extras.stage_counts || null"
            :stage="state.stage"
            :compact="!isMdUp"
            @select="s => list.patch({ stage: s })"
            @secret="openSecret"
          />
          <template v-if="isMdUp">
            <span class="fv-spacer" />
            <label class="fv-search" data-pl-part="input">
              <Icon name="search" :size="13" />
              <Input
                ref="search"
                v-model="draft"
                type="search"
                class="fv-search__input"
                placeholder="Search features"
                aria-label="Search features by title, spec or tracker reference"
                data-testid="list-search"
                @keydown.enter.prevent="list.patch({ q: draft.trim() })"
                @keydown.esc.prevent="draft = state.q"
              />
            </label>
            <Select
              :model-value="appliedSort"
              :options="SORT_OPTIONS"
              bare
              aria-label="Sort features"
              data-testid="features-sort"
              @update:model-value="v => list.patch({ sort: v })"
            />
          </template>
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
            <Button v-if="canWrite" variant="secondary" size="sm" @click="openCreate('feature')">New feature</Button>
          </template>
        </ListStatus>

        <div v-if="status === 'rows'" :class="{ 'is-busy': loading }">
          <FeatureTrackTable v-if="isMdUp" :rows="items" />
          <div v-else class="fv-cards">
            <FeatureCard v-for="f in cards" :key="f.id" :feature="f" />
          </div>
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

      <aside v-if="isMdUp" class="fv-rail" data-pl-part="panel">
        <TriageRail
          ref="rail"
          v-model:folded="railFolded"
          :scope="route.query.project ? String(route.query.project) : ''"
          :focus-id="route.query.triage || null"
          @changed="reload"
        />
      </aside>
      <section v-else class="fv-triage-card" data-testid="features-triage-card" data-pl-part="panel">
        <span>Reports nobody has routed</span>
        <Button variant="secondary" class="fv-triage-go" data-testid="features-triage-open" data-pl-part="key" @click="triaging = true">
          Triage
        </Button>
      </section>
    </div>

    <TriageSheet
      v-if="!isMdUp"
      v-model:open="triaging"
      :scope="route.query.project ? String(route.query.project) : ''"
      @changed="reload"
    />
    <PlSlot
      v-model:open="secret.open"
      name="SecretStage"
      :loop="secret.loop"
      :scope-label="secret.label"
      :error="secret.error"
    />
  </div>
</template>

<style scoped>
.fv { min-width: 0; }
.fv-layout {
    display: grid; gap: 1.25rem; align-items: start;
    grid-template-columns: minmax(0, 1fr) 21rem;
    transition: grid-template-columns 0.2s ease;
}
.fv-layout--folded { grid-template-columns: minmax(0, 1fr) 52px; }
.fv-layout--folded .fv-rail { padding-left: 0; }
@media (prefers-reduced-motion: reduce) {
    .fv-layout { transition: none; }
}
.fv-main { min-width: 0; display: flex; flex-direction: column; gap: 0.9rem; }
.fv-toolbar { display: flex; align-items: center; gap: 0.6rem; min-width: 0; flex-wrap: wrap; }
.fv-spacer { flex: 1; }
.fv-search {
    display: flex; align-items: center; gap: 0.45rem; flex: 0 1 12rem; min-width: 8rem;
    border: 1px solid var(--color-border); border-radius: var(--radius-lg);
    background: var(--color-surface); padding: 0 0.6rem; color: var(--color-fg-faint);
}
.fv-search .fv-search__input { border: 0; box-shadow: none; background: none; padding: 0; height: 32px; }
.fv-rail {
    position: sticky; top: 0.5rem;
    border-left: 1px solid var(--color-border); padding-left: 1.25rem; min-width: 0;
}
.fv-cards { display: flex; flex-direction: column; gap: 0.6rem; }
.is-busy { opacity: 0.6; }
.fv-triage-card {
    display: flex; align-items: center; gap: 0.75rem;
    border: 1px solid var(--color-amber-300); border-radius: var(--radius-xl);
    background: var(--color-warning-soft); padding: 0.75rem 1rem;
    font-size: 13px; color: var(--color-warning-strong); font-weight: 600;
}
.fv-triage-card span { flex: 1; }
.fv-triage-card .fv-triage-go { min-height: 44px; background: var(--color-surface); color: var(--color-fg); }

@media (max-width: 1023.98px) {
    .fv-layout { grid-template-columns: minmax(0, 1fr); }
    .fv-rail { position: static; border-left: 0; padding-left: 0; border-top: 1px solid var(--color-border); padding-top: 1rem; }
    .fv-layout--folded { grid-template-columns: minmax(0, 1fr); }
    .fv-layout--folded .fv-rail { padding-top: 0; }
}
@media (max-width: 767.98px) {
    .fv-layout { display: flex; flex-direction: column-reverse; align-items: stretch; gap: 0.9rem; }
    .fv { padding-bottom: 5rem; }
}
</style>
