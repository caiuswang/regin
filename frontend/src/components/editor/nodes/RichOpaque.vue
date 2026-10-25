<script setup>
// A block the editor has no node for (HTML, a link definition, front matter):
// shown as the page renders it, saved as the bytes it was written as.
// Double-click opens those bytes in place; leaving the box writes them back.
//
// An unchanged box writes nothing, so the block stays the parsed node and
// goes back verbatim with the blank lines around it. The block's own trailing
// line breaks are kept off the box and put back on the way out.
import { computed, nextTick, ref } from 'vue'
import { NodeViewWrapper, nodeViewProps } from '@tiptap/vue-3'
import MarkdownContent from '../../MarkdownContent.vue'
import Textarea from '../../ui/Textarea.vue'
import { resolveImageRefs } from '../../../utils/taskBodyImages'

const props = defineProps(nodeViewProps)

const editing = ref(false)
const text = ref('')
const box = ref(null)

const raw = computed(() => props.node.attrs.raw ?? '')
const trailing = computed(() => /(?:\r?\n)*$/.exec(raw.value)[0])
const rendered = computed(() => resolveImageRefs(raw.value, props.extension.options.images()))
const rows = computed(() => Math.max(2, text.value.split('\n').length))

function open() {
  if (editing.value || !props.editor.isEditable) return
  text.value = raw.value.slice(0, raw.value.length - trailing.value.length)
  editing.value = true
  nextTick(() => box.value?.$el.focus())
}

function commit() {
  if (!editing.value) return
  editing.value = false
  if (!text.value.trim()) {
    props.deleteNode()
    return
  }
  const next = text.value + trailing.value
  if (next !== raw.value) props.updateAttributes({ raw: next })
}

function cancel() {
  editing.value = false
  props.editor.commands.focus()
}

// A link in the card is part of the block being edited, not a way off the page.
function holdLinks(event) {
  if (event.target instanceof Element && event.target.closest('a')) event.preventDefault()
}
</script>

<template>
  <NodeViewWrapper
    class="rich-opaque" :class="{ 'is-selected': selected, 'is-editing': editing }"
    contenteditable="false" data-testid="rich-opaque"
    @dblclick="open" @click="holdLinks"
  >
    <Textarea
      v-if="editing" ref="box" v-model="text" :rows="rows" spellcheck="false"
      class="rich-opaque-source" aria-label="Block source" data-testid="rich-opaque-source"
      @blur="commit" @keydown.esc.prevent.stop="cancel"
    />
    <MarkdownContent v-else class="rich-opaque-view" :markdown="rendered" title="Double-click to edit its source" />
  </NodeViewWrapper>
</template>
