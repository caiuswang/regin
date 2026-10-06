<script setup>
// Pixel stage tabs: classic's tabs, counts and select, each led by its sprite.
// Order, testids and roles are classic's, so the tab is the same control.
import { computed } from 'vue'
import PixelSprite from '../PixelSprite.vue'

const props = defineProps({
  counts: { type: Object, default: null },
  stage: { type: String, default: '' },
  compact: { type: Boolean, default: false },
})
const emit = defineEmits(['select', 'secret'])

// The browser's own click count (`detail`), not a timer: a third click in one
// gesture on ALL opens the secret stage (R12). It still selects ALL first.
function pick(t, e) {
  emit('select', t.key)
  if (t.key === '' && e.detail >= 3) emit('secret')
}

const DESKTOP = ['', 'spec', 'building', 'shipped', 'escaped']
const MOBILE = ['', 'escaped', 'building', 'shipped', 'spec']
const LABEL = { '': 'All', spec: 'Spec', building: 'Building', shipped: 'Shipped', escaped: 'Escaped' }
const SPRITE = { '': 'bot', spec: 'scroll', building: 'hammer', shipped: 'flag', escaped: 'bug' }

const tabs = computed(() => (props.compact ? MOBILE : DESKTOP).map(key => ({
  key,
  id: key || 'all',
  label: LABEL[key],
  sprite: SPRITE[key],
  n: props.counts ? props.counts[key || 'all'] : null,
})))
</script>

<template>
  <div
    role="tablist"
    aria-label="Feature stage"
    data-testid="feature-stage-tabs"
    data-pl-part="stage-tabs"
    :data-pl-compact="compact ? '' : undefined"
  >
    <button
      v-for="t in tabs"
      :key="t.id"
      type="button"
      role="tab"
      data-pl-part="stage-tab"
      :data-stage="t.id"
      :aria-selected="stage === t.key"
      :data-testid="`feature-stage-${t.id}`"
      @click="pick(t, $event)"
    >
      <PixelSprite :name="t.sprite" :scale="2" :data-pl-glyph="`feature.${t.id}`" />
      <span data-pl-part="stage-label">{{ t.label }}</span>
      <b v-if="t.n != null" data-pl-part="count" :data-testid="`feature-stage-count-${t.id}`">{{ t.n }}</b>
    </button>
  </div>
</template>
