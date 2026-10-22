<script setup>
// "Add project" beside a project field that has nothing to offer yet.
// Viewers get nothing: they cannot register one.
import { ref } from 'vue'
import Button from '../ui/Button.vue'
import AddProjectDialog from './AddProjectDialog.vue'
import { canAddProjects } from '../../composables/useAddProject'

defineProps({ testid: { type: String, default: 'add-project-link' } })
const emit = defineEmits(['added'])
const adding = ref(false)
const allowed = canAddProjects()
</script>

<template>
  <template v-if="allowed">
    <Button variant="link" size="sm" class="apl" :data-testid="testid" @click="adding = true">Add project</Button>
    <AddProjectDialog v-model:open="adding" @added="emit('added', $event)" />
  </template>
</template>

<style scoped>
.apl { height: auto; padding: 0; font-size: 0.75rem; }
</style>
