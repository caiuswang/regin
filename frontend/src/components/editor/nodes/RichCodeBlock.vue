<script setup>
// A code block on the rich face: its text stays ProseMirror's (coloured by the
// lowlight decorations), the picker in its corner rewrites the fence's info
// string, and a mermaid block shows its diagram underneath — drawn by the very
// component the read view uses.
//
// Picking a language is one undo step and touches only this block's attrs,
// so this block alone is re-serialized on Save.
import { computed } from 'vue'
import { NodeViewContent, NodeViewWrapper, nodeViewProps } from '@tiptap/vue-3'
import MermaidDiagram from '../../markdown/MermaidDiagram.vue'
import Select from '../../ui/Select.vue'
import { LANGUAGES } from '../../../editor/languages.js'
import 'highlight.js/styles/github-dark.css'

const props = defineProps(nodeViewProps)

const language = computed(() => props.node.attrs.language || null)
const picked = computed(() => language.value ?? 'plain')
const choices = computed(() => (LANGUAGES.includes(picked.value) ? LANGUAGES : [...LANGUAGES, picked.value]))

function pick(value) {
  const next = value === 'plain' ? null : value
  if (next === language.value) return
  const at = props.getPos()
  if (typeof at !== 'number') return
  props.editor.chain()
    .newUndoStep()
    .command(({ tr }) => { tr.setNodeAttribute(at, 'language', next); return true })
    .run()
  props.editor.commands.newUndoStep()
}
</script>

<template>
  <NodeViewWrapper class="rich-code" :data-language="language">
    <div class="rich-code-bar" contenteditable="false">
      <Select
        data-testid="code-language"
        aria-label="Code block language"
        :model-value="picked"
        :options="choices"
        @update:model-value="pick"
      />
    </div>
    <pre><NodeViewContent as="code" :class="language ? ['hljs', `language-${language}`] : null" /></pre>
    <MermaidDiagram
      v-if="language === 'mermaid'"
      data-testid="mermaid-preview"
      class="rich-code-preview"
      :source="node.textContent"
      :show-source="false"
    />
  </NodeViewWrapper>
</template>

<style scoped>
.rich-code { position: relative; margin: 0 0 1rem; }
.rich-code > pre { margin-bottom: 0; padding-right: 7rem; }
.rich-code-bar { position: absolute; top: 0.375rem; right: 0.375rem; z-index: 1; }
.rich-code-preview {
    margin: 0; padding: 0.75rem;
    border: 1px solid var(--color-border-subtle); border-top: 0;
    border-radius: 0 0 0.5rem 0.5rem;
}
</style>
