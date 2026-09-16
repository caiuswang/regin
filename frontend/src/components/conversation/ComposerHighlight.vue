<script setup>
// Token highlight for a plain <textarea>: a mirror of the draft rendered
// BEHIND the field, every glyph transparent, so only the coloured rectangles
// under `@path` mentions and `[Image #N]` markers show through. Painting the
// backdrop rather than recolouring the text is what keeps the real textarea on
// top — its caret, selection, spellcheck and IME are untouched, and there is no
// second copy of the text for them to drift against.
//
// The illusion only holds while mirror and field wrap identically, so the host
// must style the pair together (font, padding, line-height, width) rather than
// giving this component typography of its own.
import { computed } from 'vue'

const props = defineProps({
  text: { type: String, default: '' },
})

// `@` is only a mention on a word boundary — the same rejection mentionToken()
// makes in useFileMentions, without which every email address in a draft would
// light up as a file reference.
const TOKEN = /\[Image #\d+\]|(^|\s)(@\S+)/g

const parts = computed(() => {
  const text = props.text
  const out = []
  let last = 0
  for (const m of text.matchAll(TOKEN)) {
    const token = m[2] || m[0]
    const start = m.index + (m[2] ? m[1].length : 0)
    if (start > last) out.push({ text: text.slice(last, start), kind: '' })
    out.push({ text: token, kind: m[2] ? 'file' : 'image' })
    last = start + token.length
  }
  // A trailing newline gets no line box of its own here, but does in the
  // textarea; the space keeps the two scroll heights in step.
  out.push({ text: `${text.slice(last)} `, kind: '' })
  return out
})
</script>

<template>
  <div class="composer-hl" aria-hidden="true">
    <span
      v-for="(part, i) in parts"
      :key="i"
      :class="part.kind ? `composer-hl-${part.kind}` : null"
    >{{ part.text }}</span>
  </div>
</template>

<style scoped>
.composer-hl {
  position: absolute;
  inset: 0;
  overflow: hidden;
  pointer-events: none;
  color: transparent;
  white-space: pre-wrap;
  overflow-wrap: break-word;
}

/* Cloned so a mention that wraps keeps its fill on both lines instead of
   painting one box across the gap. */
.composer-hl-file,
.composer-hl-image {
  border-radius: 4px;
  box-decoration-break: clone;
  -webkit-box-decoration-break: clone;
}
.composer-hl-file {
  background: color-mix(in srgb, var(--color-primary) 22%, transparent);
}
.composer-hl-image {
  background: color-mix(in srgb, var(--color-agent) 24%, transparent);
}
</style>
