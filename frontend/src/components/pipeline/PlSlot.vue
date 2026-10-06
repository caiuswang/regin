<script setup>
// <PlSlot name="OverviewLoop" v-bind="…" /> renders the applied theme's
// override for a slot, else classic's component, else nothing. Attributes and
// listeners pass straight through, so no wrapper element is added.
//
// A call site whose classic look is NOT the classic slot component passes that
// look as the default slot: it renders unless a non-classic theme overrides
// the slot, the same fallback PlGlyph gives an icon.
import { computed, useSlots } from 'vue'
import { useAppliedTheme } from '../../composables/usePipelineTheme'
import { classicTheme } from '../../pipeline/themes/registry'

defineOptions({ inheritAttrs: false })
const props = defineProps({ name: { type: String, required: true } })
const slots = useSlots()

const theme = useAppliedTheme()
const override = computed(() => {
  const own = theme.value.slots?.[props.name]
  return own && own !== classicTheme.slots[props.name] ? own : null
})
const component = computed(() => override.value
  ?? (slots.default ? null : classicTheme.slots[props.name] ?? null))
</script>

<template>
  <component :is="component" v-if="component" v-bind="$attrs" />
  <slot v-else />
</template>
