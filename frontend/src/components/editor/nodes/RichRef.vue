<script setup>
// A `ref` node drawn as the chip the saved page draws for it. While the body
// is being edited a click selects the chip rather than following it: the
// draft is still on the page.
import { computed } from 'vue'
import { NodeViewWrapper, nodeViewProps } from '@tiptap/vue-3'
import RefChip from '../../refs/RefChip.vue'

const props = defineProps(nodeViewProps)

const target = computed(() => `${props.node.attrs.kind}:${props.node.attrs.key}`)
const item = computed(() => props.extension.options.item(target.value))
</script>

<template>
  <NodeViewWrapper
    as="span" class="rich-ref" :class="{ 'is-selected': selected }"
    data-testid="rich-ref" @click.capture.prevent
  >
    <RefChip :target="target" :label="node.attrs.label" :item="item" draggable="false" />
  </NodeViewWrapper>
</template>
