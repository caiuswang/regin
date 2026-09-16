<script setup>
// One defect, rendered as a card rather than a table row.
//
// The symptom is the reporter's own wording and is shown in full — no clamp,
// no ellipsis. `regin defects list` truncates at 88 characters because a
// terminal line is finite; a page is not, and the wording is the whole
// evidence a reader needs to decide which feature caused it.
import Badge from '../Badge.vue'
import DefectActions from './DefectActions.vue'

defineProps({
  defect: { type: Object, required: true },
  canWrite: { type: Boolean, default: false },
})
defineEmits(['changed'])
</script>

<template>
  <li class="card">
    <div class="head">
      <span class="id">#{{ defect.id }}</span>
      <Badge :color="defect.status === 'open' ? 'amber' : 'green'"
             :label="defect.status" />
      <span class="round">round {{ defect.round }}</span>
      <router-link v-if="defect.feature_key" class="key"
                   :to="`/features/${defect.feature_key}`">
        {{ defect.feature_key }}
      </router-link>
      <span v-else class="unblamed">unattributed</span>
      <span class="spacer" />
      <span class="when">{{ defect.opened_at }}</span>
    </div>

    <p class="symptom">{{ defect.symptom }}</p>

    <dl class="facts">
      <template v-if="defect.suspect_paths.length">
        <dt>suspect paths</dt>
        <dd class="mono">{{ defect.suspect_paths.join(' · ') }}</dd>
      </template>
      <template v-if="defect.missing_check">
        <dt>missing check</dt>
        <dd>{{ defect.missing_check }}</dd>
      </template>
      <template v-if="defect.fix_external_key">
        <dt>fix item</dt>
        <dd class="mono">{{ defect.fix_external_key }}</dd>
      </template>
      <template v-if="defect.blamed_against_state">
        <dt>blamed against</dt>
        <dd class="mono">{{ defect.blamed_against_state }}</dd>
      </template>
    </dl>

    <DefectActions :defect="defect" :can-write="canWrite"
                   @changed="(row) => $emit('changed', row)" />
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
    color: var(--color-fg);
}
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
