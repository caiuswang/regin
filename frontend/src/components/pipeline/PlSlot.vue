<script setup>
// <PlSlot name="OverviewLoop" v-bind="…" /> renders the applied theme's
// override for a slot, else classic's component, else nothing. Attributes and
// listeners pass straight through, so no wrapper element is added.
import { computed } from 'vue'
import { useAppliedTheme } from '../../composables/usePipelineTheme'
import { classicTheme } from '../../pipeline/themes/registry'

defineOptions({ inheritAttrs: false })
const props = defineProps({ name: { type: String, required: true } })

const theme = useAppliedTheme()
const component = computed(() =>
  theme.value.slots?.[props.name] ?? classicTheme.slots[props.name] ?? null)
</script>

<template>
  <component :is="component" v-if="component" v-bind="$attrs" />
</template>
