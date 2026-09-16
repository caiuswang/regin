<script setup>
// What you are looking at, when you arrived here by following a feature's task
// count. A scope with no banner is a filtered list wearing an unfiltered
// heading: the reader sees four tasks under the word "Tasks" with no way to
// know the rest of the backlog is withheld, and no way back out.
//
// Modelled on `DefectsScopeBanner`, which answers the same question one tab
// over. The id is not resolved to a title here: a task row carries
// `feature_id` and no name, and `features.title` is the authority for what a
// feature is called — so the name is one click away rather than invented or
// paid for with a ledger build per arrival.
defineProps({
  featureId: { type: String, default: '' },
})
</script>

<template>
  <p v-if="featureId" class="scoped" data-testid="tasks-scoped-to">
    Scoped to the steps of
    <router-link :to="`/features/${featureId}`"
    ><strong>feature #{{ featureId }}</strong></router-link> ·
    <router-link to="/tasks">every task</router-link>
  </p>
</template>

<style scoped>
.scoped { font-size: 11.5px; color: var(--color-fg-muted); }
.scoped strong { font-family: var(--font-mono, monospace); }
</style>
