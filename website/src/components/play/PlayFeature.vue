<script setup>
import { computed } from 'vue'

import { PT } from '../../content/play.js'

const props = defineProps({ st: { type: Object, required: true } })
const typed = computed(() => PT.slice(0, props.st.typed))
</script>

<template>
  <div class="play-field-wrap">
    <span class="play-micro-label">FEATURE TITLE</span>
    <div class="play-field" :class="{ 'is-typing': st.typed > 0 }">
      <span class="play-field-id">#139</span><span class="play-field-text">{{ typed }}</span><span class="play-caret" :class="{ 'is-off': st.specced }"></span>
    </div>
  </div>
  <div class="play-spec play-spec-tiles" :class="{ 'is-specced': st.specced }">
    <div class="play-tile"><span class="play-tile-n">10</span><span class="play-tile-k">DESIGN IMAGES</span></div>
    <div class="play-tile"><span class="play-tile-n">28</span><span class="play-tile-k">GHERKIN TESTS</span></div>
    <div class="play-tile"><span class="play-tile-n is-green">15</span><span class="play-tile-k">SLICES</span></div>
  </div>
  <pre class="play-pre play-spec" :class="{ 'is-specced': st.specced }"><span class="is-dim">Scenario:</span> switching keeps place
<span class="is-dim">Scenario:</span> unknown stored value
<span class="is-dim">Scenario:</span> a theme chunk that fails to load
<span class="is-dim">Scenario:</span> colour budget
<span class="is-dim">… 24 more</span></pre>
</template>
