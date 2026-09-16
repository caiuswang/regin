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
import api from '../api'
import Button from '../components/ui/Button.vue'
import Icon from '../components/ui/Icon.vue'
import ListToolbar from '../components/ListToolbar.vue'
import PageControls from '../components/PageControls.vue'
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
const DEFAULT_SORT = 'activity:desc'

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

const q = computed(() => String(route.query.q || ''))
const stage = computed(() => String(route.query.stage || ''))
const sort = computed(() => String(route.query.sort || DEFAULT_SORT))
const sortKey = computed(() => sort.value.split(':')[0] || 'activity')
const sortDir = computed(() => (sort.value.split(':')[1] === 'asc' ? 'asc' : 'desc'))

const {
  items, extras, loading, error,
  page, pageSize, total, pageCount, hasNext, hasPrev, load,
} = usePage({
  path: '/features',
  buildQuery: () => ({
    q: q.value,
    stage: stage.value,
    sort: sort.value,
    // Shell-owned (R1): the tab reads the scope but never sets it, so the
    // project carries across tabs without three writers of one axis. The URL
    // spells it `project` — one axis, one name, across all three tabs — and
    // this endpoint spells the same scope `project_id`, so the translation
    // happens here rather than by the shell writing a per-tab key. `all` is
    // the absence of a scope rather than a value of one, and this endpoint
    // refuses the literal, so `scopeParam` drops it.
    project_id: scopeParam(route.query.project),
  }),
})

// The search box is the one control that is not purely derived: it holds what
// has been typed but not yet submitted. Submitting is a navigation.
const draft = ref(null)
const typed = computed({
  get: () => (draft.value === null ? q.value : draft.value),
  set: (value) => { draft.value = value },
})

const population = computed(() => extras.value.total ?? 0)
const cov = computed(() => extras.value.coverage)
const unattributed = computed(() => extras.value.unattributed_defects || 0)
// What the counters describe: `total` is the filtered match, `population` the
// table. Two numbers because they answer two questions, and one number
// answering both answers neither.
const filtered = computed(() => Boolean(q.value || stage.value))
// An offset past the last page: rows exist, this page just has none of them.
const beyondEnd = computed(() => Boolean(total.value) && !items.value.length)
// Whether Reset has anything to undo — the position counts, because a reader
// stranded on page 9 of a list they did not filter still needs one control
// that puts them back at the top.
const narrowed = computed(() => Boolean(filtered.value || route.query.sort
  || route.query.page || route.query.size))

// One navigation per control, and a filter change drops the page: an offset
// that survives a narrowing points into rows the new query does not have.
// `replace`, not `push`, so the Back button leaves the list rather than
// walking a filter history nobody asked to keep.
function navigate(patch, { keepPage = false } = {}) {
  const next = { ...route.query, ...patch }
  if (!keepPage) delete next.page
  for (const [key, value] of Object.entries(next)) {
    if (value === '' || value === undefined || value === null) delete next[key]
  }
  router.replace({ path: '/features', query: next })
}

function submitSearch() {
  const term = typed.value.trim()
  draft.value = null
  navigate({ q: term })
}
function setFacet(key, value) { navigate({ [key]: value }) }
function setSort(key, dir) { navigate({ sort: `${key}:${dir}` }) }
function toggleDir() {
  setSort(sortKey.value, sortDir.value === 'asc' ? 'desc' : 'asc')
}
function reset() {
  draft.value = null
  const { project } = route.query
  router.replace({ path: '/features', query: project ? { project } : {} })
}
// Clamped to the list that came back, because the server echoes the offset it
// was ASKED for rather than the last one that exists — `?page=9` on a two-page
// list is a real read returning no rows. Without the clamp, Prev walks back one
// empty page at a time from wherever the link pointed.
function gotoPage(index) {
  const target = Math.max(0, Math.min(pageCount.value - 1, index))
  navigate({ page: String(target) }, { keepPage: true })
}
function setSize(size) { navigate({ size: String(size) }) }

// The URL is read, never written back from here: `usePage` holds the offset
// this view navigated to, and a second writer of it would let the rows and the
// address bar disagree.
async function reload() {
  // An unsubmitted search term describes the rows it was typed against and no
  // others, so any navigation retires it. Leaving it in the box lets the
  // control contradict the list beneath it — invariant 1, inside one session.
  draft.value = null
  page.value = Math.max(0, parseInt(route.query.page, 10) || 0)
  const size = parseInt(route.query.size, 10)
  if (size > 0) pageSize.value = size
  await load()
}
watch(() => route.query, reload, { immediate: true, deep: true })

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
</script>

<template>
  <div class="space-y-4">
    <!-- The title and the lead belong to the Pipeline shell now; the controls
         belong to this tab alone, and ride up into the shell's header. -->
    <Teleport v-if="isCurrentTab" defer to="#pipeline-actions">
      <div class="flex items-center gap-2">
        <ListToolbar
          v-model:q="typed"
          search-label="Search features by title, spec or tracker reference"
          search-placeholder="search title, spec or reference"
          :facets="[{ key: 'stage', label: 'Filter by stage',
                      value: stage, options: STAGE_OPTIONS }]"
          :sort-options="SORT_OPTIONS"
          :sort-key="sortKey"
          :sort-dir="sortDir"
          :dirty="narrowed"
          @submit="submitSearch"
          @facet="setFacet"
          @sort-key="key => setSort(key, sortDir)"
          @toggle-dir="toggleDir"
          @reset="reset"
        />
        <Button
          v-if="canWrite"
          variant="primary"
          data-testid="declare-feature"
          @click="declaring = true"
        >
          <Icon name="plus" :size="12" />
          New feature
        </Button>
      </div>
    </Teleport>

    <NewFeatureDialog v-model:open="declaring" @declared="declared" />

    <div v-if="cov" class="counters">
      <span>
        <b>{{ cov.declared_sessions }}</b> of {{ num(cov.all_sessions) }}
        sessions attributed
        ({{ percent(cov.declared_sessions, cov.all_sessions) }})
        · <b>{{ money(cov.declared_usd) }}</b> of {{ money(cov.all_usd) }}
        ({{ percent(cov.declared_usd, cov.all_usd) }})
      </span>
      <!-- A link now that `/defects` carries the remedy as well as the list:
           attribute, close and reopen are all on the web, so the click lands
           on the action owed instead of one short of it. Both this number and
           that page read `store.unattributed()`, so they cannot disagree. -->
      <router-link
        v-if="unattributed" class="counter-gap"
        :to="{ path: '/defects', query: { scope: 'unattributed' } }"
      >
        <Icon name="alert-triangle" :size="11" />
        <b>{{ unattributed }}</b> defects blamed on no feature
      </router-link>
    </div>

    <p v-if="loading" class="text-sm text-slate-500">Loading…</p>
    <p
      v-else-if="error"
      class="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700"
      role="alert"
    >{{ error }}</p>

    <template v-else>
      <p v-if="filtered" class="text-xs text-slate-500">
        {{ total }} of {{ population }} features match
      </p>

      <!-- An empty page has two causes and they need two sentences. A
           narrowing that matched nothing is about the query; an offset past
           the end is about the position, and telling that reader "nothing
           matches" sends them to change a filter that is working. -->
      <div v-if="!items.length" class="empty">
        <strong>{{ beyondEnd ? 'past the end' : 'no match' }}</strong>
        <p v-if="beyondEnd">
          Page {{ page + 1 }} is past the last page of this list. Its
          {{ total }} {{ total === 1 ? 'row' : 'rows' }} end on page
          {{ pageCount }}.
        </p>
        <p v-else>
          Nothing here matches that. regin knows {{ population }}
          {{ population === 1 ? 'feature' : 'features' }} in total.
        </p>
      </div>

      <template v-else>
        <FeatureIndexTable
          :rows="items"
          :sort-key="sortKey"
          :sort-dir="sortDir"
          @sort="setSort"
        />
      </template>

      <!-- Outside the rows, because the way back from an offset past the end
           is the pager itself: hiding it with the table strands the reader on
           an empty page with no Prev. -->
      <PageControls
        v-if="total"
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
        @set-size="setSize"
      />
    </template>
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
.empty {
    border: 1px dashed var(--color-border-strong); border-radius: 0.6rem;
    padding: 0.9rem 1rem; color: var(--color-fg-muted);
}
.empty strong {
    display: block; font-family: var(--font-mono, monospace);
    font-size: 13px; color: var(--color-fg-subtle);
}
.empty p { margin-top: 0.3rem; font-size: 12px; }
</style>
