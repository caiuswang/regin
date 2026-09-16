<script setup>
import { computed, ref, watch, onUnmounted } from 'vue'
import Button from './ui/Button.vue'
import ClampedText from './ui/ClampedText.vue'
import Icon from './ui/Icon.vue'
import { useSpanImages } from '../composables/useSpanImages.js'
import { imageSegments, unreferencedImages } from '../utils/promptImages.js'

const props = defineProps({
  text: { type: String, default: '' },
  traceId: { type: String, default: '' },
  spanId: { type: String, default: '' },
  // 1-indexed N values from `[Image #N]` markers that were actually
  // attached. Cumulative across the session, so N can be > 1 even for
  // a prompt with a single image.
  imageIndices: { type: Array, default: () => [] },
  // Forwarded to ClampedText so an outer "Show full prompt" opens this clamp
  // too, instead of leaving the reader a second button to find.
  forceExpanded: { type: Boolean, default: false },
})

const { blobUrls } = useSpanImages(() => ({
  traceId: props.traceId,
  spanId: props.spanId,
  indices: props.imageIndices,
}))

const segments = computed(() => imageSegments(props.text, props.imageIndices))
const trailingImages = computed(() => unreferencedImages(segments.value, props.imageIndices))

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
  <ClampedText :lines="8" :force-expanded="forceExpanded" class="text-[13.5px] whitespace-pre-wrap break-words text-slate-800 leading-[1.5]">
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
            class="max-h-32 max-w-full sm:max-w-[16rem] object-contain block"
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
            class="max-h-32 max-w-full sm:max-w-[16rem] object-contain block"
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
