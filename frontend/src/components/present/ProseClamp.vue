<script setup>
// A long response no longer stretches its turn: prose caps at 7.4em behind a
// fade, and the release valve is the presentation reader — deliberately NOT a
// second inline expand. An inline expand would put the same 2k-token wall back
// in the feed one click later, which is the shape this clamp exists to remove.
import { computed } from 'vue'
import { plainText } from '../../utils/plainText.js'
import Button from '../ui/Button.vue'

const props = defineProps({
  // Raw source text — drives both the clamp trigger and the word count.
  text: { type: String, default: '' },
  label: { type: String, default: 'Read full response' },
})
defineEmits(['present'])

const CLAMP_CHARS = 320
const clamped = computed(() => (props.text || '').length > CLAMP_CHARS)
const words = computed(() => plainText(props.text).split(/\s+/).filter(Boolean).length)
</script>

<template>
  <div>
    <div :class="clamped ? 'prose-clamp' : ''"><slot /></div>
    <Button
      v-if="clamped"
      variant="ghost"
      size="sm"
      class="prose-clamp-more mt-2 gap-2 px-1.5 text-[11.5px] font-semibold"
      @click.stop="$emit('present')"
    >⤢ {{ label }}<span class="prose-clamp-words">{{ words }} words</span></Button>
  </div>
</template>

<style scoped>
.prose-clamp {
  max-height: 7.4em;
  overflow: hidden;
  mask-image: linear-gradient(180deg, #000 72%, transparent);
  -webkit-mask-image: linear-gradient(180deg, #000 72%, transparent);
}
.prose-clamp-words { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-weight: 400; color: var(--color-fg-faint); }
</style>
