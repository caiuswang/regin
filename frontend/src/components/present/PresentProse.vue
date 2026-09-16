<script setup>
// A span's prose with its attached images in reading order.
//
// The feed can only afford a 8rem thumbnail beside the marker; the reader is
// where the payload is shown whole, so a `[Image #N]` marker becomes the image
// itself, at document scale, between the markdown runs that surround it.
import { computed } from 'vue'
import MarkdownContent from '../MarkdownContent.vue'
import { useSpanImages } from '../../composables/useSpanImages.js'
import { imageSegments, unreferencedImages } from '../../utils/promptImages.js'

const props = defineProps({
  markdown: { type: String, default: '' },
  // Literal text — a typed prompt, a file, an injected block. It still comes
  // through here rather than being rendered straight in the parent, so an
  // attached image lands where its `[Image #N]` marker sits either way.
  verbatim: { type: Boolean, default: false },
  traceId: { type: String, default: '' },
  spanId: { type: String, default: '' },
  imageIndices: { type: Array, default: () => [] },
})

// Literal text is ONE document. `imageSegments` splits on every `[Image #N]`
// marker, including one with nothing attached behind it — which it keeps as
// literal text, but in a segment of its own. Left alone, a prompt that merely
// mentions `[Image #5]` paints three stacked wells where the conversation card
// paints one continuous block. Whitespace-only runs are kept here for the same
// reason: in a verbatim body they are the text's own blank lines.
function sewn(blocks) {
  return blocks.reduce((out, b) => {
    const prev = out[out.length - 1]
    if (b.type === 'text' && prev?.type === 'text') prev.value += b.value
    else out.push(b.type === 'text' ? { ...b } : b)
    return out
  }, [])
}

// One ordered list rather than prose-then-extras: with no images this is a
// single markdown run, which is exactly what the reader rendered before.
// Whitespace-only runs are dropped — the gap between two adjacent markers is
// not a paragraph.
const blocks = computed(() => {
  const segs = imageSegments(props.markdown, props.imageIndices)
  const extra = unreferencedImages(segs, props.imageIndices)
    .map(idx => ({ type: 'image', idx }))
  const all = [...segs, ...extra]
  if (props.verbatim) return sewn(all)
  return all.filter(b => b.type !== 'text' || b.value.trim())
})

const { blobUrls } = useSpanImages(() => ({
  traceId: props.traceId,
  spanId: props.spanId,
  indices: props.imageIndices,
}))
</script>

<template>
  <template v-for="(block, i) in blocks" :key="i">
    <pre v-if="block.type === 'text' && verbatim" class="present-verbatim">{{ block.value }}</pre>
    <MarkdownContent v-else-if="block.type === 'text'" :markdown="block.value" />
    <figure v-else class="present-figure">
      <!-- Until the authenticated fetch lands — and for good if it fails — the
           marker stands in for the image, which is what the feed falls back to
           as well. An `<img>` with an empty src would paint a broken glyph. -->
      <img
        v-if="blobUrls.get(block.idx)"
        :src="blobUrls.get(block.idx)"
        :alt="`Image #${block.idx}`"
        class="present-image"
      />
      <span v-else class="present-image-pending">[Image #{{ block.idx }}]</span>
      <!-- No caption under the placeholder: it already says which image it is,
           and `image_indices` deliberately lists images the persistence caps
           dropped, so this pairing is a real state, not a transient one. -->
      <figcaption v-if="blobUrls.get(block.idx)" class="present-figcaption">Image #{{ block.idx }}</figcaption>
    </figure>
  </template>
</template>

<style scoped>
.present-figure { margin: 18px 0; }
/* A placeholder is one line of text, not a plate: drop the figure's margins so
   it reads as part of the prose. It still takes its own line — the runs either
   side of it are block roots — which is honest about there being nothing to
   show. */
.present-figure:not(:has(.present-image)) { margin: 0; }
.present-image {
  display: block; max-width: 100%; max-height: 70vh; object-fit: contain;
  border-radius: 11px; border: 1px solid var(--color-border);
  background: var(--color-surface-2);
}
.present-image-pending {
  display: inline-block; font-family: var(--font-mono); font-size: 12.5px;
  color: var(--color-fg-subtle);
}
.present-figcaption {
  margin-top: 6px; font-family: var(--font-mono); font-size: 11px;
  color: var(--color-fg-subtle);
}
</style>
