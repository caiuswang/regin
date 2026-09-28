<script setup>
// The tasks of one feature that are running or armed. Rows come from
// the next-step read so each carries its live run and what it waits on; the
// header counts come from the status totals, never from the rows loaded.
import { computed, onMounted, ref } from 'vue'
import api from '../../api'
import TaskStatusIcon from '../tasks/TaskStatusIcon.vue'
import { ago } from '../../utils/pipelineFormat'
import { slicesLink, tasksLink } from '../../utils/featureLinks'

const props = defineProps({
  featureId: { type: Number, required: true },
})

const ORDER = ['running', 'ready', 'blocked']
const HEAD = ['running', 'armed']
const PAGE = 20

const groups = ref([])
const byStatus = ref({})
// Idea slices, counted off the same report the ledger's Slices panel draws,
// so "K not armed" and that panel's "Arm K" cannot disagree.
const notArmed = ref(0)
const loaded = ref(false)
const error = ref('')

onMounted(async () => {
  const q = `feature_id=${props.featureId}`
  try {
    // The heading's counts are garnish on the rows: losing them must not hide the rows.
    const [g, c, s] = await Promise.all([
      api.get(`/tasks?group=next_step&${q}&size=${PAGE}`),
      api.get(`/tasks/counts?${q}`).catch(() => null),
      api.get(`/features/${props.featureId}/slices`).catch(() => null),
    ])
    notArmed.value = (s?.rows || []).filter(r => r.task_status === 'idea').length
    const byKey = new Map((g.groups || []).map(grp => [grp.key, grp]))
    groups.value = ORDER.map(k => byKey.get(k)).filter(Boolean)
    byStatus.value = c?.by_status || {}
  } catch (e) {
    error.value = e?.message || String(e)
  } finally {
    loaded.value = true
  }
})

const heading = computed(() => HEAD
  .filter(s => byStatus.value[s])
  .map(s => `${byStatus.value[s]} ${s}`)
  .join(' · '))
const rows = computed(() => groups.value.flatMap(g => g.rows || []))
const more = computed(() => groups.value.reduce((n, g) => n + (g.more || 0), 0))

function label(t) {
  if (t.status === 'running') return t.live?.started_at ? `running · ${ago(t.live.started_at)}` : 'running'
  if (t.waits_on) return `${t.status} · waits #${t.waits_on.id}`
  return t.status
}
</script>

<template>
  <section class="fa" :data-testid="`feature-active-${featureId}`">
    <h3 class="fa-label" data-testid="feature-active-heading">Active tasks<template v-if="heading"> · {{ heading }}</template></h3>
    <p v-if="notArmed" class="fa-ideas" data-testid="feature-active-ideas">
      {{ notArmed }} not armed ·
      <router-link
        :to="slicesLink(featureId)"
        class="fa-more focus-visible:outline-2 focus-visible:outline-blue-500"
        data-testid="feature-active-arm"
      >Arm →</router-link>
    </p>
    <p v-if="!loaded" class="fa-muted">Loading…</p>
    <p v-else-if="error" class="fa-muted" role="alert">{{ error }}</p>
    <p v-else-if="!rows.length" class="fa-muted">Nothing is running or armed.</p>
    <ul v-else class="fa-list">
      <li v-for="t in rows" :key="t.id" class="fa-item" :data-testid="`feature-active-task-${t.id}`">
        <TaskStatusIcon :status="t.status" :size="13" />
        <span class="fa-id">#{{ t.id }}</span>
        <router-link :to="`/tasks/${t.id}`" class="fa-title focus-visible:outline-2 focus-visible:outline-blue-500">{{ t.title }}</router-link>
        <span class="fa-state" :class="`fa-state--${t.status}`">{{ label(t) }}</span>
      </li>
    </ul>
    <router-link
      v-if="more"
      :to="tasksLink(featureId)"
      class="fa-more focus-visible:outline-2 focus-visible:outline-blue-500"
      data-testid="feature-active-more"
    >{{ more }} more in Tasks →</router-link>
  </section>
</template>

<style scoped>
.fa { min-width: 0; display: flex; flex-direction: column; gap: 0.4rem; }
.fa-label {
    font-size: 10.5px; font-weight: 600; letter-spacing: 0.06em;
    text-transform: uppercase; color: var(--color-fg-faint);
}
.fa-list { display: flex; flex-direction: column; gap: 0.35rem; }
.fa-item {
    display: flex; align-items: center; gap: 0.5rem; min-width: 0;
    border: 1px solid var(--color-border); border-radius: var(--radius-lg);
    background: var(--color-surface); padding: 0.4rem 0.6rem; font-size: 12.5px;
}
.fa-item :deep(svg) { flex: none; }
.fa-id { font-family: var(--font-mono, monospace); font-size: 11.5px; color: var(--color-fg-faint); flex: none; }
.fa-title { flex: 1; min-width: 0; color: var(--color-fg); text-decoration: none; text-wrap: pretty; }
.fa-title:hover { text-decoration: underline; }
.fa-state { flex: none; font-size: 11px; font-weight: 500; }
.fa-state--idea { color: var(--color-fg-faint); }
.fa-state--armed { color: var(--color-info); }
.fa-state--running { color: var(--color-agent); }
.fa-muted { font-size: 12px; color: var(--color-fg-muted); }
.fa-ideas { font-size: 12px; color: var(--color-fg-muted); }
.fa-more { font-size: 12px; color: var(--color-info); text-decoration: none; }
.fa-more:hover { text-decoration: underline; }
</style>
