<script setup>
// One defect, rendered as a card rather than a table row.
//
// The symptom is the reporter's own wording and is shown in full — no clamp,
// no ellipsis. `regin defects list` truncates at 88 characters because a
// terminal line is finite; a page is not, and the wording is the whole
// evidence a reader needs to decide which feature caused it.
import Badge from '../Badge.vue'
import DefectActions from './DefectActions.vue'
import DefectImages from './DefectImages.vue'
import { shortDate } from '../../utils/ledgerFormat'

defineProps({
  defect: { type: Object, required: true },
  canWrite: { type: Boolean, default: false },
})
defineEmits(['changed'])
</script>

<template>
  <li class="card">
    <div class="head">
      <!-- The id is the row's address. A list is a predicate plus a page
           position, and neither survives being sent to someone else; a
           defect that cannot be linked to can only be pointed at by
           describing it. -->
      <router-link class="id" :to="`/defects/${defect.id}`"
                   :data-testid="`defect-permalink-${defect.id}`"
      >#{{ defect.id }}</router-link>
      <Badge :color="defect.status === 'open' ? 'amber' : 'green'"
             :label="defect.status" />
      <span class="round">round {{ defect.round }}</span>
      <router-link v-if="defect.feature_id" class="key"
                   :to="`/features/${defect.feature_id}`">
        {{ defect.feature_title || `#${defect.feature_id}` }}
      </router-link>
      <span v-else class="unblamed">unattributed</span>
      <span class="spacer" />
      <span class="when" :title="defect.opened_at">{{ shortDate(defect.opened_at) }}</span>
    </div>

    <p class="symptom">{{ defect.symptom }}</p>

    <!-- Only when the row really carries evidence: binding costs one
         authenticated fetch per picture, and a list of reports that mostly
         have none should pay nothing for them. -->
    <DefectImages v-if="defect.images?.length" :defect="defect" />

    <dl class="facts">
      <template v-if="defect.suspect_paths.length">
        <dt>suspect paths</dt>
        <dd class="mono">{{ defect.suspect_paths.join(' · ') }}</dd>
      </template>
      <template v-if="defect.missing_check">
        <dt>missing check</dt>
        <dd>{{ defect.missing_check }}</dd>
      </template>
      <template v-if="defect.fix_task_id">
        <dt>fix item</dt>
        <dd>
          <router-link class="mono" :to="`/tasks/${defect.fix_task_id}`"
          >task #{{ defect.fix_task_id }}</router-link>
        </dd>
      </template>
      <!-- The event blame was assigned against. An escape is `shipped` — one
           comparison against an immutable row, so there is no "we never
           looked" reading to render as a gap. -->
      <template v-if="defect.blamed_event_kind">
        <dt>blamed against</dt>
        <dd class="mono">{{ defect.blamed_event_kind }}</dd>
      </template>
    </dl>

    <DefectActions :defect="defect" :can-write="canWrite"
                   @changed="(msg) => $emit('changed', msg)" />
  </li>
</template>

<style scoped>
.card {
    display: flex; flex-direction: column; gap: 0.5rem;
    border: 1px solid var(--color-border); border-radius: var(--radius-xl);
    background: var(--color-surface-1); padding: 0.7rem 0.9rem;
}
.head {
    display: flex; flex-wrap: wrap; align-items: center; gap: 0.5rem;
    font-size: 11.5px; color: var(--color-fg-faint);
}
.id {
    font-family: var(--font-mono, monospace); font-weight: 600;
    color: var(--color-fg); text-decoration: none;
}
.id:hover { text-decoration: underline; }
.round { font-variant-numeric: tabular-nums; }
.key {
    font-family: var(--font-mono, monospace); font-weight: 600;
    color: var(--color-primary); text-decoration: none;
}
.key:hover { text-decoration: underline; }
.unblamed { color: var(--color-amber-800); }
.spacer { flex: 1 1 auto; }
.when { font-variant-numeric: tabular-nums; }
.symptom {
    max-width: 60rem; font-size: 13px; line-height: 1.5;
    color: var(--color-fg); white-space: pre-wrap; overflow-wrap: anywhere;
}
.facts {
    display: grid; grid-template-columns: max-content 1fr;
    gap: 0.15rem 0.7rem; font-size: 11.5px;
}
.facts dt {
    font-size: 10.5px; letter-spacing: 0.06em; text-transform: uppercase;
    color: var(--color-fg-faint);
}
.facts dd { margin: 0; color: var(--color-fg-muted); overflow-wrap: anywhere; }
.mono { font-family: var(--font-mono, monospace); }
</style>
