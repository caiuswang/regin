<script setup>
// A free-form task is the default and carries no chip: marking every row
// "task" would be noise on the one kind that says nothing about its deliverable.
import { computed } from 'vue'
import Badge from '../Badge.vue'

const props = defineProps({
  kind: { type: String, default: '' },
})

const COLOR = { spec: 'blue', build: 'green', fix: 'amber' }
const color = computed(() => COLOR[props.kind] || '')
</script>

<template>
  <Badge v-if="color" :color="color" :label="kind" data-pl-part="tag" data-testid="task-kind-chip" />
</template>
