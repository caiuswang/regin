<script setup>
// "Projects" (1a / 2a, scope all): the active projects under the attention
// sort — escapes, then open defects, then activity. Quiet ones are only
// counted; finding or re-sorting projects is the chip picker's job.
import Button from '../ui/Button.vue'
import SegmentBar from './SegmentBar.vue'
import { loopBar, projectActivity } from '../../utils/pipelineFormat'

defineProps({
  panel: { type: Object, required: true },
  compact: { type: Boolean, default: false },
})
const emit = defineEmits(['pick', 'all'])

// Each count is a way to its rows: the same tab, under that project.
const to = (path, id, extra = {}) => ({ path, query: { project: String(id), ...extra } })
const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`
</script>

<template>
  <section class="pl-panel pj" data-pl-part="panel" :class="{ 'pj--compact': compact }" data-testid="projects-panel" aria-labelledby="pj-title">
    <header class="pl-panel__head" data-pl-part="panel-head" data-pl-chapter="1-4">
      <h2 id="pj-title" class="pl-panel__title" data-pl-part="panel-title">Projects</h2>
      <span class="pl-panel__sub" data-pl-part="panel-meta" data-testid="projects-counts">{{ panel.active }} active · {{ panel.quiet }} quiet</span>
      <Button variant="link" size="sm" class="pj-all" data-testid="projects-all" @click="emit('all')">
        All {{ panel.total }} projects →
      </Button>
    </header>
    <p v-if="!panel.rows.length" class="pl-panel__empty" data-pl-part="panel-empty" data-testid="projects-empty">
      All {{ panel.total }} projects quiet
    </p>
    <ul v-else>
      <li v-for="row in panel.rows" :key="row.id" class="pj-row" :data-testid="`projects-row-${row.id}`">
        <Button
          variant="ghost"
          class="pj-pick"
          :data-testid="`projects-pick-${row.id}`"
          @click="emit('pick', String(row.id))"
        >
          <span class="pj-name">{{ row.name }}</span>
          <span class="pj-activity">{{ projectActivity(row) }}</span>
        </Button>
        <template v-if="!compact">
          <SegmentBar class="pj-bar" :parts="loopBar(row.stage_bar)" :height="6" />
          <router-link class="pj-fig pj-link" :to="to('/features', row.id)" :data-testid="`projects-features-${row.id}`">{{ row.features }} features</router-link>
          <router-link class="pj-fig pj-link" :to="to('/tasks', row.id)" :data-testid="`projects-tasks-${row.id}`">{{ row.open_tasks }} open tasks</router-link>
        </template>
        <span class="pj-attention" :data-testid="`projects-attention-${row.id}`">
          <router-link
            v-if="row.escaped" class="pj-link" :to="to('/features', row.id, { stage: 'escaped' })"
            :data-testid="`projects-escaped-${row.id}`"
          >{{ row.escaped }} escaped</router-link><template v-if="row.escaped && row.open_defects"> · </template><router-link
            v-if="row.open_defects" class="pj-link" :to="to('/defects', row.id, { status: 'open' })"
            :data-testid="`projects-defects-${row.id}`"
          >{{ plural(row.open_defects, 'open defect') }}</router-link>
        </span>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.pj .pj-all { margin-left: auto; height: auto; padding: 0; font-size: 12.5px; }
.pj-row {
    display: grid; align-items: center; gap: 1rem;
    grid-template-columns: minmax(8rem, 10rem) minmax(6rem, 1fr) 5.5rem 6.5rem minmax(7rem, auto);
    padding: 0.6rem 1.1rem; border-top: 1px solid var(--color-border-subtle);
}
.pj-row .pj-pick {
    display: flex; flex-direction: column; align-items: flex-start; justify-content: center; gap: 0;
    min-width: 0; height: auto; padding: 0.15rem 0.3rem; margin-left: -0.3rem; text-align: left;
}
.pj-name { font-size: 13.5px; font-weight: 600; color: var(--color-fg); max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pj-pick:hover .pj-name { text-decoration: underline; }
.pj-activity { font-size: 11.5px; font-weight: 400; color: var(--color-fg-subtle); }
.pj-fig { font-size: 12.5px; color: var(--color-fg-muted); white-space: nowrap; text-align: right; font-variant-numeric: tabular-nums; }
.pj-link { color: inherit; text-decoration: none; }
.pj-link:hover { text-decoration: underline; }
.pj-attention { font-size: 12.5px; font-weight: 600; color: var(--color-warning-strong); white-space: nowrap; text-align: right; }

.pj--compact .pj-row { grid-template-columns: minmax(0, 1fr) auto; }
@media (max-width: 1023.98px) {
    .pj:not(.pj--compact) .pj-row { grid-template-columns: minmax(0, 1fr) auto; }
    .pj:not(.pj--compact) .pj-bar { grid-column: 1 / -1; order: 3; }
    .pj:not(.pj--compact) .pj-fig { display: none; }
}
</style>
