<script setup>
import { computed } from 'vue'
import { fmtAgo } from '../utils/traceFormatters'

const props = defineProps({
  rows: { type: Array, default: () => [] },
})

const maxTotal = computed(
  () => props.rows.reduce((m, r) => Math.max(m, r.total || 0), 0) || 1,
)

/**
 * A skill with no prior-window reads has no defined growth rate, so it is
 * labelled rather than shown as an infinite increase; one with no activity
 * in either window gets no label at all, since "0% change" would overstate
 * how much we know.
 */
function trend(row) {
  const now = row.recent || 0
  const before = row.prior || 0
  if (!before && !now) return null
  if (!before) return { label: 'new', tone: 'up' }
  const pct = Math.round(((now - before) / before) * 100)
  if (pct === 0) return { label: 'flat', tone: 'flat' }
  return { label: `${pct > 0 ? '+' : ''}${pct}%`, tone: pct > 0 ? 'up' : 'down' }
}

function sourceParts(row) {
  return [
    { key: 'invoke', n: row.invokes || 0 },
    { key: 'launch', n: row.launches || 0 },
    { key: 'read', n: row.reads || 0 },
  ].filter((p) => p.n > 0)
}
</script>

<template>
  <table v-if="rows.length" class="tbl">
    <thead>
      <tr>
        <th>Skill</th>
        <th class="text-right">Uses</th>
        <th class="hidden md:table-cell">How it was reached</th>
        <th class="text-right hidden sm:table-cell">Sessions</th>
        <th class="text-right">7d</th>
        <th class="text-right">Trend</th>
        <th class="hidden lg:table-cell">Last</th>
      </tr>
    </thead>
    <tbody>
      <tr v-for="r in rows" :key="r.skill_id">
        <td class="skill-cell">
          <router-link
            :to="`/skills/${r.skill_id}`"
            :title="r.skill_id"
            class="text-blue-600 hover:underline focus-visible:outline-2 focus-visible:outline-blue-500"
          ><code class="text-xs">{{ r.skill_id }}</code></router-link>
        </td>
        <td class="text-right tabular-nums">
          <div class="flex items-center justify-end gap-2">
            <span
              class="roi-bar"
              :style="{ width: Math.max(2, Math.round((r.total / maxTotal) * 44)) + 'px' }"
              aria-hidden="true"
            ></span>
            <span>{{ r.total }}</span>
          </div>
        </td>
        <td class="hidden md:table-cell">
          <span class="flex flex-wrap gap-1">
            <span v-for="p in sourceParts(r)" :key="p.key" class="src-chip">
              {{ p.key }} {{ p.n }}
            </span>
          </span>
        </td>
        <td class="text-right tabular-nums hidden sm:table-cell">{{ r.sessions }}</td>
        <td class="text-right tabular-nums">{{ r.recent }}</td>
        <td class="text-right">
          <span v-if="trend(r)" class="trend" :class="`trend-${trend(r).tone}`">
            {{ trend(r).label }}
          </span>
          <span v-else class="text-gray-300">-</span>
        </td>
        <td class="hidden lg:table-cell text-gray-400 text-xs whitespace-nowrap">
          {{ fmtAgo(r.last_seen) }}
        </td>
      </tr>
    </tbody>
  </table>
  <p v-else class="p-4 text-sm text-gray-400">No skill activity yet.</p>
</template>

<style scoped>
/* A skill id is one token — wrapping it broke words mid-character
   ("impeccabl/e"). Truncating instead keeps the id readable AND leaves the
   metric columns on screen at 390px; letting it scroll horizontally pushed
   them out of view, which is the more costly loss. Full id stays available
   via the title attribute. */
.skill-cell {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 9rem;
}
@media (min-width: 640px) {
  .skill-cell { max-width: none; }
}
/* Hidden below sm in CSS rather than via Tailwind's `hidden` class: this
   scoped rule sets `display` and is injected after the utilities, so it
   would win the cascade and the bar would stay visible at 390px. The bar is
   a redundant cue for the adjacent count, so dropping it there buys the
   width the Trend column needs. */
.roi-bar {
  display: none;
  height: 0.5rem;
  border-radius: 9999px;
  background: var(--color-slate-300);
}
@media (min-width: 640px) {
  .roi-bar { display: inline-block; }
}
.src-chip {
  font-size: 0.6875rem;
  color: var(--color-slate-600);
  background: var(--color-slate-100);
  border: 1px solid var(--color-slate-200);
  border-radius: 0.25rem;
  padding: 0 0.3rem;
  white-space: nowrap;
}
.trend {
  font-size: 0.6875rem;
  font-variant-numeric: tabular-nums;
}
/* Direction is carried by the sign in the label; color stays low-saturation
   so a long leaderboard doesn't read as a wall of status. */
.trend-up { color: var(--color-emerald-700); }
.trend-down { color: var(--color-slate-500); }
.trend-flat { color: var(--color-slate-400); }
</style>
