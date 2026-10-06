<script setup>
// <PlGlyph kind="project"><Icon name="folder" /></PlGlyph> draws the applied
// theme's glyph for `kind`, else classic's, else the default slot — the icon
// the call site drew before themes existed.
import { computed } from 'vue'
import { useAppliedTheme } from '../../composables/usePipelineTheme'
import { classicTheme } from '../../pipeline/themes/registry'

defineOptions({ inheritAttrs: false })
const props = defineProps({ kind: { type: String, required: true } })

const theme = useAppliedTheme()
const glyph = computed(() =>
  theme.value.glyphs?.[props.kind] ?? classicTheme.glyphs[props.kind] ?? null)
</script>

<template>
  <component :is="glyph" v-if="glyph" :kind="kind" :data-pl-glyph="kind" v-bind="$attrs" />
  <slot v-else />
</template>
