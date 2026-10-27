<script setup>
import { ref, reactive, onMounted, onBeforeUnmount } from 'vue'
import { LEVELS } from '../../content/quest.js'
import { QuestEngine, VIEW, levelById } from './engine.js'
import { useLevelDrag } from './useLevelDrag.js'
import { useTypewriter } from './useTypewriter.js'
import Button from '../ui/Button.vue'

const stage = ref(null)
const canvas = ref(null)
const home = () => Object.fromEntries(LEVELS.map((l) => [l.id, { x: l.x, y: l.y }]))
const positions = reactive(home())
const speaker = ref(levelById.spec)
const events = ref([])
const round = ref(1)
const playing = ref(false)
const userPaused = ref(false)
const { typed, type } = useTypewriter()

let engine = null
let observer = null

function say(level, text) {
  speaker.value = level
  type(text)
}

const drag = useLevelDrag(stage, positions, (id) => say(levelById[id], levelById[id].detail))

function setPlaying(on) {
  playing.value = on
  if (on) engine.play()
  else {
    engine.pause()
    engine.draw()
  }
}

function togglePlay() {
  userPaused.value = playing.value
  setPlaying(!playing.value)
}

function resetMap() {
  Object.assign(positions, home())
  engine.restart()
  engine.draw()
}

function box(l) {
  const p = positions[l.id]
  return {
    left: `${((p.x * 16 + 8 - 26) / VIEW.width) * 100}%`,
    top: `${((p.y * 16 + 8 - 34) / VIEW.height) * 100}%`,
    width: `${(52 / VIEW.width) * 100}%`,
    height: `${(62 / VIEW.height) * 100}%`,
  }
}

onMounted(async () => {
  await document.fonts.load('8px Silkscreen')
  engine = new QuestEngine(canvas.value, positions, {
    onLog: (kind) => events.value.push({ id: events.value.length + 1, kind }),
    onSay: say,
    onRound: (n) => { round.value = n },
    onReset: () => { events.value = [] },
  })
  engine.draw()
  userPaused.value = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  observer = new IntersectionObserver(([entry]) => {
    if (!userPaused.value) setPlaying(entry.isIntersecting)
  }, { threshold: 0.2 })
  observer.observe(stage.value)
})

onBeforeUnmount(() => {
  observer?.disconnect()
  engine?.pause()
})
</script>

<template>
  <figure class="quest">
    <div class="hud-bar quest-hud">
      <span><span class="hud-accent">WORLD 1</span> · THE PIPELINE QUEST</span>
      <span>ROUNDS-TO-DONE <b class="quest-round">{{ round }}</b></span>
    </div>
    <div ref="stage" class="quest-stage">
      <canvas
        ref="canvas" class="quest-canvas" :width="VIEW.width" :height="VIEW.height" role="img"
        aria-label="A pixel map of the feature pipeline: spec, slice, transcribe, arm, run, finish and ship, with the defect and fix road leading back to run."
      />
      <Button
        v-for="l in LEVELS" :key="l.id" variant="bare"
        class="quest-level" :class="{ 'is-boss': l.id === 'defect' }" :style="box(l)"
        :aria-label="`${l.code} ${l.name}. Drag or use arrow keys to move it; Enter to read about it.`"
        @pointerdown="drag.onPointerDown(l.id, $event)" @pointermove="drag.onPointerMove"
        @pointerup="drag.onPointerUp" @keydown="drag.onKey(l.id, $event)"
      >
        <span class="quest-tag"><b>{{ l.code }}</b><span class="quest-name"> {{ l.name }}</span></span>
      </Button>
    </div>
    <div class="quest-panels">
      <div class="quest-dialog" aria-live="polite">
        <p class="quest-kicker">{{ speaker.code }} · {{ speaker.name }}</p>
        <p class="quest-line">{{ typed }}<span class="quest-caret" aria-hidden="true"></span></p>
      </div>
      <div class="quest-log">
        <p class="quest-kicker">feature_events</p>
        <ol>
          <li v-for="e in events.slice(-5)" :key="e.id" :class="{ 'is-ok': e.kind === 'shipped' }">
            <span>#{{ e.id }}</span> {{ e.kind }}
          </li>
          <li v-if="!events.length" class="is-empty">—</li>
        </ol>
      </div>
    </div>
    <figcaption class="quest-controls">
      <Button variant="bare" class="key-primary" @click="togglePlay">{{ playing ? '❚❚ PAUSE' : '▶ PLAY' }}</Button>
      <Button variant="bare" class="key-outline" @click="resetMap">RESET MAP</Button>
      <span class="quest-hint">Drag any level to reroute the road. Click one to read it.</span>
    </figcaption>
  </figure>
</template>
