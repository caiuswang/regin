<script setup>
// The "Repeat" half of the schedule form: a frequency plus the one or two
// fields that frequency needs. Only Custom exposes a raw cron expression.
import Button from '../ui/Button.vue'
import Input from '../ui/Input.vue'
import Select from '../ui/Select.vue'
import { FREQUENCIES, WEEKDAYS } from '../../utils/taskSchedule.js'

const props = defineProps({
  draft: { type: Object, required: true },
})
const emit = defineEmits(['update'])

function toggleDay(day) {
  const days = props.draft.days.includes(day)
    ? props.draft.days.filter(d => d !== day)
    : [...props.draft.days, day]
  emit('update', { days })
}
</script>

<template>
  <div class="tsr">
    <label class="tsk-row">
      <span class="tsk-field">Repeats</span>
      <Select
        :model-value="draft.freq" :options="FREQUENCIES" aria-label="How often"
        data-testid="task-schedule-freq" @update:model-value="v => emit('update', { freq: v })"
      />
    </label>
    <div v-if="draft.freq === 'weekly'" class="tsk-row">
      <span class="tsk-field">On</span>
      <div class="tsr-days" role="group" aria-label="Days of the week">
        <Button
          v-for="d in WEEKDAYS" :key="d.value" size="sm"
          :variant="draft.days.includes(d.value) ? 'primary' : 'secondary'"
          :aria-pressed="draft.days.includes(d.value)" class="tsr-day"
          @click="toggleDay(d.value)"
        >{{ d.label }}</Button>
      </div>
    </div>
    <label v-if="draft.freq === 'hourly'" class="tsk-row">
      <span class="tsk-field">At minute</span>
      <Input
        :model-value="draft.minute" type="number" min="0" max="59" class="tsr-minute"
        data-testid="task-schedule-minute" @update:model-value="v => emit('update', { minute: v })"
      />
      <span class="tsk-note">past every hour</span>
    </label>
    <label v-else-if="draft.freq === 'custom'" class="tsk-row">
      <span class="tsk-field">Cron</span>
      <Input
        :model-value="draft.cron_expr" class="tsr-cron" placeholder="0 9 * * 1-5"
        data-testid="task-schedule-cron" @update:model-value="v => emit('update', { cron_expr: v })"
      />
      <span class="tsk-note">minute hour day month weekday</span>
    </label>
    <label v-else class="tsk-row">
      <span class="tsk-field">At</span>
      <Input
        :model-value="draft.repeat_time" type="time" class="tsk-time"
        data-testid="task-schedule-repeat-time" @update:model-value="v => emit('update', { repeat_time: v })"
      />
    </label>
  </div>
</template>

<style scoped>
.tsr { display: contents; }
.tsr-days { display: inline-flex; flex-wrap: wrap; gap: 0.25rem; }
.tsr-day { min-width: 2.75rem; }
.tsr-minute { width: 5rem; }
.tsr-cron { font-family: var(--font-mono, ui-monospace, monospace); width: 12rem; }
</style>
