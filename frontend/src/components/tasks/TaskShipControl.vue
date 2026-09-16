<script setup>
// The status cell, and — on a top-level task — the done-claim beside it.
//
// The claim is an operator act and nothing derives it. A child run settling
// `done` says *this session finished*, never *this feature shipped*: deriving
// one from the other would flip a feature to `shipped` when its first build
// session ended, and turn every defect after that into an escape.
//
// So the control exists only where the claim is meaningful. A task with a
// parent is a run of something and renders the plain badge, because the
// server refuses the claim on it and a button that always errors is worse
// than no button. A top-level task holding runs of its own is the feature
// those runs built, and is claimable.
import { computed, ref } from 'vue'
import Badge from '../Badge.vue'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import { taskStatusColor } from '../../composables/useBadgeColor.js'

const props = defineProps({
  task: { type: Object, required: true },
  canWrite: { type: Boolean, default: false },
})
const emit = defineEmits(['changed'])

const busy = ref(false)
const failed = ref('')

// The grain, read the same way the server reads it before refusing.
const isFeature = computed(() => props.task.parent_id === null)
// The claim, never the status: a task's own run settling writes `done`
// onto it and says nothing about whether the feature shipped.
const shipped = computed(() => !!props.task.claimed_at)
const claimable = computed(
  () => props.canWrite && isFeature.value
    && ['idea', 'running', 'failed', 'done'].includes(props.task.status))

async function move(status) {
  busy.value = true
  failed.value = ''
  const body = await (await import('../../api')).default
    .patch(`/tasks/${props.task.id}`, { status })
  busy.value = false
  if (body?.ok === false) {
    // Inline and specific: the server's refusal names which grain it refused
    // on, and a toast would take that sentence away from the row it explains.
    failed.value = body.msg || 'the claim was refused'
    return
  }
  emit('changed', body.task)
}
</script>

<template>
  <div class="ts-wrap">
    <div class="ts-row">
      <Badge :color="taskStatusColor(task.status)" :label="task.status" />

      <Button
        v-if="claimable && !shipped"
        variant="secondary" size="sm" :disabled="busy"
        data-testid="task-ship"
        :aria-label="`Claim task ${task.id} done`"
        @click="move('done')"
      >
        <Icon name="check" :size="12" />
        {{ busy ? 'Shipping…' : 'Ship' }}
      </Button>

      <!-- The un-claim costs no round: nothing was built between the claim and
           withdrawing it, so nothing was walked back. -->
      <Button
        v-else-if="claimable"
        variant="ghost" size="sm" :disabled="busy"
        data-testid="task-unclaim"
        :aria-label="`Withdraw the done-claim on task ${task.id}`"
        @click="move('idea')"
      >{{ busy ? 'Withdrawing…' : 'Un-claim' }}</Button>
    </div>

    <p v-if="failed" class="ts-fail" role="status" data-testid="task-ship-error">
      {{ failed }}
    </p>
  </div>
</template>

<style scoped>
.ts-wrap { display: flex; flex-direction: column; gap: 0.3rem; }
.ts-row { display: flex; align-items: center; gap: 0.4rem; flex-wrap: wrap; }
.ts-fail {
    margin: 0; font-size: 11.5px; color: var(--color-amber-800);
    background: var(--color-amber-50); border-radius: 0.4rem;
    padding: 0.25rem 0.45rem;
}
</style>
