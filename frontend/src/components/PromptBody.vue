<script setup>
import { computed, ref, watch, watchEffect, onUnmounted } from 'vue'
import api from '../api'
import Button from './ui/Button.vue'
import ClampedText from './ui/ClampedText.vue'
import Icon from './ui/Icon.vue'

const props = defineProps({
  text: { type: String, default: '' },
  traceId: { type: String, default: '' },
  spanId: { type: String, default: '' },
  // 1-indexed N values from `[Image #N]` markers that were actually
  // attached. Cumulative across the session, so N can be > 1 even for
  // a prompt with a single image.
  imageIndices: { type: Array, default: () => [] },
})

const validIndices = computed(() => new Set(props.imageIndices))

// idx -> object-URL. Image-bytes endpoints require an Authorization
// header that <img> tags can't attach, so we fetch each image via the
// authenticated api client and hand the resulting blob to <img>. Revoke
// URLs on unmount / when the prompt changes so we don't leak blob
// references.
const blobUrls = ref(new Map())

function revokeAll() {
  for (const url of blobUrls.value.values()) URL.revokeObjectURL(url)
  blobUrls.value = new Map()
}

watchEffect(async () => {
  const indices = props.imageIndices
  const traceId = props.traceId
  const spanId = props.spanId
  if (!traceId || !spanId || !indices.length) {
    revokeAll()
    return
  }
  for (const idx of indices) {
    if (blobUrls.value.has(idx)) continue
    try {
      const url = await api.getBlobUrl(
        `/sessions/${encodeURIComponent(traceId)}/prompts/${encodeURIComponent(spanId)}/images/${idx}`,
      )
      // Re-check after the await — props.imageIndices may have changed
      // and a stale fetch shouldn't repopulate the cache.
      if (props.imageIndices.includes(idx)) {
        blobUrls.value.set(idx, url)
        blobUrls.value = new Map(blobUrls.value)
      } else {
        URL.revokeObjectURL(url)
      }
    } catch {
      // Best-effort: a failed image fetch falls back to the [Image #N]
      // placeholder rendering, not a broken img tag.
    }
  }
})

onUnmounted(revokeAll)

// Split `text` into an ordered array of {type:'text'|'image', value|idx}
// segments. A `[Image #N]` marker becomes an image segment only when N
// is in the persisted set — typing `[Image #5]` without attaching
// leaves the literal text alone.
const segments = computed(() => {
  const out = []
  if (!props.text) return out
  const re = /\[Image #(\d+)\]/g
  let last = 0
  let m
  while ((m = re.exec(props.text)) !== null) {
    const idx = parseInt(m[1], 10)
    const valid = validIndices.value.has(idx)
    if (m.index > last) {
      out.push({ type: 'text', value: props.text.slice(last, m.index) })
    }
    if (valid) {
      out.push({ type: 'image', idx })
    } else {
      out.push({ type: 'text', value: m[0] })
    }
    last = re.lastIndex
  }
  if (last < props.text.length) {
    out.push({ type: 'text', value: props.text.slice(last) })
  }
  return out
})

// If the prompt text was truncated by the hook (`…` suffix) a trailing
// image marker may have been cut. Render placeholder thumbnails for
// any indices that didn't match a marker so they aren't silently
// dropped.
const trailingImages = computed(() => {
  if (!props.imageIndices.length) return []
  const referenced = new Set(
    segments.value.filter(s => s.type === 'image').map(s => s.idx),
  )
  return props.imageIndices.filter(idx => !referenced.has(idx))
})

const lightboxIdx = ref(null)
function openLightbox(idx) { lightboxIdx.value = idx }
function closeLightbox() { lightboxIdx.value = null }

function onKeyDown(e) {
  if (e.key === 'Escape') closeLightbox()
}
watch(lightboxIdx, (idx) => {
  if (idx !== null) {
    window.addEventListener('keydown', onKeyDown)
  } else {
    window.removeEventListener('keydown', onKeyDown)
  }
})
onUnmounted(() => window.removeEventListener('keydown', onKeyDown))
</script>

<template>
  <ClampedText :lines="8" class="text-[13.5px] whitespace-pre-wrap break-words text-purple-900 leading-relaxed">
    <template v-for="(seg, pos) in segments" :key="pos">
      <span v-if="seg.type === 'text'">{{ seg.value }}</span>
      <span
        v-else
        class="inline-flex items-center gap-1 align-middle mx-0.5"
      >
        <span class="font-mono text-[11px] text-purple-700">[Image #{{ seg.idx }}]</span>
        <Button
          variant="ghost"
          class="h-auto rounded border border-purple-300 bg-white p-0.5 hover:border-purple-500 focus-visible:outline-2 focus-visible:outline-purple-500 cursor-zoom-in"
          :title="`Image #${seg.idx} — click to enlarge`"
          @click.stop="openLightbox(seg.idx)"
        >
          <img
            :src="blobUrls.get(seg.idx) || ''"
            alt=""
            loading="lazy"
            class="max-h-32 max-w-[16rem] object-contain block"
          />
        </Button>
      </span>
    </template>
    <div v-if="trailingImages.length" class="mt-1 flex flex-wrap gap-2">
      <span
        v-for="imageNum in trailingImages"
        :key="imageNum"
        class="inline-flex items-center gap-1"
      >
        <span class="font-mono text-[11px] text-purple-700">[Image #{{ imageNum }}]</span>
        <Button
          variant="ghost"
          class="h-auto rounded border border-purple-300 bg-white p-0.5 hover:border-purple-500 focus-visible:outline-2 focus-visible:outline-purple-500 cursor-zoom-in"
          :title="`Image #${imageNum} — click to enlarge`"
          @click.stop="openLightbox(imageNum)"
        >
          <img
            :src="blobUrls.get(imageNum) || ''"
            alt=""
            loading="lazy"
            class="max-h-32 max-w-[16rem] object-contain block"
          />
        </Button>
      </span>
    </div>
  </ClampedText>

  <Teleport to="body">
    <div
      v-if="lightboxIdx !== null"
      class="fixed inset-0 z-50 bg-black/75 hover:bg-black/80 flex items-center justify-center p-6 cursor-pointer focus-visible:outline-2 focus-visible:outline-white"
      role="button"
      tabindex="0"
      aria-label="Close image preview"
      @click="closeLightbox"
      @keydown.enter="closeLightbox"
    >
      <img
        :src="blobUrls.get(lightboxIdx) || ''"
        alt="Attached image preview"
        class="max-h-full max-w-full object-contain shadow-2xl"
        @click.stop
      />
      <Button
        variant="ghost"
        size="icon"
        class="absolute top-4 right-4 text-white/80 hover:bg-transparent hover:text-white focus-visible:outline-2 focus-visible:outline-white"
        title="Close (esc)"
        @click.stop="closeLightbox"
      >
        <Icon name="x" :size="22" />
      </Button>
    </div>
  </Teleport>
</template>
