<script setup>
import { computed } from 'vue'
import { verdictColor } from '../constants/gradeVerdicts'

const props = defineProps({
  grade: { type: Object, required: true },
})

// scoreboard is a per-criterion counter map, e.g.
// { groundedness: { grounded: 2, total: 2 }, coverage: { covered: 1, total: 1 } }.
// Render each as a "<positive>/<total>" stat chip, tolerant of shapes that
// don't carry a total.
const stats = computed(() =>
  Object.entries(props.grade.scoreboard || {}).map(([name, counts]) => ({
    name: name.replace(/_/g, ' '),
    value: formatStat(counts),
  })),
)

function formatStat(counts) {
  if (!counts || typeof counts !== 'object') return String(counts ?? '')
  if ('total' in counts) {
    const positive = Object.entries(counts).find(([k]) => k !== 'total')
    return positive ? `${positive[1]}/${counts.total}` : String(counts.total)
  }
  return Object.entries(counts).map(([k, v]) => `${k} ${v}`).join(' · ')
}

// Drop the leading scoreboard summary line (already shown as chips) and keep
// the explanatory bullets.
const reportLines = computed(() =>
  (props.grade.report || '')
    .split('\n')
    .map(l => l.trimEnd())
    .filter(Boolean),
)
</script>

<template>
  <div class="grade-report">
    <div class="grade-report-head">
      <span :class="['verdict-pill', `verdict-${verdictColor[grade.verdict] || 'gray'}`]">
        {{ grade.verdict }}
      </span>
      <span class="grade-report-axis">{{ grade.axis }}</span>
      <span class="grade-report-meta">
        <span class="meta-chip">tier {{ grade.tier }}</span>
        <span class="meta-chip">judge {{ grade.judge || 'mechanical' }}</span>
        <span v-if="grade.rubric_version" class="meta-chip">rubric {{ grade.rubric_version }}</span>
        <span class="meta-chip meta-chip-muted">{{ grade.created_at }}</span>
      </span>
    </div>

    <div v-if="stats.length" class="stat-row">
      <span v-for="s in stats" :key="s.name" class="stat-chip">
        <span class="stat-name">{{ s.name }}</span>
        <span class="stat-value">{{ s.value }}</span>
      </span>
    </div>

    <ul v-if="reportLines.length" class="report-lines">
      <li v-for="(line, i) in reportLines" :key="i">{{ line }}</li>
    </ul>
  </div>
</template>

<style scoped>
.grade-report {
  margin-top: 0.5rem;
  padding: 0.85rem 0;
}
.grade-report + .grade-report { border-top: 1px solid var(--color-slate-200); }
.grade-report-head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.5rem;
}
.verdict-pill {
  font-size: 0.72rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  padding: 0.15rem 0.55rem;
  border-radius: 999px;
}
.verdict-green { background: var(--color-green-100); color: var(--color-green-700); }
.verdict-yellow { background: var(--color-yellow-100); color: var(--color-yellow-700); }
.verdict-red { background: var(--color-red-100); color: var(--color-red-700); }
.verdict-gray { background: var(--color-slate-100); color: var(--color-slate-600); }
.grade-report-axis {
  font-weight: 600;
  font-size: 0.9rem;
  text-transform: capitalize;
}
.grade-report-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
  margin-left: auto;
}
.meta-chip {
  font-size: 0.7rem;
  color: var(--color-slate-600);
  background: var(--color-slate-100);
  padding: 0.1rem 0.45rem;
  border-radius: 4px;
}
.meta-chip-muted { color: var(--color-slate-400); background: transparent; }
.stat-row {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
  margin-top: 0.6rem;
}
.stat-chip {
  display: inline-flex;
  align-items: baseline;
  gap: 0.35rem;
  border: 1px solid var(--color-slate-200);
  border-radius: 6px;
  padding: 0.2rem 0.5rem;
}
.stat-name { font-size: 0.72rem; color: var(--color-slate-500); text-transform: capitalize; }
.stat-value { font-size: 0.82rem; font-weight: 600; color: var(--color-slate-800); }
.report-lines {
  margin-top: 0.6rem;
  padding-left: 1rem;
  list-style: disc;
}
.report-lines li {
  font-size: 0.8rem;
  line-height: 1.5;
  color: var(--color-slate-700);
  margin-bottom: 0.15rem;
}
</style>
