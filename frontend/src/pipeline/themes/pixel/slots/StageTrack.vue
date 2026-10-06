<script setup>
// The level track: SPEC → BUILD → SHIP as stations on a dashed line, a bot at
// the current one, the ship flag coloured by outcome. Classic's props plus two
// optional marks — `live` hangs an AGENT tag over the bot, `openDefects` a bug
// beside it — so a call site that passes neither gets a plain track.
import { computed } from 'vue'
import PixelSprite from '../PixelSprite.vue'

const props = defineProps({
  stage: { type: String, required: true },
  compact: { type: Boolean, default: false },
  showLabel: { type: Boolean, default: true },
  live: { type: Boolean, default: false },
  openDefects: { type: Number, default: 0 },
})

const STATIONS = [
  { key: 'spec', name: 'Spec' },
  { key: 'build', name: 'Build' },
  { key: 'ship', name: 'Ship' },
]
const AT = { spec: 0, building: 1, shipped: 2, escaped: 2 }
const FLAG = { shipped: 'flag', escaped: 'flag.escaped' }

const at = computed(() => AT[props.stage] ?? -1)
const flag = computed(() => FLAG[props.stage] || 'flag.cold')
const scale = computed(() => (props.compact ? 1 : 2))
const label = computed(() => [
  `Stage: ${props.stage}`,
  props.live && 'agent running',
  props.openDefects > 0 && `${props.openDefects} open defect${props.openDefects === 1 ? '' : 's'}`,
].filter(Boolean).join(' · '))
</script>

<template>
  <div
    data-pl-part="level"
    :data-stage="stage"
    :data-pl-compact="compact ? '' : undefined"
    role="img"
    :aria-label="label"
  >
    <div data-pl-part="level-track">
      <span
        v-for="(s, i) in STATIONS"
        :key="s.key"
        data-pl-part="level-station"
        :data-reached="i <= at ? '' : undefined"
        :data-current="i === at ? '' : undefined"
      >
        <span data-pl-part="level-actors">
          <span v-if="i === at" data-pl-part="level-bot">
            <span v-if="live" data-pl-part="level-agent" data-testid="level-agent">Agent</span>
            <PixelSprite name="bot" :scale="scale" data-pl-glyph="feature.building" />
          </span>
          <PixelSprite v-if="i === 2" :name="flag" :scale="scale" :data-pl-glyph="`feature.${stage === 'escaped' ? 'escaped' : 'shipped'}`" />
          <PixelSprite v-if="i === at && openDefects > 0" name="bug" :scale="scale" data-pl-glyph="defect.wild" data-testid="level-bug" />
        </span>
        <i data-pl-part="level-tick" />
        <span v-if="!compact" data-pl-part="level-name">{{ s.name }}</span>
      </span>
    </div>
    <span v-if="compact && showLabel" data-pl-part="level-word">{{ stage }}</span>
  </div>
</template>
