<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api from '../api'
import Card from '../components/Card.vue'
import Badge from '../components/Badge.vue'

const route = useRoute()
const router = useRouter()
const data = ref(null)
const loading = ref(true)

async function load() {
  loading.value = true
  const params = new URLSearchParams()
  if (route.query.endpoint) params.set('endpoint', route.query.endpoint)
  if (route.query.gave_up) params.set('gave_up', route.query.gave_up)
  params.set('limit', '200')
  data.value = await api.get('/ingest-errors?' + params.toString())
  loading.value = false
}

onMounted(load)
watch(() => route.query, load)

function filterBy(key, value) {
  const q = { ...route.query }
  if (value) { q[key] = value } else { delete q[key] }
  router.push({ query: q })
}

function clearFilters() {
  router.push({ query: {} })
}

function fmtDate(iso) {
  if (!iso) return '-'
  try {
    return new Date(iso).toLocaleString()
  } catch {
    return iso
  }
}

const totals = computed(() => {
  const g = data.value?.by_gave_up || { true: 0, false: 0 }
  return { dropped: g.true || 0, retried: g.false || 0 }
})
</script>

<template>
  <div v-if="loading" class="empty-state">Loading ingest errors…</div>
  <div v-else>
    <p class="page-subtitle mb-4">
      Failures logged by the hook-plugin retry loop (<code class="cell-code">{{ data.path }}</code>).
      <strong>{{ totals.dropped }}</strong> dropped after retries exhausted,
      <strong>{{ totals.retried }}</strong> retried and recovered.
    </p>

    <div class="toolbar mb-5">
      <span class="toolbar-label">Filter</span>
      <button type="button"
        class="filter-chip focus-visible:outline-2 focus-visible:outline-blue-500"
        :class="{ active: !route.query.endpoint && !route.query.gave_up }"
        @click="clearFilters">All</button>
      <button type="button"
        class="filter-chip focus-visible:outline-2 focus-visible:outline-blue-500"
        :class="{ active: route.query.gave_up === 'true' }"
        @click="filterBy('gave_up', 'true')">Dropped</button>
      <button type="button"
        class="filter-chip focus-visible:outline-2 focus-visible:outline-blue-500"
        :class="{ active: route.query.gave_up === 'false' }"
        @click="filterBy('gave_up', 'false')">Retried</button>
      <Badge v-if="route.query.endpoint" color="blue">
        endpoint = {{ route.query.endpoint }}
        <button type="button" class="badge-x focus-visible:outline-2 focus-visible:outline-blue-500"
          aria-label="Clear endpoint filter" @click="filterBy('endpoint', null)">&times;</button>
      </Badge>
    </div>

    <div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
      <Card :no-padding="true">
        <div class="px-4 py-2.5 bg-gray-50 border-b border-gray-200 font-semibold text-sm">
          By endpoint
        </div>
        <table v-if="Object.keys(data.by_endpoint).length" class="tbl">
          <thead><tr><th>Endpoint</th><th class="text-right">Errors</th></tr></thead>
          <tbody>
            <tr v-for="(n, ep) in data.by_endpoint" :key="ep"
                :class="{ 'bg-blue-50': route.query.endpoint === ep }">
              <td>
                <span class="text-blue-600 hover:underline cursor-pointer"
                      @click="filterBy('endpoint', ep)">
                  <code class="text-xs">{{ ep }}</code>
                </span>
              </td>
              <td class="text-right">{{ n }}</td>
            </tr>
          </tbody>
        </table>
        <p v-else class="p-4 text-sm text-gray-400">No errors recorded.</p>
      </Card>

      <Card :no-padding="true">
        <div class="px-4 py-2.5 bg-gray-50 border-b border-gray-200 font-semibold text-sm">
          By error type
        </div>
        <table v-if="Object.keys(data.by_error_type).length" class="tbl">
          <thead><tr><th>Error type</th><th class="text-right">Count</th></tr></thead>
          <tbody>
            <tr v-for="(n, t) in data.by_error_type" :key="t">
              <td><code class="text-xs">{{ t }}</code></td>
              <td class="text-right">{{ n }}</td>
            </tr>
          </tbody>
        </table>
        <p v-else class="p-4 text-sm text-gray-400">No errors recorded.</p>
      </Card>
    </div>

    <Card :no-padding="true">
      <div class="px-4 py-2.5 bg-gray-50 border-b border-gray-200 font-semibold text-sm">
        Recent entries
        <span class="text-gray-400 font-normal">({{ data.rows.length }})</span>
      </div>
      <table v-if="data.rows.length" class="tbl hidden sm:table">
        <thead>
          <tr>
            <th>When</th>
            <th>Endpoint</th>
            <th>Error type</th>
            <th class="text-right">Attempt</th>
            <th>Status</th>
            <th>Message</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(row, pos) in data.rows" :key="pos">
            <td class="text-gray-500 text-xs whitespace-nowrap">{{ fmtDate(row.timestamp) }}</td>
            <td>
              <span class="text-blue-600 hover:underline cursor-pointer"
                    @click="filterBy('endpoint', row.endpoint)">
                <code class="text-xs">{{ row.endpoint }}</code>
              </span>
            </td>
            <td><code class="text-xs">{{ row.error_type }}</code></td>
            <td class="text-right text-xs text-gray-500">
              {{ row.attempt }} / {{ row.max_attempts }}
            </td>
            <td>
              <span v-if="row.gave_up"
                    class="inline-block rounded bg-red-100 text-red-800 text-[10px] font-semibold px-1.5 py-0.5 uppercase tracking-wide">
                dropped
              </span>
              <span v-else
                    class="inline-block rounded bg-amber-100 text-amber-800 text-[10px] font-semibold px-1.5 py-0.5 uppercase tracking-wide">
                retried
              </span>
            </td>
            <td class="text-xs text-gray-600 break-all">{{ row.error }}</td>
          </tr>
        </tbody>
      </table>
      <ul v-if="data.rows.length" class="sm:hidden divide-y divide-gray-200">
        <li v-for="(row, pos) in data.rows" :key="pos" class="p-3 text-sm">
          <div class="flex flex-wrap items-center gap-2 mb-1">
            <span v-if="row.gave_up" class="inline-block rounded bg-red-100 text-red-800 text-[10px] font-semibold px-1.5 py-0.5 uppercase tracking-wide">dropped</span>
            <span v-else class="inline-block rounded bg-amber-100 text-amber-800 text-[10px] font-semibold px-1.5 py-0.5 uppercase tracking-wide">retried</span>
            <code class="text-xs">{{ row.error_type }}</code>
            <span class="ml-auto text-gray-400 text-xs">{{ fmtDate(row.timestamp) }}</span>
          </div>
          <div class="text-xs text-gray-600">
            <span class="text-blue-600 hover:underline cursor-pointer" @click="filterBy('endpoint', row.endpoint)">
              <code>{{ row.endpoint }}</code>
            </span>
            <span class="ml-2 text-gray-400">attempt {{ row.attempt }}/{{ row.max_attempts }}</span>
          </div>
          <div v-if="row.error" class="mt-1 text-xs text-gray-600 break-all">{{ row.error }}</div>
        </li>
      </ul>
      <p v-if="!data.rows.length" class="p-4 text-sm text-gray-400">
        No ingest errors recorded — all hooks have landed cleanly.
      </p>
    </Card>
  </div>
</template>
