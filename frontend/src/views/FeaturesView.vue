<script setup>
// Every feature key regin knows — a way in, not a leaderboard.
//
// Ordered by most recent activity, which is the only total order that ranks
// nothing. Every alternative sorts a gap against a figure: a key with no
// session has no cost at all, and `ORDER BY cost` files that absence beside
// `$0.00`, which is the one rendering this whole surface refuses.
//
// The header counters describe the POPULATION, never the match, so a search
// reads "3 of 25 features" rather than implying it has shown everything. The
// search lives in the URL because a shared `?q=` is a link to a predicate,
// while a shared cursor would be a link to a position that moves.
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api from '../api'
import Badge from '../components/Badge.vue'
import Icon from '../components/ui/Icon.vue'
import Input from '../components/ui/Input.vue'
import { STAGE_COLOR, money, num, percent, shortDate } from '../utils/ledgerFormat'

const route = useRoute()
const router = useRouter()

const page = ref(null)
const loading = ref(true)
const loadError = ref('')
const query = ref(String(route.query.q || ''))

async function load() {
  loading.value = true
  loadError.value = ''
  const q = String(route.query.q || '')
  try {
    page.value = await api.get(
      `/features${q ? `?q=${encodeURIComponent(q)}` : ''}`)
  } catch (e) {
    loadError.value = e?.message || String(e)
  } finally {
    loading.value = false
  }
}
watch(() => route.query.q, () => {
  query.value = String(route.query.q || '')
  load()
}, { immediate: true })

// Submitted on Enter, never per keystroke: a debounce is a timer heuristic,
// and a submit is a navigation.
function submit() {
  const q = query.value.trim()
  router.push({ path: '/features', query: q ? { q } : {} })
}

const cov = computed(() => page.value?.coverage)
const searching = computed(() => Boolean(String(route.query.q || '')))
</script>

<template>
  <div class="mx-auto max-w-6xl space-y-4 p-4">
    <header class="flex flex-wrap items-baseline justify-between gap-3">
      <div>
        <h1 class="text-lg font-semibold text-slate-800">Features</h1>
        <p class="text-xs text-slate-500">
          Every key regin knows — from a task link, a declaration, or a bug
          blamed on it.
        </p>
      </div>
      <Input
        v-model="query"
        class="w-64"
        placeholder="search key or title"
        aria-label="Search features by key or title"
        @keydown.enter.prevent="submit"
      />
    </header>

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
        v-if="page.unattributed_defects" class="counter-gap"
        :to="{ path: '/defects', query: { scope: 'unattributed' } }"
      >
        <Icon name="alert-triangle" :size="11" />
        <b>{{ page.unattributed_defects }}</b> defects blamed on no feature
      </router-link>
    </div>

    <p v-if="loading" class="text-sm text-slate-500">Loading…</p>
    <p
      v-else-if="loadError"
      class="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700"
      role="alert"
    >{{ loadError }}</p>

    <template v-else-if="page">
      <p class="text-xs text-slate-500">
        <template v-if="searching">
          {{ page.items.length }} of {{ page.total }} features
        </template>
        <template v-else>{{ page.total }} features</template>
      </p>

      <div v-if="!page.items.length" class="empty">
        <strong>no match</strong>
        <p>
          Nothing here matches that. regin knows {{ page.total }} feature
          {{ page.total === 1 ? 'key' : 'keys' }} in total.
        </p>
      </div>

      <table v-else class="index">
        <thead>
          <tr>
            <th scope="col">Feature</th>
            <th scope="col">Stage</th>
            <th scope="col" class="num">Sessions</th>
            <th scope="col" class="num">Cost</th>
            <th scope="col" class="num">Defects</th>
            <th scope="col" class="num">Last activity</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in page.items" :key="row.key">
            <td>
              <router-link class="key" :to="`/features/${row.key}`">{{ row.key }}</router-link>
              <span class="title" :title="row.title || ''">{{ row.title || '—' }}</span>
            </td>
            <td>
              <Badge
                v-if="row.stage"
                :color="STAGE_COLOR[row.stage] || 'gray'"
                :label="row.stage"
              />
              <!-- Not a degraded row: a key with no stage and bugs blamed on
                   it is exactly the population this surface exists to
                   shrink. -->
              <span v-else class="nostage">stage not fetched</span>
            </td>
            <td class="num">{{ row.sessions || '—' }}</td>
            <td class="num" :class="row.cost_usd === null ? 'is-empty' : ''">
              <template v-if="row.cost_usd === null">none declared</template>
              <template v-else>≤ {{ money(row.cost_usd) }}</template>
            </td>
            <td class="num">
              <!-- A link, because the count is the question and the rows are
                   the answer: a number with no way through to what it counts
                   is where a reported defect goes unread. -->
              <router-link
                v-if="row.defects.total"
                :to="{ path: '/defects', query: { feature: row.key } }"
                :class="row.defects.escapes ? 'text-amber-700' : ''"
              >{{ row.defects.open }} / {{ row.defects.total }}</router-link>
              <span v-else>{{ row.defects.open }} / {{ row.defects.total }}</span>
            </td>
            <td class="num muted">{{ shortDate(row.last_activity) }}</td>
          </tr>
        </tbody>
      </table>
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
.index { width: 100%; border-collapse: collapse; font-size: 13px; }
.index th {
    text-align: left; font-size: 10.5px; font-weight: 600;
    letter-spacing: 0.06em; text-transform: uppercase;
    color: var(--color-fg-faint); padding: 0.3rem 0.5rem;
    border-bottom: 1px solid var(--color-border);
}
.index td {
    padding: 0.45rem 0.5rem;
    border-bottom: 1px solid var(--color-border-subtle);
    vertical-align: baseline;
}
.index tbody tr:hover { background: var(--color-slate-50); }
.index .num {
    text-align: right; font-variant-numeric: tabular-nums;
    white-space: nowrap;
}
.index .muted { color: var(--color-fg-faint); }
.key {
    font-family: var(--font-mono, monospace); font-weight: 600;
    color: var(--color-primary); text-decoration: none;
}
.key:hover { text-decoration: underline; }
.key:focus-visible { outline: 2px solid var(--color-blue-500); }
.title {
    display: block; max-width: 34rem; overflow: hidden;
    text-overflow: ellipsis; white-space: nowrap;
    font-size: 11.5px; color: var(--color-fg-subtle);
}
.nostage { font-size: 11px; font-style: italic; color: var(--color-fg-faint); }
.is-empty { font-style: italic; color: var(--color-fg-faint); }
.empty {
    border: 1px dashed var(--color-border-strong); border-radius: 0.6rem;
    padding: 0.9rem 1rem; color: var(--color-fg-muted);
}
.empty strong {
    display: block; font-family: var(--font-mono, monospace);
    font-size: 13px; color: var(--color-fg-subtle);
}
.empty p { margin-top: 0.3rem; font-size: 12px; }
@media (max-width: 768px) {
    .index { display: block; overflow-x: auto; }
}
</style>
