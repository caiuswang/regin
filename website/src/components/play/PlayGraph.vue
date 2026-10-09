<script setup>
import { computed } from 'vue'

import { P_COLS, P_NODES, P_TIMELINE } from '../../content/play.js'

const props = defineProps({ st: { type: Object, required: true } })

const ST_LABEL = { done: 'DONE', run: 'RUNNING', ready: 'READY', wait: 'WAITING' }
const LAST_TICK = P_TIMELINE.length - 1

function nodeState(i, tick) {
  const [running, done] = P_TIMELINE[tick]
  if (done.includes(i)) return 'done'
  if (running.includes(i)) return 'run'
  return tick === 0 && i === 0 ? 'ready' : 'wait'
}

const cols = computed(() => P_COLS.map((col, ci) => ({
  key: ci,
  arrow: ci < P_COLS.length - 1,
  arrowDone: col.every((i) => nodeState(i, props.st.tick) === 'done'),
  nodes: col.map((i) => ({ id: P_NODES[i][0], name: P_NODES[i][1], state: nodeState(i, props.st.tick) })),
})))

const note = computed(() => {
  if (props.st.tick === 0) return 'Only S1 has nothing upstream, so only S1 is ready.'
  if (props.st.tick >= LAST_TICK) return 'Every slice done. 28 task runs, no hand-typed prompt.'
  return 'Runs finish → dependents are re-checked and released.'
})
</script>

<template>
  <div class="play-graph-scroll">
    <div class="play-graph">
      <template v-for="col in cols" :key="col.key">
        <div class="play-graph-col">
          <div v-for="n in col.nodes" :key="n.id" :class="['play-node', `is-${n.state}`]">
            <span class="play-node-head"><span class="play-node-id">{{ n.id }}</span><span class="play-node-st">{{ ST_LABEL[n.state] }}</span></span>
            <span class="play-node-name">{{ n.name }}</span>
          </div>
        </div>
        <span v-if="col.arrow" class="play-graph-arrow" :class="{ 'is-done': col.arrowDone }" aria-hidden="true">▶</span>
      </template>
    </div>
  </div>
  <p class="play-graph-note">{{ note }}</p>
</template>
