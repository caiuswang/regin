<script setup>
import { ref, onMounted } from 'vue'
import api from '../api'
import Card from '../components/Card.vue'
import Badge from '../components/Badge.vue'

const data = ref(null)
const loading = ref(true)

onMounted(async () => {
  data.value = await api.get('/experiments')
  loading.value = false
})
</script>

<template>
  <div v-if="loading" class="empty-state">Loading experiments…</div>
  <div v-else>
    <header class="page-header">
      <div class="page-header-text">
        <div class="page-eyebrow">Engineering</div>
        <h1 class="page-title">Concealment Experiments</h1>
        <p class="page-subtitle">
          {{ data.total }} experiment{{ data.total !== 1 ? 's' : '' }} defined.
          Each hides H2 sections of a pattern's <code>SKILL.md</code> to measure behavioral impact.
          <router-link to="/trace/triggers" class="text-link">See live traces →</router-link>
        </p>
      </div>
    </header>

    <Card v-if="!data.grouped.length" class="empty-state">
      No experiments yet. Open a <router-link to="/patterns" class="text-link">pattern</router-link> and create one from its detail page.
    </Card>

    <div v-for="[slug, rows] in data.grouped" :key="slug" class="mb-5">
      <Card :no-padding="true">
        <div class="card-group-header">
          <h2 class="card-group-title">
            <router-link :to="`/patterns/${slug}`"
              class="table-link focus-visible:outline-2 focus-visible:outline-blue-500">
              {{ slug }}
            </router-link>
          </h2>
        </div>
        <table class="tbl" style="table-layout:fixed;width:100%">
          <colgroup>
            <col style="width:22%"><col style="width:8%"><col style="width:30%"><col style="width:8%"><col style="width:8%"><col style="width:15%"><col style="width:9%">
          </colgroup>
          <thead>
            <tr>
              <th>Name</th><th>State</th><th>Sections</th>
              <th class="text-right">Checks</th><th class="text-right">Fired</th>
              <th>Created</th><th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="e in rows" :key="e.id" :class="{ 'tbl-row-active': e.active }">
              <td class="truncate">
                <router-link :to="`/experiments/${e.id}`"
                  class="table-link focus-visible:outline-2 focus-visible:outline-blue-500"
                  :title="e.name">{{ e.name }}</router-link>
              </td>
              <td><Badge :color="e.active ? 'green' : 'gray'" :label="e.active ? 'active' : 'idle'" /></td>
              <td class="truncate" :title="e.sections.join(', ')">
                <code v-for="(s, pos) in e.sections" :key="s" class="cell-code">{{ s }}{{ pos < e.sections.length - 1 ? ', ' : '' }}</code>
              </td>
              <td class="text-right font-mono text-xs">{{ e.trigger_total }}</td>
              <td class="text-right">
                <Badge v-if="e.trigger_fired > 0" color="red" :label="String(e.trigger_fired)" />
                <span v-else class="text-slate-400 font-mono text-xs">0</span>
              </td>
              <td class="text-slate-400 text-xs">{{ (e.created_at || '').slice(0, 10) }}</td>
              <td>
                <router-link :to="`/experiments/${e.id}`"
                  class="btn btn-secondary text-xs focus-visible:outline-2 focus-visible:outline-blue-500">
                  View
                </router-link>
              </td>
            </tr>
          </tbody>
        </table>
      </Card>
    </div>
  </div>
</template>

