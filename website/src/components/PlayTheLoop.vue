<script setup>
import { usePlayTheLoop } from './play/usePlayTheLoop.js'
import PlayTrack from './play/PlayTrack.vue'
import PlayFeature from './play/PlayFeature.vue'
import PlayTasks from './play/PlayTasks.vue'
import PlayGraph from './play/PlayGraph.vue'
import PlayDefects from './play/PlayDefects.vue'
import PlayShip from './play/PlayShip.vue'
import Button from './ui/Button.vue'

const { st, act, goStage, fix, done, actionLabel, track, hud, meta } = usePlayTheLoop()
const SCREENS = [PlayFeature, PlayTasks, PlayGraph, PlayDefects, PlayShip]
</script>

<template>
  <section id="play" class="play" aria-labelledby="play-h">
    <div class="hud-bar play-hud">
      <span class="play-hud-label"><span class="hud-accent">LEVEL 0</span> · PLAY THE LOOP · FEATURE #139</span>
      <span class="play-hud-stats">
        <span class="play-hud-stat">STAGE <b>{{ hud.stageNo }}/5</b></span>
        <span class="play-hud-stat">TASKS DONE <b class="is-green">{{ hud.doneCount }}</b></span>
        <span class="play-hud-stat">OPEN DEFECTS <b class="is-gold">{{ hud.openDefects }}</b></span>
      </span>
    </div>
    <div class="play-head">
      <h2 id="play-h" class="play-title">Walk one feature from idea to ship.</h2>
      <p class="play-lead">Feature #139 — the Pixel theme you're looking at — replayed in five screens. Press the button on each one.</p>
    </div>
    <PlayTrack :track="track" @go="goStage" />
    <div class="play-body">
      <div class="play-text">
        <p class="play-kicker">{{ meta.kicker }}</p>
        <h3 class="play-stage-title">{{ meta.title }}</h3>
        <p class="play-stage-body">{{ meta.body }}</p>
        <code class="play-cmd">{{ meta.cmd }}</code>
        <Button variant="bare" class="play-act" :class="{ 'is-done': done }" :disabled="st.busy" @click="act">{{ actionLabel }}</Button>
      </div>
      <div class="play-screen">
        <div class="play-screen-bar">
          <span class="play-screen-title">{{ meta.screen }}</span><span>REGIN · PIXEL</span>
        </div>
        <div class="play-screen-body">
          <component :is="SCREENS[st.stage]" :st="st" @fix="fix" />
        </div>
      </div>
    </div>
  </section>
</template>
