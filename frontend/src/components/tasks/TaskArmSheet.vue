<script setup>
// Arming is the privileged step: this form is the only thing in the browser
// that may write a task's execution parameters, and its fields are exactly the
// arm allowlist. The menus are served, not hardcoded — models, permission
// modes, efforts and repos describe the install this page is driving.
import { onMounted, reactive } from 'vue'
import Button from '../ui/Button.vue'
import Checkbox from '../ui/Checkbox.vue'
import Dialog from '../ui/Dialog.vue'
import Input from '../ui/Input.vue'
import LaunchWorktreeFields from '../live/LaunchWorktreeFields.vue'
import Select from '../ui/Select.vue'
import { CUSTOM_MODEL, CUSTOM_SKILL, armDraft, useTaskArm } from '../../composables/useTaskArm.js'

const props = defineProps({
  task: { type: Object, required: true },
})
const emit = defineEmits(['close', 'armed'])

const {
  options, loading, error, saving, armError, skillOptions,
  cwdOptions, modeOptions, modelOptions, effortOptions,
  loadOptions, adoptUnlisted, arm,
} = useTaskArm()

const draft = reactive(armDraft(props.task))

onMounted(async () => {
  await loadOptions()
  adoptUnlisted(draft)
})

async function submit() {
  if (await arm(props.task, draft)) emit('armed')
}
</script>

<template>
  <Dialog
    :open="true"
    title="Arm this task"
    :description="task.title"
    @update:open="v => !v && emit('close')"
  >
    <div v-if="loading" class="task-arm-note">reading launch options…</div>
    <div v-else-if="error" class="live-launch-warn" data-testid="task-arm-error">
      {{ error }}
    </div>
    <div v-else class="live-launch" data-testid="task-arm-form">
      <label class="live-launch-lbl" for="task-arm-cwd">working directory</label>
      <Select
        id="task-arm-cwd"
        v-model="draft.cwd"
        block
        :options="cwdOptions"
        data-testid="task-arm-cwd"
        aria-label="Working directory"
      />

      <LaunchWorktreeFields
        v-model:enabled="draft.useWorktree"
        v-model:branch="draft.worktreeBranch"
        v-model:base="draft.worktreeBase"
        :cwd="draft.cwd"
      />

      <label class="live-launch-lbl" for="task-arm-mode">permission mode</label>
      <Select
        id="task-arm-mode"
        v-model="draft.permission_mode"
        block
        :options="modeOptions"
        data-testid="task-arm-mode"
        aria-label="Permission mode"
      />

      <label class="live-launch-lbl" for="task-arm-model">model</label>
      <Select
        id="task-arm-model"
        v-model="draft.model"
        block
        :options="modelOptions"
        data-testid="task-arm-model"
        aria-label="Model"
      />
      <Input
        v-if="draft.model === CUSTOM_MODEL"
        v-model="draft.modelCustom"
        data-testid="task-arm-model-custom"
        placeholder="e.g. claude-opus-4-8"
        aria-label="Custom model id"
      />

      <label class="live-launch-lbl" for="task-arm-effort">effort</label>
      <Select
        id="task-arm-effort"
        v-model="draft.effort"
        block
        :options="effortOptions"
        data-testid="task-arm-effort"
        aria-label="Reasoning effort"
      />

      <label class="live-launch-lbl" for="task-arm-skill">
        skill <span class="task-arm-note">— the run opens with <code>/&lt;skill&gt;</code> and the task as its subject</span>
      </label>
      <Select
        id="task-arm-skill"
        v-model="draft.skill"
        block
        :options="skillOptions"
        data-testid="task-arm-skill"
        aria-label="Skill to invoke"
      />
      <Input
        v-if="draft.skill === CUSTOM_SKILL"
        v-model="draft.skillCustom"
        data-testid="task-arm-skill-custom"
        placeholder="e.g. /my-local-skill"
        aria-label="Custom skill name"
      />
      <Input
        v-if="draft.skill"
        v-model="draft.skillArgs"
        data-testid="task-arm-skill-args"
        placeholder="extra arguments, before the task body"
        aria-label="Skill arguments"
      />

      <label class="live-launch-lbl" for="task-arm-verify">
        verify command <span class="task-arm-note">— recorded, not yet run</span>
      </label>
      <Input
        id="task-arm-verify"
        v-model="draft.verify_cmd"
        data-testid="task-arm-verify"
        placeholder="e.g. .venv/bin/python -m pytest -q"
        aria-label="Verify command (recorded, not yet run)"
      />

      <label class="live-launch-check">
        <Checkbox v-model="draft.one_shot" data-testid="task-arm-oneshot" />
        <span>end after the first turn</span>
      </label>

      <p v-if="armError" class="live-launch-warn" role="alert" data-testid="task-arm-refusal">
        {{ armError.message }}
        <span v-if="armError.field"> — rejected field: <code>{{ armError.field }}</code></span>
      </p>
      <!-- Arming still writes parameters with the SDK tier off — it is the run
           that would be refused, and saying so here is earlier than finding out
           from a 409. -->
      <p v-if="!options?.enabled" class="task-arm-note" data-testid="task-arm-sdk-off">
        the agent SDK tier is off, so an armed task will refuse to run until
        <code>agent_sdk.enabled</code> is set.
      </p>
    </div>

    <template #footer>
      <Button variant="ghost" @click="emit('close')">Cancel</Button>
      <Button
        variant="primary"
        :disabled="saving || loading || !!error"
        data-testid="task-arm-submit"
        @click="submit"
      >
        {{ saving ? 'Arming…' : 'Arm task' }}
      </Button>
    </template>
  </Dialog>
</template>

<style scoped>
.task-arm-note {
    font-size: 0.8125rem;
    color: var(--color-fg-muted);
}
</style>
