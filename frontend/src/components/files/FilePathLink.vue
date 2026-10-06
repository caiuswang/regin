<script setup>
import { computed } from 'vue'
import Button from '../ui/Button.vue'
import { openFilePreview } from '../../composables/useFilePreview.js'
import { sessionPathSource } from './source.js'

// A path a span names, opened as the file is on disk now (a session source).
// Without the session there is nothing to resolve the path against, so it
// stays text rather than a link that 404s.
const props = defineProps({
  traceId: { type: String, default: '' },
  path: { type: String, default: '' },
})

const source = computed(() => sessionPathSource(props.traceId, props.path))
</script>

<template>
  <!-- `.stop`: these sit inside cards whose own click selects or folds them. -->
  <Button
    v-if="source"
    variant="link"
    size="sm"
    class="font-normal text-[length:inherit] whitespace-normal break-all text-left justify-start select-text"
    :title="`Open ${path}`"
    data-testid="file-path-link"
    @click.stop="openFilePreview(source)"
    @keydown.enter.stop
  ><slot>{{ path }}</slot></Button>
  <span v-else><slot>{{ path }}</slot></span>
</template>
