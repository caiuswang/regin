<script setup>
import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import api from '../api'
import Card from '../components/Card.vue'
import Badge from '../components/Badge.vue'
import MarkdownContent from '../components/MarkdownContent.vue'
import Breadcrumb from '../components/Breadcrumb.vue'

const route = useRoute()
const plan = ref(null)
const mentions = ref({ skills: [] })
const sessions = ref([])
const loading = ref(true)
const error = ref('')

onMounted(async () => {
  try {
    const [p, m, s] = await Promise.all([
      api.get(`/plans/${route.params.filename}`),
      api.get(`/plans/${route.params.filename}/mentions`),
      api.get(`/plan-sessions?plan=${encodeURIComponent(route.params.filename)}`),
    ])
    plan.value = p
    mentions.value = m
    sessions.value = s.items || []
  } catch (e) {
    error.value = e.message || 'Failed to load plan'
  } finally {
    loading.value = false
  }
})

function fmtDate(iso) {
  if (!iso) return '-'
  const d = new Date(iso)
  return d.toLocaleString()
}
</script>

<template>
  <div v-if="loading" class="empty-state">Loading plan…</div>
  <div v-else-if="error" class="empty-state text-rose-600">{{ error }}</div>
  <div v-else>
    <Breadcrumb :items="[
      { label: 'Plans', to: '/plans' },
      { label: plan.title, to: null },
    ]" />

    <header class="page-header">
      <div class="page-header-text">
        <div class="page-eyebrow">Plan</div>
        <h1 class="page-title">{{ plan.title }}</h1>
        <p class="page-subtitle">
          <code class="cell-code">{{ plan.filename }}</code>
          · last updated {{ fmtDate(plan.updated_at) }} · {{ plan.size }} B
        </p>
      </div>
    </header>

    <Card v-if="mentions.skills.length">
      <h2 class="card-header">Skills used by authoring session</h2>
      <div class="flex flex-wrap gap-2">
        <router-link
          v-for="s in mentions.skills"
          :key="s.skill_id"
          :to="`/skills/${s.skill_id}`"
          class="skill-pill focus-visible:outline-2 focus-visible:outline-blue-500"
        >
          <span class="skill-pill-name">{{ s.skill_id }}</span>
          <span v-if="s.last_read_at" class="skill-pill-meta">read {{ fmtDate(s.last_read_at) }}</span>
          <span v-else class="skill-pill-meta is-muted">not read yet</span>
        </router-link>
      </div>
    </Card>

    <Card v-if="sessions.length">
      <h2 class="card-header">Linked sessions</h2>
      <div class="divide-y divide-slate-100">
        <router-link
          v-for="s in sessions" :key="s.id"
          :to="`/trace/sessions/${s.session_id}`"
          class="flex items-center justify-between text-sm py-2 -mx-3 px-3 rounded hover:bg-slate-50 focus-visible:outline-2 focus-visible:outline-blue-500 no-underline text-inherit"
        >
          <div class="flex items-center gap-2">
            <code class="cell-code">{{ s.session_id ? s.session_id.slice(0, 8) : 'unknown' }}…</code>
            <Badge v-if="!s.ended_at" color="green" label="active" />
            <span v-else class="text-slate-400 text-xs">ended</span>
          </div>
          <div class="text-slate-400 text-xs font-mono">
            <span v-if="!s.ended_at">started {{ fmtDate(s.started_at) }}</span>
            <span v-else>{{ fmtDate(s.started_at) }} – {{ fmtDate(s.ended_at) }}</span>
          </div>
        </router-link>
      </div>
    </Card>

    <Card>
      <MarkdownContent :markdown="plan.content" />
    </Card>
  </div>
</template>

<style scoped>
.skill-pill {
    display: inline-flex;
    align-items: center;
    gap: 0.375rem;
    padding: 0.25rem 0.625rem;
    border-radius: 9999px;
    background: var(--color-blue-50);
    color: var(--color-blue-800);
    font-size: 0.75rem;
    text-decoration: none;
    transition: background-color 150ms;
}
.skill-pill:hover { background: var(--color-blue-100); }
.skill-pill-name { font-weight: 500; }
.skill-pill-meta {
    color: rgba(30, 64, 175, 0.7);
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 0.6875rem;
}
.skill-pill-meta.is-muted { opacity: 0.6; }
</style>
