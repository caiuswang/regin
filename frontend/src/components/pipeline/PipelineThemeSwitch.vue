<script setup>
import { computed } from 'vue'
import Select from '../ui/Select.vue'
import { usePipelineTheme } from '../../composables/usePipelineTheme'

const { themes, preference, activeId, setPreference } = usePipelineTheme()
const options = themes.map(t => ({ value: t.id, label: t.label }))
// Names the theme on screen: after a failed load that is classic, though the
// stored preference still says otherwise.
const model = computed({ get: () => activeId.value ?? preference.value, set: setPreference })
</script>

<template>
  <Select
    v-model="model"
    :options="options"
    bare
    class="pl-theme-switch"
    aria-label="Pipeline theme"
    data-testid="pipeline-theme-switch"
    data-pl-part="theme-switch"
  />
</template>

