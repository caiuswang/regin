<script setup>
// R2's `next_step`, as the server computed it. Kind `none` draws nothing: a
// shipped feature with no escape has no step owed.
defineProps({
  step: { type: Object, default: null },
})
</script>

<template>
  <router-link
    v-if="step && step.kind !== 'none' && step.target"
    :to="step.target"
    class="fns focus-visible:outline-2 focus-visible:outline-blue-500"
    :data-kind="step.kind"
    data-testid="feature-next-step"
  >Next: {{ step.label }}</router-link>
</template>

<style scoped>
.fns {
    font-size: 11.5px; color: var(--color-fg-muted); text-decoration: none;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.fns:hover { color: var(--color-fg); text-decoration: underline; }
.fns[data-kind="reopen"], .fns[data-kind="resolve_defects"] {
    color: var(--color-warning-strong);
}
</style>
