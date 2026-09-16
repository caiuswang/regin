<script setup>
// The defect ledger, readable at last — and writable, which is what earns
// the list. A page that could only show eight unattributed defects while the
// only remedy stayed on the CLI would stop one click short of the one action
// owed, which is why `/features` refused to link here until now.
//
// The whole list state lives in the URL (`useDefectsQuery`) because a shared
// `?scope=`/`?status=`/`?q=`/`?sort=`/`?page=` is a link to exactly the rows
// on screen. `all` is the default scope, and that is the repair: unattributed
// used to be, so a defect reported against a feature — every report filed
// from a task page — landed in the one scope this page could not open.
import { computed, ref, watch } from 'vue'
import { usePipelineHeaderPublisher } from '../composables/usePipelineHeader'
import { useDefectsQuery } from '../composables/useDefectsQuery'
import { useFlash } from '../composables/useFlash'
import api from '../api'
import Icon from '../components/ui/Icon.vue'
import PageControls from '../components/PageControls.vue'
import DefectRow from '../components/defects/DefectRow.vue'
import DefectsToolbar from '../components/defects/DefectsToolbar.vue'
import DefectsScopeBanner from '../components/defects/DefectsScopeBanner.vue'

const publish = usePipelineHeaderPublisher()
const { state, apiQuery, sortKey, sortDir, patch, reset, narrowed } = useDefectsQuery()
const { flash } = useFlash()

const page = ref(null)
// Two different questions, and conflating them is what lost the reader's
// place: "is there anything to draw yet" and "is a read in flight". Only the
// first may blank the list — a reload after a write keeps the rows, the
// scroll position and any half-typed form standing (invariant 7).
const loading = ref(true)
const busy = ref(false)
const loadError = ref('')
// Which read the rendered rows belong to. Two reads can be in flight — a
// write's reload and a control change — and without an identity the one that
// answers last wins, leaving rows on screen under a URL that asked for
// different ones. Compared, not timed.
let issued = 0

// Read off the stored role, never off a field in the payload — a payload that
// varies by role is a payload two users cannot compare.
const canWrite = computed(() => api.getStoredUser()?.role !== 'viewer')

const pagination = computed(() => page.value?.pagination || {})
// The population the controls describe, not the rows this page holds
// (invariant 6). Falling back to the loaded length is only reached when the
// response carries no pagination at all, where the two are the same number.
const total = computed(
  () => pagination.value.total ?? page.value?.items?.length ?? 0)
const pageCount = computed(
  () => Math.max(1, Math.ceil(total.value / (pagination.value.size || state.value.size))))

// Each scope says what its own emptiness means. "no defects" would be a claim
// about the ledger; these are claims about the scope, which is what is true.
const EMPTY = {
  all: 'The ledger is empty. Nothing has been reported yet.',
  unattributed: 'Nothing is unblamed. Every defect regin holds names the feature that caused it.',
  feature: 'This feature has no defects recorded.',
  'fix-item': 'Nothing points at this item.',
}

const LEAD = {
  all: 'Every defect regin holds, newest first — attributed or not.',
  unattributed: 'Defects blamed on no feature. Attribution is a human naming the feature that CAUSED the bug — regin never guesses one from a path, a session or a cost.',
  feature: 'Every defect blamed on this feature.',
  'fix-item': 'Every defect this one tracker item is meant to close.',
}

// A narrowed list is still a claim about a population, so the empty state has
// to name the narrowing rather than the ledger — otherwise a search with no
// hits reads as "there are no defects".
const emptyText = computed(() => {
  // Past the end is a position, not a verdict on the ledger. Saying "nothing
  // has been reported yet" over 120 rows is the page contradicting the count
  // printed beside it.
  if (total.value) return `Page ${state.value.page + 1} is past the end of ${total.value}.`
  if (narrowed.value) return 'No defect matches these controls. Widen the search or reset them.'
  return EMPTY[page.value?.scope] || EMPTY.all
})

// A write can move the row OUT of the scope being read — attributing one on
// the unattributed list is the whole point of that list — so the scope is
// re-read rather than the row patched in place. Splicing the server's row back
// left an attributed defect sitting under "blamed on no feature", still in the
// count, which is the page asserting something it had just disproved. The
// predicate is the server's, and re-reading is how it stays the server's.
async function load() {
  const mine = ++issued
  busy.value = true
  loadError.value = ''
  try {
    const body = await api.get(`/defects?${apiQuery.value}`)
    if (mine !== issued) return
    page.value = body
    publish({ subtitle: LEAD[page.value.scope] })
  } catch (e) {
    if (mine !== issued) return
    loadError.value = e?.message || String(e)
  } finally {
    if (mine === issued) {
      loading.value = false
      busy.value = false
    }
  }
}
watch(apiQuery, load, { immediate: true })

// After a write the list reloads on the page and under the query it was
// already on. Nothing resets to page 0 and nothing remounts: the row the
// operator just acted on stays where they left it.
function onChanged(message) {
  if (message) flash(message)
  load()
}
</script>

<template>
  <div class="space-y-4">
    <!-- The title and the scope's lead sentence are published up to the
         Pipeline shell; the controls stay here, because they are this tab's
         own predicate rather than a way out of it. -->
    <header class="space-y-2">
      <DefectsToolbar
        :state="state"
        :sort-key="sortKey"
        :sort-dir="sortDir"
        :narrowed="narrowed"
        :show-scopes="!state.feature_id && !state.fix_item"
        @patch="patch"
        @reset="reset"
      />
      <DefectsScopeBanner
        :feature-id="state.feature_id"
        :fix-item="state.fix_item"
        :feature-title="page?.items?.[0]?.feature_title || ''"
      />
    </header>

    <p v-if="loading" class="text-sm text-slate-500">Loading…</p>
    <p
      v-else-if="loadError"
      class="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700"
      role="alert"
    >{{ loadError }}</p>

    <template v-else-if="page">
      <p v-if="page.scope === 'unattributed' && total" class="count">
        <Icon name="alert-triangle" :size="11" />
        {{ total }} {{ total === 1 ? 'defect' : 'defects' }} blamed on no feature
      </p>

      <div v-if="!page.items.length" class="empty">
        <strong>nothing here</strong>
        <p>{{ emptyText }}</p>
        <p v-if="total">
          <router-link :to="{ path: '/defects', query: { ...$route.query, page: undefined } }"
          >back to the first page</router-link>
        </p>
      </div>

      <template v-else>
        <ul class="list" :class="{ 'is-busy': busy }">
          <DefectRow
            v-for="defect in page.items"
            :key="defect.id"
            :defect="defect"
            :can-write="canWrite"
            @changed="onChanged"
          />
        </ul>
        <PageControls
          v-if="page.pagination"
          :page="pagination.page ?? state.page"
          :page-count="pageCount"
          :total="total"
          :size="pagination.size ?? state.size"
          :has-next="!!pagination.has_next"
          :has-prev="!!pagination.has_prev"
          :loading="busy"
          @prev="patch({ page: String(state.page - 1) })"
          @next="patch({ page: String(state.page + 1) })"
          @goto="n => patch({ page: String(Math.min(Math.max(0, n), pageCount - 1)) })"
          @set-size="n => patch({ size: String(n) })"
        />
      </template>
    </template>
  </div>
</template>

<style scoped>
.count {
    display: inline-flex; align-items: center; gap: 0.3rem;
    font-size: 11.5px; color: var(--color-fg-faint);
    font-variant-numeric: tabular-nums;
}
.list { display: flex; flex-direction: column; gap: 0.6rem; }
/* A reload in flight dims the rows it is about to replace. It does not
   remove them: the reader keeps their place, and the open form keeps its
   text. */
.list.is-busy { opacity: 0.6; }
.empty {
    border: 1px dashed var(--color-border); border-radius: var(--radius-xl);
    padding: 1.2rem; text-align: center; color: var(--color-fg-muted);
}
.empty strong { display: block; font-size: 13px; color: var(--color-fg); }
.empty p { margin-top: 0.25rem; font-size: 11.5px; }
</style>
