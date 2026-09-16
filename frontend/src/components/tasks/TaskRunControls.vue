<script setup>
// The control strip on a task row. What it offers is decided by the status the
// server reports MERGED with liveness, never by the status alone: a `running`
// row with nothing behind it is exactly what a killed runner leaves, and the
// operator's way out of that is another Run — so the button stays.
//
// Until liveness answers, a `running` row offers nothing: "Run again" over a
// live agent launches a second one.
import { computed } from 'vue'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'

const props = defineProps({
  task: { type: Object, required: true },
  liveness: { type: Object, default: null },
  busy: { type: Boolean, default: false },
})
defineEmits(['control'])

const RUNNABLE = ['armed', 'queued', 'failed', 'done']

const claimed = computed(() => props.task.status === 'running')
const running = computed(() => claimed.value && !!props.liveness?.live)
const canRun = computed(() => !running.value
  && (RUNNABLE.includes(props.task.status)
      || (claimed.value && !!props.liveness)))
</script>

<template>
  <template v-if="running">
    <Button
      variant="secondary"
      size="sm"
      :disabled="busy"
      :aria-label="`Interrupt ${task.title}`"
      data-testid="task-interrupt"
      @click="$emit('control', 'interrupt')"
    >
      <Icon name="pause" :size="13" />
      Interrupt
    </Button>
    <Button
      variant="danger"
      size="sm"
      :disabled="busy"
      :aria-label="`Stop ${task.title}`"
      data-testid="task-stop"
      @click="$emit('control', 'stop')"
    >
      <Icon name="square" :size="13" />
      Stop
    </Button>
  </template>
  <Button
    v-else-if="canRun"
    variant="primary"
    size="sm"
    :disabled="busy"
    :aria-label="`Run ${task.title}`"
    data-testid="task-run"
    @click="$emit('control', 'run')"
  >
    <Icon name="play" :size="13" />
    {{ task.status === 'running' ? 'Run again' : 'Run' }}
  </Button>
</template>
