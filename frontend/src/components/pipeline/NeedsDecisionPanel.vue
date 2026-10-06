<script setup>
// "Needs a decision" (1a): the newest reports nobody has routed. Each row's one
// action is Attribute, which opens the report on the Features triage rail —
// the rail owns Decline and the other verbs. On a phone (2a) the panel is one
// summary card opening the triage sheet.
import { computed } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import { defectRef, monthDay, reopenedLabel } from '../../utils/pipelineFormat'

const props = defineProps({
  decision: { type: Object, required: true },
  compact: { type: Boolean, default: false },
  featuresTo: { type: Function, required: true },
})
const emit = defineEmits(['triage'])

const canWrite = computed(() => api.getStoredUser()?.role !== 'viewer')
const newest = computed(() => props.decision.rows[0] || null)
</script>

<template>
  <section v-if="compact" class="nd-card" data-pl-part="panel" data-testid="needs-decision">
    <div class="nd-card__text">
      <strong>{{ decision.waiting }} defect{{ decision.waiting === 1 ? '' : 's' }} need{{ decision.waiting === 1 ? 's' : '' }} a decision<template v-if="decision.all_projects"> · all projects</template></strong>
      <span v-if="newest">Newest: {{ defectRef(newest.id) }} · {{ monthDay(newest.opened_at) }}</span>
    </div>
    <Button v-if="decision.waiting" variant="secondary" class="nd-card__go" data-pl-part="key" data-testid="needs-decision-triage"
            @click="emit('triage')"
    >Triage</Button>
  </section>

  <section v-else class="pl-panel nd" data-pl-part="panel" data-testid="needs-decision" aria-labelledby="nd-title">
    <header class="pl-panel__head" data-pl-part="panel-head">
      <h2 id="nd-title" class="pl-panel__title" data-pl-part="panel-title" data-pl-chapter="1-3">Needs a decision</h2>
      <span
        class="pl-panel__sub" data-pl-part="panel-meta" data-pl-label="waiting"
        :data-tone="decision.waiting ? 'warn' : undefined" data-testid="needs-decision-count"
      >{{ decision.waiting }}</span>
      <span v-if="decision.all_projects" class="pl-panel__sub" data-pl-part="panel-meta" data-testid="needs-decision-all-projects">· all projects</span>
      <router-link :to="featuresTo()" class="pl-panel__link focus-visible:outline-2 focus-visible:outline-blue-500" data-pl-part="panel-link"
                   data-testid="needs-decision-triage"
      >Triage all →</router-link>
    </header>
    <p v-if="!decision.rows.length" class="pl-panel__empty" data-pl-part="panel-empty" data-testid="needs-decision-empty">
      Nothing waiting.
    </p>
    <ul v-else>
      <li v-for="row in decision.rows" :key="row.id" class="nd-row" data-pl-part="row" :data-testid="`needs-decision-row-${row.id}`">
        <router-link :to="`/defects/${row.id}`" class="nd-id focus-visible:outline-2 focus-visible:outline-blue-500" data-pl-part="row-id">
          {{ defectRef(row.id) }}
        </router-link>
        <p class="nd-symptom" :title="row.symptom" data-testid="needs-decision-symptom">{{ row.symptom }}</p>
        <span class="nd-meta">{{ [reopenedLabel(row.round), monthDay(row.opened_at)].filter(Boolean).join(' · ') }}</span>
        <Button v-if="canWrite" size="sm" variant="secondary" class="nd-act" data-pl-part="key"
                :data-testid="`needs-decision-attribute-${row.id}`"
                @click="$router.push(featuresTo(row.id))"
        >Attribute</Button>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.nd-row {
    display: grid; grid-template-columns: 3.5rem minmax(0, 1fr) auto; column-gap: 0.75rem; row-gap: 0.15rem;
    align-items: center; padding: 0.6rem 1.1rem; border-top: 1px solid var(--color-border-subtle);
}
.nd-id {
    grid-row: 1 / span 2; align-self: start; padding-top: 1px;
    font-size: 13px; font-weight: 600; color: var(--color-fg); text-decoration: none; white-space: nowrap;
}
.nd-id:hover { text-decoration: underline; }
.nd-symptom {
    grid-column: 2; min-width: 0; font-size: 13px; color: var(--color-fg);
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.nd-meta { grid-column: 2; font-size: 11.5px; color: var(--color-fg-faint); }
.nd-row .nd-act {
    grid-column: 3; grid-row: 1 / span 2; height: 26px; color: var(--color-fg-muted);
    background: var(--color-surface); border-color: var(--color-border);
}
.nd-card {
    display: flex; align-items: center; gap: 0.75rem;
    border: 1px solid var(--color-border); border-radius: var(--radius-xl);
    background: var(--color-surface); padding: 0.9rem 1rem;
}
.nd-card__text { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 0.15rem; font-size: 12.5px; color: var(--color-fg-muted); }
.nd-card__text strong { font-size: 14px; color: var(--color-fg); }
.nd-card .nd-card__go { min-height: 44px; }
</style>
