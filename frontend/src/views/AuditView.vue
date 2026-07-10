<script setup>
import { onMounted } from 'vue'
import Card from '../components/Card.vue'
import PageControls from '../components/PageControls.vue'
import { usePage } from '../composables/usePage'

const {
  items: entries, loading,
  page, pageSize, total, pageCount, hasNext, hasPrev,
  load, next, prev, goto, setSize,
} = usePage({ path: '/audit', size: 50 })

onMounted(load)
</script>

<template>
  <div>
    <header class="page-header">
      <div class="page-header-text">
        <div class="page-eyebrow">Observability</div>
        <h1 class="page-title">Audit Log</h1>
        <p class="page-subtitle">Mutations performed via the web dashboard. Read-only.</p>
      </div>
    </header>

    <div v-if="loading" class="empty-state">Loading audit log…</div>
    <Card v-else-if="entries.length === 0 && total === 0" class="empty-state">
      No audit entries yet.
    </Card>

    <Card v-else :no-padding="true">
      <table class="tbl hidden sm:table">
        <thead>
          <tr>
            <th>Time</th>
            <th>User</th>
            <th>Action</th>
            <th>Target</th>
            <th>Detail</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="e in entries" :key="e.id">
            <td class="text-slate-500 whitespace-nowrap text-xs">{{ e.created_at }}</td>
            <td class="font-medium">{{ e.username }}</td>
            <td class="whitespace-nowrap"><span class="cell-code">{{ e.action }}</span></td>
            <td class="font-mono text-xs">{{ e.target }}</td>
            <td class="text-slate-500 text-xs truncate max-w-0 min-w-32" :title="e.detail">{{ e.detail }}</td>
          </tr>
        </tbody>
      </table>
      <ul class="sm:hidden divide-y divide-slate-100">
        <li v-for="e in entries" :key="e.id" class="p-3 text-sm">
          <div class="flex flex-wrap items-center gap-2 mb-1">
            <span class="font-medium">{{ e.username }}</span>
            <span class="cell-code">{{ e.action }}</span>
            <span class="ml-auto text-slate-400 text-xs">{{ e.created_at }}</span>
          </div>
          <div class="font-mono text-xs text-slate-700 break-all">{{ e.target }}</div>
          <div v-if="e.detail" class="mt-1 text-xs text-slate-500 break-words">{{ e.detail }}</div>
        </li>
      </ul>

      <PageControls
        :page="page" :page-count="pageCount"
        :total="total" :size="pageSize"
        :has-next="hasNext" :has-prev="hasPrev" :loading="loading"
        @prev="prev" @next="next" @goto="goto" @set-size="setSize"
      />
    </Card>
  </div>
</template>
