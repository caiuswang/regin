<script setup>
import { computed } from 'vue'
import InlineText from '../blocks/InlineText.vue'
import ToneText from './ToneText.vue'

const props = defineProps({
  n: { type: Number, required: true },
  anchor: { type: String, required: true },
  level: { type: Object, required: true },
})

const headingId = computed(() => `l${props.n}-h`)
const pips = computed(() => [1, 2, 3, 4].map((i) => (i < props.n ? 'is-passed' : i === props.n ? 'is-current' : '')))

const METER_CELLS = 20
const meterSpan = computed(() => {
  const m = props.level.meter
  return m ? Math.round((m.part / m.whole) * METER_CELLS) : 0
})
</script>

<template>
  <section :id="anchor" :aria-labelledby="headingId" :data-screen-label="`Level 1-${n} ${level.layer}`" class="land-panel level">
    <div class="hud-bar land-hud">
      <span class="land-hud-title">
        <span aria-hidden="true" class="level-pips"><span v-for="(state, i) in pips" :key="i" class="level-pip" :class="state" /></span>
        <span class="hud-accent">LEVEL 1-{{ n }}</span> · {{ level.hud }}
      </span>
      <span class="land-hud-stats">
        <span v-for="(stat, i) in level.stats" :key="i" class="land-hud-stat"><ToneText :segs="stat" tag="b" /></span>
      </span>
    </div>
    <div class="level-grid">
      <div class="level-text">
        <h2 :id="headingId" class="land-h2">{{ level.title }}</h2>
        <p class="land-lead level-lead"><InlineText :text="level.body" /></p>
        <pre v-if="level.pre" class="land-pre"><template v-for="(line, i) in level.pre" :key="i"><ToneText :segs="line" />{{ i < level.pre.length - 1 ? '\n' : '' }}</template></pre>
        <div v-if="level.tiles" class="level-tiles">
          <div v-for="tile in level.tiles" :key="tile.label" class="level-tile">
            <span class="level-tile-label">{{ tile.label }}</span>
            <span class="level-tile-value">{{ tile.value }}</span>
          </div>
          <div v-if="level.meter" class="level-tile is-wide">
            <span class="level-tile-label">{{ level.meter.label }}</span>
            <span class="level-tile-value tone-green">${{ level.meter.part }} <span class="level-tile-of">OF ${{ level.meter.whole }}</span></span>
            <span aria-hidden="true" class="level-meter">
              <span class="level-meter-part" :style="{ gridColumn: `span ${meterSpan}` }" />
              <span class="level-meter-rest" :style="{ gridColumn: `span ${METER_CELLS - meterSpan}` }" />
            </span>
          </div>
        </div>
        <p class="level-caption">{{ level.caption }}</p>
        <RouterLink :to="level.link.to" class="level-link focus-visible:ring">{{ level.link.label }}</RouterLink>
      </div>
      <figure class="land-shot">
        <div class="land-shot-frame">
          <img :src="level.shot.src" :alt="level.shot.alt" width="2880" height="1800" loading="lazy" />
        </div>
        <figcaption class="land-figcaption">{{ level.shot.cap }}</figcaption>
      </figure>
    </div>
  </section>
</template>
