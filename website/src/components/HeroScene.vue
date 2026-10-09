<script setup>
import PixelSprite from './play/PixelSprite.vue'
import { BUG_PALETTE, BUG_ROWS, ROBOT_FRAME_1, ROBOT_FRAME_2, ROBOT_PALETTE } from '../content/play.js'
</script>

<template>
  <div class="hero-scene" role="img" aria-label="A pixel robot walks the loop: spec, run, watch, then counts a defect.">
    <div class="rg-ground"></div>
    <div v-for="i in 4" :key="i" :class="['rg-pad', `rg-pad-${i - 1}`]" data-rg-anim></div>
    <div class="rg-robot" data-rg-anim aria-hidden="true">
      <div class="rg-frame rg-frame-1" data-rg-anim><PixelSprite :rows="ROBOT_FRAME_1" :palette="ROBOT_PALETTE" /></div>
      <div class="rg-frame rg-frame-2" data-rg-anim><PixelSprite :rows="ROBOT_FRAME_2" :palette="ROBOT_PALETTE" /></div>
    </div>
    <div class="rg-bug" aria-hidden="true">
      <div class="rg-bug-squash" data-rg-anim>
        <div class="rg-bug-wiggle" data-rg-anim><PixelSprite :rows="BUG_ROWS" :palette="BUG_PALETTE" /></div>
      </div>
    </div>
    <div class="rg-pop" data-rg-anim aria-hidden="true">+1 DEFECT</div>
  </div>
</template>
