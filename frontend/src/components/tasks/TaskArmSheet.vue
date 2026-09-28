<script setup>
// Arming is the privileged step: this form is the only thing in the browser
// that may write a task's execution parameters, and its fields are exactly the
// arm allowlist. The menus are served, not hardcoded — models, permission
// modes, efforts and repos describe the install this page is driving.
import { computed, onMounted, reactive, ref } from 'vue'
import Button from '../ui/Button.vue'
import Checkbox from '../ui/Checkbox.vue'
import Dialog from '../ui/Dialog.vue'
import Input from '../ui/Input.vue'
import LaunchWorktreeFields from '../live/LaunchWorktreeFields.vue'
import Select from '../ui/Select.vue'
import { CUSTOM_MODEL, CUSTOM_SKILL, useTaskArm } from '../../composables/useTaskArm.js'
import {
  blankArmDraft, cwdWithin, forgetArm, hasRecalledValues, projectCwd,
  rememberArm, seededArmDraft,
} from '../../composables/useArmPrefs.js'
import { useProjects } from '../../composables/useProjects.js'

const props = defineProps({
  task: { type: Object, required: true },
})
const emit = defineEmits(['close', 'armed'])

const {
  options, loading, error, saving, armError, skillOptions,
  cwdOptions, modeOptions, modelOptions, effortOptions,
  loadOptions, adoptUnlisted, pruneUnlisted, arm,
} = useTaskArm()

const { items: projects, ensure: ensureProjects, byId } = useProjects()

const seeded = seededArmDraft(props.task)
const draft = reactive(seeded.draft)
// Surfaced, never silent. Arming is the one browser write that sets execution
// parameters, so a form filled from another task's pick has to say so — an
// operator who cannot see that `bypassPermissions` was carried over would arm
// it by pressing the button they always press.
const recalled = ref(seeded.recalled)

// Seeded after the recall banner is settled: a cwd that came from the task's
// own project is not a carry-over from another task, and must not raise it.
function seedProjectCwd() {
  if (props.task?.armed?.params) return
  const cwd = projectCwd(props.task, projects.value)
  if (cwd !== null) draft.cwd = cwd
}

onMounted(async () => {
  await Promise.all([loadOptions(), ensureProjects()])
  if (recalled.value) {
    pruneUnlisted(draft)
    recalled.value = hasRecalledValues(draft)
  }
  seedProjectCwd()
  adoptUnlisted(draft)
})

function resetDraft() {
  Object.assign(draft, blankArmDraft(props.task))
  forgetArm()
  recalled.value = false
  seedProjectCwd()
}

const projectRepo = computed(() => byId(props.task?.project_id))

// Offered even when `repo_paths` does not list them: a Select whose value is
// not among its options renders blank, and hides where the run will go.
const cwdChoices = computed(() => {
  const listed = new Set(cwdOptions.value.map(o => o.value))
  const extra = [...new Set([draft.cwd, projectRepo.value?.path])]
    .filter(path => path && !listed.has(path))
  return [...cwdOptions.value, ...extra.map(path => ({ value: path, label: path }))]
})

// A note, never a refusal: one run has one directory, and a step of a
// multi-repo feature may legitimately run somewhere its filing does not say.
const cwdMismatch = computed(() => {
  const repo = projectRepo.value
  if (!repo?.path || cwdWithin(draft.cwd, repo.path)) return ''
  return `Filed under ${repo.name} but will run in `
    + `${draft.cwd || 'the server working directory'}.`
})

async function submit() {
  if (!await arm(props.task, draft)) return
  rememberArm(draft)
  emit('armed')
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
      <p v-if="recalled" class="task-arm-recalled" data-testid="task-arm-recalled">
        <span>Filled in from your last arm — check it before you arm this one.</span>
        <Button variant="ghost" size="sm" data-testid="task-arm-reset" @click="resetDraft">
          Reset
        </Button>
      </p>

      <label class="live-launch-lbl" for="task-arm-cwd">working directory</label>
      <Select
        id="task-arm-cwd"
        v-model="draft.cwd"
        block
        :options="cwdChoices"
        data-testid="task-arm-cwd"
        aria-label="Working directory"
      />
      <p v-if="cwdMismatch" class="task-arm-note" role="status" data-testid="task-arm-cwd-mismatch">
        {{ cwdMismatch }}
      </p>

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
        verify command <span class="task-arm-note">Gate — exit 0 marks the task done</span>
      </label>
      <Input
        id="task-arm-verify"
        v-model="draft.verify_cmd"
        data-testid="task-arm-verify"
        placeholder="e.g. .venv/bin/python -m pytest -q"
        aria-label="Verify command (gate — exit 0 marks the task done)"
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

.task-arm-recalled {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    flex-wrap: wrap;
    margin: 0 0 4px;
    padding: 8px 10px;
    border: 1px solid var(--color-info-soft);
    border-radius: var(--radius-md);
    background: var(--color-info-soft);
    font-size: 0.8125rem;
    line-height: 1.45;
    color: var(--color-info-strong);
}
</style>
