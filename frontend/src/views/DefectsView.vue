<script setup>
// The defect ledger, readable at last — and writable, which is what earns
// the list. A page that could only show eight unattributed defects while the
// only remedy stayed on the CLI would stop one click short of the one action
// owed, which is why `/features` refused to link here until now.
//
// The scope lives in the URL because a shared `?feature=` is a link to a
// predicate. `all` is the default, and that is the repair: unattributed used
// to be, so a defect reported against a feature — every report filed from a
// task page — landed in the one scope this page could not open. It was saved,
// and it was then unfindable from every surface regin has.
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import api from '../api'
import Icon from '../components/ui/Icon.vue'
import DefectRow from '../components/defects/DefectRow.vue'

const route = useRoute()

const page = ref(null)
const loading = ref(true)
const loadError = ref('')

// Read off the stored role, never off a field in the payload — a payload that
// varies by role is a payload two users cannot compare.
const canWrite = computed(() => api.getStoredUser()?.role !== 'viewer')

const feature = computed(() => String(route.query.feature || '').trim())
const fixItem = computed(() => String(route.query.fix_item || '').trim())

const scopeQuery = computed(() => {
  if (feature.value) return `?feature=${encodeURIComponent(feature.value)}`
  if (fixItem.value) return `?fix_item=${encodeURIComponent(fixItem.value)}`
  const scope = String(route.query.scope || '').trim()
  return scope ? `?scope=${encodeURIComponent(scope)}` : ''
})

// Only the two ledger-wide scopes are toggles. A feature or a fix item is a
// scope you arrive in by following a key, and offering it as a button with no
// key to put in it would be a control that cannot be pressed.
const TOGGLES = [
  { scope: 'all', label: 'All', to: { path: '/defects' } },
  { scope: 'unattributed', label: 'Unattributed',
    to: { path: '/defects', query: { scope: 'unattributed' } } },
]

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

// A write can move the row OUT of the scope being read — attributing one on
// the unattributed list is the whole point of that list — so the scope is
// re-read rather than the row patched in place. Splicing the server's row back
// left an attributed defect sitting under "blamed on no feature", still in the
// count, which is the page asserting something it had just disproved. The
// predicate is the server's, and re-reading is how it stays the server's.
async function load() {
  loading.value = true
  loadError.value = ''
  try {
    page.value = await api.get(`/defects${scopeQuery.value}`)
  } catch (e) {
    loadError.value = e?.message || String(e)
  } finally {
    loading.value = false
  }
}
watch(scopeQuery, load, { immediate: true })

</script>

<template>
  <div class="mx-auto max-w-5xl space-y-4 p-4">
    <header class="space-y-1">
      <h1 class="text-lg font-semibold text-slate-800">Defects</h1>
      <p v-if="page" class="max-w-3xl text-xs text-slate-500">
        {{ LEAD[page.scope] }}
      </p>
      <nav v-if="!feature && !fixItem" class="scopes" aria-label="Defect scope">
        <router-link
          v-for="toggle in TOGGLES"
          :key="toggle.scope"
          :to="toggle.to"
          class="scope"
          :class="{ 'is-on': page && page.scope === toggle.scope }"
          :data-testid="`defects-scope-${toggle.scope}`"
        >{{ toggle.label }}</router-link>
      </nav>
      <p v-else-if="feature" class="scoped" data-testid="defects-scoped-to">
        Scoped to <strong>{{ feature }}</strong> ·
        <router-link to="/defects">every defect</router-link>
      </p>
    </header>

    <p v-if="loading" class="text-sm text-slate-500">Loading…</p>
    <p
      v-else-if="loadError"
      class="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700"
      role="alert"
    >{{ loadError }}</p>

    <template v-else-if="page">
      <p class="count">
        <Icon v-if="page.scope === 'unattributed' && page.items.length"
              name="alert-triangle" :size="11" />
        {{ page.items.length }}
        {{ page.items.length === 1 ? 'defect' : 'defects' }}
      </p>

      <div v-if="!page.items.length" class="empty">
        <strong>nothing here</strong>
        <p>{{ EMPTY[page.scope] }}</p>
      </div>

      <ul v-else class="list">
        <DefectRow
          v-for="defect in page.items"
          :key="defect.id"
          :defect="defect"
          :can-write="canWrite"
          @changed="load()"
        />
      </ul>
    </template>
  </div>
</template>

<style scoped>
.scopes { display: flex; gap: 0.35rem; padding-top: 0.15rem; }
.scope {
    border: 1px solid var(--color-border); border-radius: var(--radius-lg);
    padding: 0.15rem 0.5rem; font-size: 11.5px; text-decoration: none;
    color: var(--color-fg-muted); background: var(--color-surface-1);
}
.scope.is-on {
    color: var(--color-fg); border-color: var(--color-fg-faint);
    font-weight: 600;
}
.scoped { font-size: 11.5px; color: var(--color-fg-muted); }
.scoped strong { font-family: var(--font-mono, monospace); }
.count {
    display: inline-flex; align-items: center; gap: 0.3rem;
    font-size: 11.5px; color: var(--color-fg-faint);
    font-variant-numeric: tabular-nums;
}
.list { display: flex; flex-direction: column; gap: 0.6rem; }
.empty {
    border: 1px dashed var(--color-border); border-radius: var(--radius-xl);
    padding: 1.2rem; text-align: center; color: var(--color-fg-muted);
}
.empty strong { display: block; font-size: 13px; color: var(--color-fg); }
.empty p { margin-top: 0.25rem; font-size: 11.5px; }
</style>
