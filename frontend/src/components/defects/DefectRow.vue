<script setup>
// One defect, rendered as a card rather than a table row.
//
// The symptom is the reporter's own wording and is shown in full — no clamp,
// no ellipsis. `regin defects list` truncates at 88 characters because a
// terminal line is finite; a page is not, and the wording is the whole
// evidence a reader needs to decide which feature caused it.
import { ref } from 'vue'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import DefectActions from './DefectActions.vue'
import DefectImages from './DefectImages.vue'
import { shortDate } from '../../utils/ledgerFormat'

const props = defineProps({
  defect: { type: Object, required: true },
  canWrite: { type: Boolean, default: false },
  // Set where the list is already scoped to one feature: every row would
  // otherwise repeat the title the page is headed by.
  hideFeature: { type: Boolean, default: false },
  // A row in a list of rows (the feature page's ledger grammar) rather than
  // a free-standing card.
  flat: { type: Boolean, default: false },
  // A list row: two lines at rest (the head, then the symptom's first line),
  // with the full wording, evidence and actions behind a per-row disclosure.
  // The permalink and the expanded row still carry the symptom unclamped.
  collapsible: { type: Boolean, default: false },
})
defineEmits(['changed'])

const open = ref(!props.collapsible)
</script>

<template>
  <li
    :class="[flat ? 'ledger-row defect-flat' : 'card', { 'is-collapsed': !open }]"
    :data-testid="`defect-row-${defect.id}`"
  >
    <div class="head">
      <!-- The id is the row's address. A list is a predicate plus a page
           position, and neither survives being sent to someone else; a
           defect that cannot be linked to can only be pointed at by
           describing it. -->
      <router-link class="ledger-id id" :to="`/defects/${defect.id}`"
                   :data-testid="`defect-permalink-${defect.id}`"
      >#{{ defect.id }}</router-link>
      <span class="ledger-tag"
            :class="defect.status === 'open' ? 'ledger-tag-amber' : 'ledger-tag-green'"
      >{{ defect.status }}</span>
      <span class="ledger-tag">round {{ defect.round }}</span>
      <!-- How it left triage, and only when it did. Separate from `status`
           because a declined report and a fixed bug are both closed, and only
           one of them was ever looked at. -->
      <span v-if="defect.triage_state" class="ledger-tag"
            :data-testid="`defect-triage-${defect.id}`">{{ defect.triage_state }}</span>
      <template v-if="!hideFeature">
        <router-link v-if="defect.feature_id" class="key"
                     :to="`/features/${defect.feature_id}`">
          {{ defect.feature_title || `#${defect.feature_id}` }}
        </router-link>
        <span v-else class="unblamed">unattributed</span>
      </template>
      <span class="spacer" />
      <span class="when" :title="defect.opened_at">{{ shortDate(defect.opened_at) }}</span>
      <Button
        v-if="collapsible"
        variant="ghost"
        size="sm"
        :aria-expanded="open"
        :aria-label="`${open ? 'Hide' : 'Show'} details and actions for defect #${defect.id}`"
        :data-testid="`defect-toggle-${defect.id}`"
        @click="open = !open"
      >
        <Icon :name="open ? 'chevron-down' : 'chevron-right'" :size="13" />
        {{ open ? 'Less' : 'Details' }}
      </Button>
    </div>

    <p class="symptom" :class="{ 'symptom-clamped': !open }"
       :title="open ? undefined : defect.symptom">{{ defect.symptom }}</p>

    <template v-if="open">

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
      <template v-if="defect.triage_reason">
        <dt>declined because</dt>
        <dd>{{ defect.triage_reason }}</dd>
      </template>
      <template v-if="defect.fix_state">
        <dt>fix state</dt>
        <dd class="mono">{{ defect.fix_state }}</dd>
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
    </template>
  </li>
</template>

<style scoped>
.card {
    display: flex; flex-direction: column; gap: 0.5rem;
    border: 1px solid var(--color-border); border-radius: var(--radius-xl);
    background: var(--color-surface-1); padding: 0.7rem 0.9rem;
}
.defect-flat { display: flex; flex-direction: column; gap: 0.35rem; }
.head {
    display: flex; flex-wrap: wrap; align-items: center; gap: 0.5rem;
    font-size: 11.5px; color: var(--color-fg-faint);
}
.card .id { font-weight: 600; color: var(--color-fg); }
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
.symptom-clamped {
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.card.is-collapsed { gap: 0.25rem; padding: 0.5rem 0.9rem; }
/* Two lines at rest on any width: the head never wraps — the feature title
   truncates and, on a phone, the date gives way to the disclosure. */
.is-collapsed .head { flex-wrap: nowrap; min-width: 0; }
.is-collapsed .head > * { flex-shrink: 0; }
.is-collapsed .key, .is-collapsed .unblamed {
    flex-shrink: 1; min-width: 0;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
@media (max-width: 639px) {
    .is-collapsed .when { display: none; }
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
