<script setup>
// Arming is the privileged step: this form is the only thing in the browser
// that may write a task's execution parameters, and its fields are exactly the
// arm allowlist. The menus are served, not hardcoded — models, permission
// modes, efforts and repos describe the install this page is driving.
import { computed, onMounted, reactive, ref, watch } from 'vue'
import Button from '../ui/Button.vue'
import Checkbox from '../ui/Checkbox.vue'
import Dialog from '../ui/Dialog.vue'
import Input from '../ui/Input.vue'
import LaunchWorktreeFields from '../live/LaunchWorktreeFields.vue'
import Select from '../ui/Select.vue'
import PlGlyph from '../pipeline/PlGlyph.vue'
import PlSlot from '../pipeline/PlSlot.vue'
import TaskArmSkills from './TaskArmSkills.vue'
import { CUSTOM_MODEL, declares, useTaskArm } from '../../composables/useTaskArm.js'
import api from '../../api'
import {
  blankArmDraft, cwdWithin, forgetArm, hasRecalledValues, prefilledArmDraft,
  projectCwd, rememberArm, seededArmDraft,
} from '../../composables/useArmPrefs.js'
import { useProjects } from '../../composables/useProjects.js'
import { useFlash } from '../../composables/useFlash.js'
import { taskTitle } from '../../utils/taskTitle.js'

const props = defineProps({
  task: { type: Object, required: true },
})
const emit = defineEmits(['close', 'armed'])

const {
  options, loading, error, saving, armError,
  skillCatalog, loadSkills, pruneUnavailableSkills, skillMissing,
  cwdOptions, modeOptions, modelOptions, effortOptions,
  providerRow, providerOptions, launchOptions,
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
let recalledFields = seeded.recalledFields

// The last attempt's cwd outranks the project's (R4b): it is where this very
// task last ran, not a default for its filing.
const attemptCwd = ref('')
let higher = {}
// The form stays hidden until sources 2 and 3 have answered — they replace the
// draft wholesale, and anything typed before they land would be overwritten —
// and until the first skill menu has pruned the recalled skills, or Arm could
// send a remembered skill the run's directory lacks.
const prefilling = ref(true)

// Seeded after the recall banner is settled: a cwd that came from the task's
// own project is not a carry-over from another task, and must not raise it.
function seedProjectCwd() {
  if (props.task?.armed?.params || attemptCwd.value) return
  const cwd = projectCwd(props.task, projects.value)
  if (cwd !== null) draft.cwd = cwd
}

// Sources 2 and 3 of R4b. Neither is an error when absent: a task that is no
// slice has no proposal (404), and a task never run has no attempt.
async function layerHigherSources() {
  if (props.task?.armed?.params || props.task?.id == null) return
  const [proposal, history] = await Promise.all([
    api.get(`/tasks/${props.task.id}/arm-proposal`).catch(() => null),
    api.get(`/tasks/${props.task.id}/runs`).catch(() => null),
  ])
  const lastRun = history?.runs?.[0] || null
  if (!proposal && !lastRun) return
  higher = { proposal, lastRun }
  const seeded = prefilledArmDraft(props.task, { proposal, lastRun })
  Object.assign(draft, seeded.draft)
  recalled.value = seeded.recalled
  recalledFields = seeded.recalledFields
  attemptCwd.value = lastRun?.params?.cwd || ''
}

// Registered in setup so it stops with the sheet; it starts answering only
// after mount, whose own load has already read the first cwd.
let skillsFollowCwd = false
watch(() => draft.cwd, cwd => { if (skillsFollowCwd) loadSkills(cwd, draft.provider) })

// Launch mode is per slice, never remembered (D1 of #120), and offered only
// as the picked provider declares it.
const provider = computed(() => providerRow(draft.provider))
const shows = field => declares(provider.value, field)
const launchChoices = computed(() => launchOptions(draft.provider))
const missingBinary = computed(() => provider.value && !provider.value.available
  ? provider.value.binary || provider.value.id : '')

// A model is one CLI's id, so a switch drops it rather than send `opus` to
// kimi; a launch mode the new provider does not declare goes back to its
// structured channel.
watch(() => draft.provider, () => {
  if (prefilling.value) return
  draft.model = ''
  draft.modelCustom = ''
  if (!launchChoices.value.some(o => o.value === draft.launch)) draft.launch = ''
  loadSkills(draft.cwd, draft.provider)
})

onMounted(async () => {
  try {
    await settleDraft()
  } finally {
    prefilling.value = false
    skillsFollowCwd = true
  }
})

async function settleDraft() {
  await Promise.all([loadOptions(), ensureProjects(), layerHigherSources()])
  if (recalled.value) {
    pruneUnlisted(draft)
    recalled.value = hasRecalledValues(draft)
  }
  seedProjectCwd()
  adoptUnlisted(draft)
  await loadSkills(draft.cwd, draft.provider)
  if (recalledFields.includes('skills')) {
    pruneUnavailableSkills(draft)
    recalled.value = hasRecalledValues(draft)
  }
}

// Reset drops only what the prefs carried: the plan and the history are this
// task's own, so they come straight back.
function resetDraft() {
  forgetArm()
  Object.assign(draft, blankArmDraft(props.task),
                prefilledArmDraft({ ...props.task, armed: null }, higher).draft)
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

const { flash } = useFlash()

async function submit() {
  if (!await arm(props.task, draft)) return
  rememberArm(draft)
  flash(`#${props.task.id} armed`, 'success',
    { link: { to: `/tasks/${props.task.id}`, label: 'Open' } })
  emit('armed')
}
</script>

<template>
  <Dialog
    :open="true"
    title="Arm this task"
    short-title="Arm task"
    :description="taskTitle(task)"
    :kicker="`Idea → queue · #${task.id}`"
    hint="Esc close"
    data-pl-part="dialog"
    data-testid="task-arm"
    @update:open="v => !v && emit('close')"
  >
    <template #mark><PlGlyph kind="dialog.arm" :scale="2" /></template>
    <div v-if="loading || prefilling" class="task-arm-note">reading launch options…</div>
    <div v-else-if="error" class="live-launch-warn" data-testid="task-arm-error">
      {{ error }}
    </div>
    <div v-else class="live-launch" data-testid="task-arm-form">
      <p v-if="recalled" class="task-arm-recalled" data-pl-part="dialog-note" data-testid="task-arm-recalled">
        <span>Filled in from your last arm — check it before you arm this one.</span>
        <Button variant="ghost" size="sm" data-pl-part="key" data-testid="task-arm-reset" @click="resetDraft">
          Reset
        </Button>
      </p>

      <!-- Sections are display:contents here, so the form reads as the one
           column it always was; a theme may lay each out on its own grid. -->
      <div class="task-arm-sec" data-pl-part="dialog-section" data-pl-section="Where">
        <label class="live-launch-lbl" for="task-arm-cwd" data-pl-part="field-label">working directory</label>
        <Select
          id="task-arm-cwd"
          v-model="draft.cwd"
          block
          :options="cwdChoices"
          data-pl-part="input"
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
      </div>

      <div class="task-arm-sec" data-pl-part="dialog-section" data-pl-section="Agent">
        <template v-if="providerOptions.length">
          <label class="live-launch-lbl" for="task-arm-provider" data-pl-part="field-label" data-pl-field="provider">agent</label>
          <Select
            id="task-arm-provider"
            v-model="draft.provider"
            block
            :options="providerOptions"
            data-pl-part="input"
            data-pl-field="provider"
            data-testid="task-arm-provider"
            aria-label="Agent provider"
          />
          <p v-if="missingBinary" class="task-arm-note" role="status" data-testid="task-arm-provider-missing">
            <code>{{ missingBinary }}</code> is not on the server's PATH, so this run will be refused until it is installed.
          </p>
        </template>

        <label v-if="shows('permission_mode')" class="live-launch-lbl" for="task-arm-mode" data-pl-part="field-label" data-pl-field="mode">permission mode</label>
        <Select
          v-if="shows('permission_mode')"
          id="task-arm-mode"
          v-model="draft.permission_mode"
          block
          :options="modeOptions"
          data-pl-part="input"
          data-pl-field="mode"
          data-testid="task-arm-mode"
          aria-label="Permission mode"
        />

        <label v-if="shows('model')" class="live-launch-lbl" for="task-arm-model" data-pl-part="field-label" data-pl-field="model">model</label>
        <Select
          v-if="shows('model')"
          id="task-arm-model"
          v-model="draft.model"
          block
          :options="modelOptions(draft.provider)"
          data-pl-part="input"
          data-pl-field="model"
          data-testid="task-arm-model"
          aria-label="Model"
        />
        <Input
          v-if="shows('model') && draft.model === CUSTOM_MODEL"
          v-model="draft.modelCustom"
          data-pl-part="input"
          data-pl-field="custom"
          data-testid="task-arm-model-custom"
          placeholder="e.g. claude-opus-4-8"
          aria-label="Custom model id"
        />

        <label v-if="shows('effort')" class="live-launch-lbl" for="task-arm-effort" data-pl-part="field-label" data-pl-field="effort">effort</label>
        <Select
          v-if="shows('effort')"
          id="task-arm-effort"
          v-model="draft.effort"
          block
          :options="effortOptions"
          data-pl-part="input"
          data-pl-field="effort"
          data-testid="task-arm-effort"
          aria-label="Reasoning effort"
        />

        <TaskArmSkills
          v-model:skills="draft.skills"
          v-model:args="draft.skillArgs"
          :catalog="skillCatalog"
          :missing="skillMissing"
          :cwd="draft.cwd"
          data-pl-part="skills"
          data-pl-field="skills"
        />
      </div>

      <div class="task-arm-sec" data-pl-part="dialog-section" data-pl-section="Finish">
        <label class="live-launch-lbl" for="task-arm-verify" data-pl-part="field-label">
          verify command <span class="task-arm-note" data-pl-part="field-hint">Gate — exit 0 marks the task done</span>
        </label>
        <Input
          id="task-arm-verify"
          v-model="draft.verify_cmd"
          data-pl-part="input"
          data-pl-field="verify"
          data-testid="task-arm-verify"
          placeholder="e.g. .venv/bin/python -m pytest -q"
          aria-label="Verify command (gate — exit 0 marks the task done)"
        />

        <label class="live-launch-lbl" for="task-arm-launch" data-pl-part="field-label">launch</label>
        <PlSlot
          id="task-arm-launch"
          v-model="draft.launch"
          name="ChoiceField"
          :options="launchChoices"
          data-testid="task-arm-launch"
          aria-label="Launch mode"
        />
        <p v-if="draft.launch === 'tmux' && options?.detached_detail" class="task-arm-note" data-testid="task-arm-launch-off">
          a tmux run will be refused here: {{ options.detached_detail }}.
        </p>

        <label class="live-launch-check" data-pl-part="check">
          <Checkbox v-model="draft.one_shot" data-testid="task-arm-oneshot" />
          <span>end the session once the agent reports finished</span>
        </label>
      </div>

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
      <Button variant="ghost" data-pl-part="key" @click="emit('close')">Cancel</Button>
      <Button
        variant="primary"
        :disabled="saving || loading || prefilling || !!error"
        data-pl-part="key--primary"
        data-pl-mark="✦"
        data-testid="task-arm-submit"
        @click="submit"
      >
        {{ saving ? 'Arming…' : 'Arm task' }}
      </Button>
    </template>
  </Dialog>
</template>

<style scoped>
.task-arm-sec { display: contents; }

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
