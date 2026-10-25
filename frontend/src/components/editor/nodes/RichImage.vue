<script setup>
// An `image` node: an `![](image:N)` drawn from the attachment the host lists
// under that index, so a picture attached after the node was made still
// appears once the host's list catches up.
import { computed } from 'vue'
import { NodeViewWrapper, nodeViewProps } from '@tiptap/vue-3'

const props = defineProps(nodeViewProps)

const url = computed(() => props.extension.options.resolve(props.node.attrs))
</script>

<template>
  <NodeViewWrapper as="span" class="rich-image" :class="{ 'is-selected': selected }" data-drag-handle>
    <img
      v-if="url" :src="url" :alt="node.attrs.alt" :title="node.attrs.title || undefined"
      :data-idx="node.attrs.idx ?? undefined"
    >
    <span v-else class="rich-image-missing" :data-idx="node.attrs.idx ?? undefined">
      (image:{{ node.attrs.idx }} — no longer attached)
    </span>
  </NodeViewWrapper>
</template>
