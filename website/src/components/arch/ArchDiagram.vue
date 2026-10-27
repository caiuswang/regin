<script setup>
import { ref, computed } from 'vue'
import { YOU, AGENT, SERVE, BANDS, PARTS, FLOWS } from '../../content/archmap.js'
import { ROBOT_FRAME_1, ROBOT_PALETTE } from '../../content/play.js'
import { useFlowSteps } from './useFlowSteps.js'
import ArchPart from './ArchPart.vue'
import PixelSprite from '../play/PixelSprite.vue'
import Button from '../ui/Button.vue'

const { flowIndex, flow, steps, badges, current, pick, clear } = useFlowSteps(FLOWS)
const selected = ref(null)

function select(id) {
  clear()
  selected.value = id
}

function choose(i) {
  selected.value = null
  pick(i)
}

const detail = computed(() => PARTS[selected.value] || null)
const bind = (part) => ({ part, badges: badges.value[part.id] || [], current: current.value === part.id, selected: selected.value === part.id })
</script>

<template>
  <figure class="arch">
    <div class="hud-bar arch-hud">
      <span><span class="hud-accent">REF R-1</span> · HOW REGIN FITS TOGETHER</span>
      <span v-if="flow">FLOW <b>{{ flowIndex + 1 }}/{{ FLOWS.length }}</b></span>
      <span v-else>PICK A FLOW</span>
    </div>

    <div class="arch-flows" role="group" aria-label="Flows">
      <Button
        v-for="(f, i) in FLOWS" :key="f.id" variant="bare" class="arch-flow"
        :class="{ 'is-on': i === flowIndex }" :aria-pressed="i === flowIndex" @click="choose(i)"
      >
        <b>{{ String(i + 1).padStart(2, '0') }}</b> {{ f.name }}
      </Button>
    </div>

    <div class="arch-diagram" :class="{ 'is-tracing': flow }">
      <div class="arch-you">
        <ArchPart v-bind="bind(YOU)" @select="select" />
        <span class="arch-you-wire" aria-hidden="true">/overview · /live · Inbox</span>
      </div>

      <div class="arch-system">
        <p class="arch-system-tab"><b>REGIN</b> wraps the agent you already use</p>
        <div class="arch-stack">
          <template v-for="(band, i) in BANDS" :key="band.id">
            <div class="arch-band-label" :class="[`row-${i + 1}`, `is-${band.id}`]">
              <span class="arch-band-name">{{ band.name }}</span>
              <span class="arch-band-role">{{ band.role }}</span>
            </div>
            <div class="arch-parts" :class="[`row-${i + 1}`, `is-${band.id}`]" role="group" :aria-label="band.name">
              <ArchPart v-for="p in band.parts" :key="p.id" v-bind="bind(p)" @select="select" />
            </div>
            <div class="arch-port" :class="`row-${i + 1}`" aria-hidden="true">
              <span v-if="band.port" class="arch-port-wire"><i>{{ band.port }}</i></span>
            </div>
          </template>
          <div class="arch-agent">
            <ArchPart v-bind="bind(AGENT)" @select="select">
              <span class="arch-robot" aria-hidden="true"><PixelSprite :rows="ROBOT_FRAME_1" :palette="ROBOT_PALETTE" :px="5" /></span>
              <span class="arch-providers">
                <span v-for="name in AGENT.providers" :key="name" class="arch-provider">{{ name }}</span>
                <span class="arch-provider is-next">+ next adapter</span>
              </span>
            </ArchPart>
          </div>
          <div class="arch-serve"><ArchPart v-bind="bind(SERVE)" @select="select" /></div>
        </div>
      </div>
    </div>

    <div class="arch-panel" aria-live="polite">
      <template v-if="flow">
        <p class="arch-panel-kicker">{{ flow.name }}</p>
        <ol class="arch-steps">
          <li v-for="([id, text], i) in steps" :key="i" :class="{ 'is-current': i === steps.length - 1 }">
            <b>{{ i + 1 }}</b><span class="arch-step-part">{{ PARTS[id].name }}</span> {{ text }}
          </li>
        </ol>
        <Button variant="bare" class="key-outline arch-clear" @click="clear">CLEAR</Button>
      </template>
      <template v-else-if="detail">
        <p class="arch-panel-kicker">{{ detail.name }}</p>
        <p class="arch-panel-text">{{ detail.detail }}</p>
      </template>
      <p v-else class="arch-panel-text">Four layers wrap the agent you already use, over one server. Pick a flow to follow it through them, or click any part to read what it does.</p>
    </div>
  </figure>
</template>
