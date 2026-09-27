<script setup>
// "Needs a decision" (1a): the newest reports nobody has routed. Attribute
// opens the report on the Features triage rail; Decline closes it after a
// confirm. On a phone (2a) the panel is one summary card opening the sheet.
import { computed, ref } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import { useConfirm } from '../../composables/useConfirm'
import { defectRef, monthDay } from '../../utils/pipelineFormat'

const props = defineProps({
  decision: { type: Object, required: true },
  compact: { type: Boolean, default: false },
  featuresTo: { type: Function, required: true },
})
const emit = defineEmits(['changed', 'triage'])

const { confirm } = useConfirm()
const canWrite = computed(() => api.getStoredUser()?.role !== 'viewer')
const newest = computed(() => props.decision.rows[0] || null)
const busy = ref(null)
const refusal = ref('')

async function decline(row) {
  const sure = await confirm(`Decline ${defectRef(row.id)}?`,
    'It leaves the triage queue as "not a bug" and blames no feature.', true)
  if (!sure) return
  busy.value = row.id
  refusal.value = ''
  try {
    const body = await api.post(`/defects/${row.id}/decline`, { reason: null })
    if (body?.ok === false) refusal.value = body.msg || 'the ledger refused'
    else emit('changed')
  } catch (e) {
    refusal.value = e?.message || String(e)
  } finally {
    busy.value = null
  }
}
</script>

<template>
  <section v-if="compact" class="nd-card" data-testid="needs-decision">
    <div class="nd-card__text">
      <strong>{{ decision.waiting }} defect{{ decision.waiting === 1 ? '' : 's' }} need{{ decision.waiting === 1 ? 's' : '' }} a decision</strong>
      <span v-if="newest">Newest: {{ defectRef(newest.id) }} · {{ monthDay(newest.opened_at) }}</span>
    </div>
    <Button v-if="decision.waiting" variant="secondary" class="nd-card__go" data-testid="needs-decision-triage"
            @click="emit('triage')"
    >Triage</Button>
  </section>

  <section v-else class="pl-panel nd" data-testid="needs-decision" aria-labelledby="nd-title">
    <header class="pl-panel__head">
      <h2 id="nd-title" class="pl-panel__title">Needs a decision</h2>
      <span class="pl-panel__sub">Reports nobody has routed</span>
      <router-link :to="featuresTo()" class="pl-panel__link focus-visible:outline-2 focus-visible:outline-blue-500"
                   data-testid="needs-decision-triage"
      >Triage →</router-link>
    </header>
    <p v-if="!decision.rows.length" class="pl-panel__empty" data-testid="needs-decision-empty">
      Nothing waiting — every report has been routed.
    </p>
    <ul v-else>
      <li v-for="row in decision.rows" :key="row.id" class="nd-row" :data-testid="`needs-decision-row-${row.id}`">
        <div class="nd-meta">
          <router-link :to="`/defects/${row.id}`" class="nd-id focus-visible:outline-2 focus-visible:outline-blue-500">
            {{ defectRef(row.id) }}
          </router-link>
          <span>round {{ row.round }}</span>
          <span>·</span>
          <span>{{ monthDay(row.opened_at) }}</span>
          <span class="nd-spacer" />
          <template v-if="canWrite">
            <Button size="sm" variant="secondary" :data-testid="`needs-decision-attribute-${row.id}`"
                    class="nd-act"
                    @click="$router.push(featuresTo(row.id))"
            >Attribute</Button>
            <Button size="sm" variant="ghost" class="nd-act" :disabled="busy === row.id"
                    :data-testid="`needs-decision-decline-${row.id}`" @click="decline(row)"
            >Decline</Button>
          </template>
        </div>
        <p class="nd-symptom">{{ row.symptom }}</p>
      </li>
    </ul>
    <p v-if="refusal" class="pl-panel__refusal" role="alert">{{ refusal }}</p>
  </section>
</template>

<style scoped>
.nd-row { padding: 0.75rem 1.1rem; border-top: 1px solid var(--color-border-subtle); }
.nd-meta { display: flex; align-items: center; gap: 0.4rem; font-size: 12px; color: var(--color-fg-faint); }
.nd-id { font-family: var(--font-mono, monospace); font-weight: 700; color: var(--color-fg); text-decoration: none; }
.nd-id:hover { text-decoration: underline; }
.nd-spacer { flex: 1; }
.nd-meta .nd-act { height: 26px; }
.nd-symptom {
    margin-top: 0.35rem; font-size: 13px; line-height: 1.5; color: var(--color-fg);
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
    overflow-wrap: anywhere;
}
.nd-card {
    display: flex; align-items: center; gap: 0.75rem;
    border: 1px solid var(--color-amber-300); border-radius: var(--radius-xl);
    background: var(--color-warning-soft); padding: 0.9rem 1rem;
}
.nd-card__text { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 0.15rem; font-size: 12.5px; color: var(--color-fg-muted); }
.nd-card__text strong { font-size: 14px; color: var(--color-warning-strong); }
.nd-card .nd-card__go { min-height: 44px; background: var(--color-surface); color: var(--color-fg); }
</style>
