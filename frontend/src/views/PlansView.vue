<script setup>
import { ref, onMounted, watch } from 'vue'
import api from '../api'
import Card from '../components/Card.vue'
import Select from '../components/ui/Select.vue'

const plans = ref([])
const loading = ref(true)

// Repo filter — a plan file matches a repo when one of its plan-sessions
// ran in a session tagged with that repo. Persisted across visits.
const REPO_KEY = 'regin_plans_repo'
const repoFilter = ref(localStorage.getItem(REPO_KEY) || 'all')
const repoOptions = ref([])

async function loadRepoOptions() {
  try {
    const res = await api.get('/repos')
    repoOptions.value = (res.repos || []).map(r => r.name)
    if (repoFilter.value !== 'all' && !repoOptions.value.includes(repoFilter.value)) {
      repoFilter.value = 'all'
    }
  } catch {
    repoOptions.value = []
  }
}

async function loadPlans() {
  loading.value = true
  const q = repoFilter.value !== 'all' ? `?repo=${encodeURIComponent(repoFilter.value)}` : ''
  const data = await api.get(`/plans${q}`)
  plans.value = data.plans || []
  loading.value = false
}

onMounted(() => {
  loadRepoOptions()
  loadPlans()
})

watch(repoFilter, (v) => {
  localStorage.setItem(REPO_KEY, v)
  loadPlans()
})

function fmtDate(iso) {
  if (!iso) return '-'
  const d = new Date(iso)
  return d.toLocaleString()
}
</script>

<template>
  <div>
    <header class="page-header">
      <div class="page-header-text">
        <div class="page-eyebrow">Engineering</div>
        <h1 class="page-title">Plans</h1>
        <p class="page-subtitle">Saved Claude Code plans from <code>~/.claude/plans/</code>. {{ plans.length }} plan{{ plans.length === 1 ? '' : 's' }}<span v-if="repoFilter !== 'all'"> in <code>{{ repoFilter }}</code></span>.</p>
      </div>
    </header>

    <div class="mb-4 flex items-center gap-2">
      <label for="plans-repo-filter" class="text-xs font-medium text-slate-500 uppercase tracking-wide">Repo</label>
      <span class="inline-block w-44">
        <Select
          id="plans-repo-filter"
          v-model="repoFilter"
          block
          aria-label="Filter plans by repo"
          :options="[{ value: 'all', label: 'All repos' }, ...repoOptions.map(n => ({ value: n, label: n }))]"
        />
      </span>
    </div>

    <Card :no-padding="true">
      <div v-if="loading" class="empty-state">Loading plans…</div>
      <div v-else class="overflow-x-auto">
      <table class="tbl">
        <thead>
          <tr>
            <th>Title</th>
            <th class="hidden md:table-cell">Filename</th>
            <th>Repo</th>
            <th>Updated</th>
            <th class="hidden md:table-cell text-right">Size</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="p in plans" :key="p.filename">
            <td>
              <router-link :to="`/plans/${p.filename}`"
                class="table-link focus-visible:outline-2 focus-visible:outline-blue-500">
                {{ p.title }}
              </router-link>
            </td>
            <td class="hidden md:table-cell"><code class="cell-code">{{ p.filename }}</code></td>
            <td class="whitespace-nowrap">
              <span
                v-for="name in (p.repos || [])"
                :key="name"
                class="mr-1 inline-block rounded border border-slate-300 bg-slate-50 text-slate-700 text-[11px] px-1.5 py-0.5"
              >{{ name }}</span>
              <span v-if="!p.repos || !p.repos.length" class="text-gray-300 text-xs" title="No linked plan-session in a registered repo">-</span>
            </td>
            <td class="text-slate-500">{{ fmtDate(p.updated_at) }}</td>
            <td class="hidden md:table-cell text-right text-slate-500 font-mono text-xs">{{ p.size }} B</td>
          </tr>
          <tr v-if="!plans.length">
            <td colspan="5" class="empty-row">
              No plan files found in <code>~/.claude/plans/</code>.
            </td>
          </tr>
        </tbody>
      </table>
      </div>
    </Card>
  </div>
</template>

<style scoped>
.empty-row {
    color: var(--color-slate-400);
    text-align: center;
    padding: 1.5rem;
    font-size: 0.875rem;
}
</style>
