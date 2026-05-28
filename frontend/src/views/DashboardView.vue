<script setup>
import { ref, onMounted } from 'vue'
import api from '../api'
import StatCard from '../components/StatCard.vue'
import Badge from '../components/Badge.vue'

const data = ref(null)
const loading = ref(true)

onMounted(async () => {
  data.value = await api.get('/dashboard')
  loading.value = false
})
</script>

<template>
  <div v-if="loading" class="empty-state">Loading dashboard…</div>
  <div v-else>
    <header class="page-header">
      <div class="page-header-text">
        <div class="page-eyebrow">Overview</div>
        <h1 class="page-title">Dashboard</h1>
        <p class="page-subtitle">
          {{ data.stats.total_repos }} registered repos ·
          {{ data.stats.total_patterns }} patterns ·
          {{ data.stats.skills.in_sync }} of {{ data.stats.skills.total }} skills in sync
        </p>
      </div>
      <div class="page-actions">
        <router-link
          to="/repos"
          class="btn btn-secondary focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-1"
        >
          Manage repos
        </router-link>
      </div>
    </header>

    <div class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3 mb-8">
      <StatCard href="/repos">
        <div class="stat-label">Repos registered</div>
        <div class="stat-value">{{ data.stats.total_repos }}</div>
      </StatCard>
      <StatCard href="/patterns">
        <div class="stat-label">Pattern documents</div>
        <div class="stat-value">{{ data.stats.total_patterns }}</div>
      </StatCard>
      <StatCard href="/skills">
        <div class="stat-label">Skills deployed</div>
        <div class="stat-value">
          {{ data.stats.skills.in_sync }}<span class="stat-value-suffix">/ {{ data.stats.skills.total }}</span>
        </div>
        <div class="stat-row-badges">
          <Badge v-if="data.stats.skills.drifted > 0" color="yellow" :label="`${data.stats.skills.drifted} out of sync`" />
          <Badge v-if="data.stats.skills.source_only > 0" color="purple" :label="`${data.stats.skills.source_only} not deployed`" />
          <Badge v-if="data.stats.skills.project_only > 0" color="green" :label="`${data.stats.skills.project_only} deployed (project)`" />
        </div>
      </StatCard>
      <StatCard href="/rules">
        <div class="stat-label">Rule-engine rules</div>
        <div class="stat-value">{{ data.stats.rules.total }}</div>
        <div class="stat-row-badges">
          <Badge v-if="data.stats.rules.fired > 0" color="red" :label="`${data.stats.rules.fired} fired`" />
        </div>
      </StatCard>
      <StatCard href="/patterns">
        <div class="stat-label">Tags</div>
        <div class="stat-value">{{ data.stats.total_tags }}</div>
      </StatCard>
    </div>

  </div>
</template>

<style scoped>
.stat-value-suffix {
    font-size: 0.875rem;
    color: #CBD5E1;
    margin-left: 0.25rem;
    font-weight: 500;
}

.stat-row-badges {
    margin-top: 0.5rem;
    display: flex;
    flex-wrap: wrap;
    gap: 0.25rem;
}
</style>
