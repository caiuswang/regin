<script setup>
// The Projects panel before any project exists: where to add one, or — for a
// viewer, who cannot — who can.
import { ref } from 'vue'
import Button from '../ui/Button.vue'
import AddProjectDialog from './AddProjectDialog.vue'
import { canAddProjects } from '../../composables/useAddProject'

const emit = defineEmits(['added'])
const canAdd = canAddProjects()
const adding = ref(false)
</script>

<template>
  <div class="pfr" data-pl-part="panel-empty" data-testid="projects-first-run">
    <template v-if="canAdd">
      <p class="pfr-text">No projects yet — add a repo or a workspace</p>
      <Button size="sm" variant="primary" data-pl-part="key--primary" data-testid="projects-first-run-add" @click="adding = true">Add project</Button>
      <AddProjectDialog v-model:open="adding" @added="emit('added', $event)" />
    </template>
    <p v-else class="pfr-text">No projects yet — an editor can add one under Repos</p>
  </div>
</template>

<style scoped>
.pfr { display: flex; align-items: center; flex-wrap: wrap; gap: 0.75rem; padding: 1rem 1.1rem; }
.pfr-text { font-size: 0.8125rem; color: var(--color-fg-muted); }
</style>
