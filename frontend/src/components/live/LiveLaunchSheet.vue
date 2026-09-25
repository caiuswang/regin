<script setup>
// Launch form for the /live card's "new run" bottom sheet — the client half of
// the SDK tier (`POST /api/agent-runs`), which until now had no consumer at
// all. The offered working directories, models and permission modes come from
// /agent-runs/launch-options rather than being hardcoded here: they describe
// the install this browser is driving, and a client that guessed them would
// drift from it.
//
// The same form doubles as "continue a session": any session the CLI can still
// reopen is a candidate, picked from a searchable list (`LiveResumePicker`),
// not just whichever one /live happens to be open on. Whether a pick continues
// the trace in view or opens a new one is the server's answer, carried on the
// row — a stopped run regin launched is resumed as itself, while a session the
// user drove in a terminal is reopened by id under a fresh trace.
import { ref, computed, onMounted } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import Checkbox from '../ui/Checkbox.vue'
import Icon from '../ui/Icon.vue'
import Input from '../ui/Input.vue'
import LaunchKindField from './LaunchKindField.vue'
import LaunchWorktreeFields from './LaunchWorktreeFields.vue'
import LiveResumePicker from './LiveResumePicker.vue'
import Select from '../ui/Select.vue'
import Textarea from '../ui/Textarea.vue'
import { useLaunchDraft } from '../../composables/useLaunchDraft.js'
import { resumeKindLabel } from '../../utils/resumeKind.js'
import { slugifyBranch } from '../../utils/worktreeSlug.js'
import { shortTraceId } from '../../utils/traceFormatters.js'

const props = defineProps({
  resumeFrom: { type: String, default: '' },
  // The working directory of the page the form was opened from, offered as the
  // starting pick. A session page knows which repo the operator is looking at,
  // and making them find it again in a menu of every registered repo is asking
  // them to re-answer a question the page already answered.
  initialCwd: { type: String, default: '' },
  // Which surface this form belongs to, for the draft's scope. `/live` has no
  // identity beyond the card it is open on, so it passes none and the session
  // id below stands in; the desktop dialogs are their own surfaces and must
  // say so, or a draft typed on the sessions LIST — a repo, a branch, a ticked
  // worktree box — walks onto an unrelated session's page and launches there.
  scope: { type: String, default: '' },
})
const emit = defineEmits(['launched'])

const opts = ref(null)
const loading = ref(true)
const error = ref(null)
const launching = ref(false)
const refusal = ref(null)
// A run that started but whose mode made gating inert. It is held here instead
// of navigating straight through, because that warning is the one thing an
// operator must not miss by being scrolled off a card they just opened.
const held = ref(null)

// Everything the operator typed or picked lives in the module-scoped draft, so
// dismissing the sheet does not discard it (see useLaunchDraft). `picking` is
// deliberately NOT in there: which step the form was on is a view position, and
// reopening should land on the form the draft describes, not back inside the
// picker the operator was leaving.
const {
  kind, prompt, cwd, model, modelCustom, effort, mode, oneShot, resume, beforePick,
  useWorktree, worktreeBranch, worktreeBase, resetDraft,
} = useLaunchDraft(props.scope || props.resumeFrom)
const picking = ref(false)

// Sentinel for "not one of the offered aliases". The launch route accepts any
// model string, so an operator pinning an exact id (`claude-opus-4-8`) must
// stay expressible — the menu decides what is one tap away, not what is legal.
const CUSTOM_MODEL = '__custom__'

// A resumed session's cwd is not a preference — `claude --resume` resolves the
// id relative to the working directory, so launching from anywhere else finds
// no session. The pick therefore sets it, and an entry is added for a path the
// install has not registered as a repo rather than silently falling back to the
// server default.
const cwdOptions = computed(() => {
  const known = opts.value?.cwds || []
  const extra = [resume.value?.cwd, props.initialCwd].filter(
    (p, i, all) => p && !known.includes(p) && all.indexOf(p) === i)
  return [
    { value: '', label: 'server working directory' },
    ...[...known, ...extra].map(p => ({ value: p, label: p })),
  ]
})

// The sentinel is named for what it *does* (defer to the install) rather than
// labelled "default", which the CLI also has as a real mode — two options
// reading "default" is a coin flip, not a choice.
const modeOptions = computed(() => {
  const cfg = opts.value?.default_permission_mode || 'default'
  return [
    { value: '', label: cfg === 'default' ? 'use server setting' : `use server setting (${cfg})` },
    ...(opts.value?.permission_modes || []).map(m => ({ value: m, label: m })),
  ]
})

// Both menus name the install's own default in their sentinel rather than
// saying "default": an operator who cannot see which model a blank field means
// has no way to tell whether picking one changed anything.
function deferOption(configured, noun) {
  return {
    value: '',
    label: configured ? `use server setting (${configured})` : `${noun} default`,
  }
}
const modelOptions = computed(() => [
  deferOption(opts.value?.default_model, 'CLI'),
  ...(opts.value?.models || []).map(m => ({ value: m, label: m })),
  { value: CUSTOM_MODEL, label: 'custom…' },
])
const effortOptions = computed(() => [
  deferOption(opts.value?.default_effort, 'CLI'),
  ...(opts.value?.efforts || []).map(e => ({ value: e, label: e })),
])

// A resumed run keeps the trace it continues; a reopened terminal session does
// not. Saying which stops the chip implying the tail below will keep growing
// when it will not — so the kind gets its own line rather than a suffix on the
// title, which a session title long enough to be worth reading clips away.
const resumeTitle = computed(() => {
  const row = resume.value
  return row ? row.title || shortTraceId(row.session_id) : ''
})
const resumeKind = computed(() => (
  resume.value ? resumeKindLabel(resume.value.kind) : ''))

// A recorded model is an exact id (`claude-opus-4-8`), which the menu of
// aliases does not contain — so adopting one lands in the custom slot rather
// than silently resetting the picker to "use server setting".
function adoptModel(id) {
  const known = (opts.value?.models || []).includes(id)
  model.value = id ? (known ? id : CUSTOM_MODEL) : ''
  if (id && !known) modelCustom.value = id
}

function pickResume(row) {
  if (!resume.value) {
    beforePick.value = {
      cwd: cwd.value, model: model.value, modelCustom: modelCustom.value,
    }
  }
  resume.value = row
  // Adopting the session's cwd is not a convenience: resume resolves the id
  // relative to it, so keeping the previous selection would launch a run that
  // finds nothing to continue.
  if (row.cwd) cwd.value = row.cwd
  // The model is inherited for a weaker but real reason: continuing a
  // conversation on a different model than it was held on is a change the
  // operator did not ask for and would not see. Only a run regin launched
  // recorded one, so a terminal session still falls back to the install's.
  if (row.model) adoptModel(row.model)
  picking.value = false
}

function clearResume() {
  resume.value = null
  // The adopted working directory belongs to the session that was picked;
  // left behind, it silently becomes the cwd of the next, unrelated launch.
  if (beforePick.value) {
    cwd.value = beforePick.value.cwd
    model.value = beforePick.value.model
    modelCustom.value = beforePick.value.modelCustom
    beforePick.value = null
  }
}

// Only worth saying when something is actually gated — otherwise "this mode
// skips the permission callback" warns about the loss of a control the install
// never turned on.
const isTerminal = computed(() => kind.value === 'terminal')
const shadowWarning = computed(() => (
  !isTerminal.value && opts.value?.gating_active && ['acceptEdits', 'bypassPermissions'].includes(mode.value)
    ? `${mode.value} skips the permission callback — nothing will be held for approval.`
    : null
))

// The prompt is optional, for a fresh run as much as a resume: opening the
// session *is* the act — the same one a bare `claude` performs at a terminal —
// and the card it lands on has a composer for the first turn. Requiring one
// would make "just start me a session here" impossible to express.
//
// `one_shot` is the single combination that stays refused, and it is refused
// here as well as at the server so the reason is legible before the click: a
// run that ends with its first turn, given no turn, would connect and
// immediately disconnect.
const oneShotNeedsPrompt = computed(() => (
  !isTerminal.value && oneShot.value && !prompt.value.trim()))
// Picking `custom…` and typing nothing is the one way to ask for a model and
// get the install's default instead: the body would carry no `model` key at
// all, so the run starts on something the operator did not choose and nothing
// on the card ever says so. Refused here for the same reason `one_shot` is —
// the mistake is legible before the click, not after the run.
const customModelIsBlank = computed(() => (
  model.value === CUSTOM_MODEL && !modelCustom.value.trim()))
// A resume outranks the worktree toggle rather than clearing it: the operator
// may drop the pick again, and a checkbox that silently unticked itself
// meanwhile would launch in the repo when they asked for a tree.
// A terminal launch sends no resume, so a pick parked in the draft does not
// block its worktree.
const resumeApplies = computed(() => !!resume.value && !isTerminal.value)
const worktreeActive = computed(() => useWorktree.value && !resumeApplies.value)
// The same test the fields' own warning uses, not a blank check: a name of
// pure punctuation is non-blank and slugifies to nothing, so the two readings
// disagreed — the warning appeared under a Launch button that was still live,
// and the click bought a 400 the form could have refused itself.
const worktreeNeedsBranch = computed(() => (
  worktreeActive.value && !slugifyBranch(worktreeBranch.value)))
const canLaunch = computed(() => !oneShotNeedsPrompt.value
  && !customModelIsBlank.value && !worktreeNeedsBranch.value
  && !launching.value)

onMounted(async () => {
  // Only into an empty slot: the draft outlives this component, so a seed that
  // overwrote it would undo a directory the operator picked before dismissing.
  if (props.initialCwd && !cwd.value) cwd.value = props.initialCwd
  try {
    opts.value = await api.get('/agent-runs/launch-options')
    settleKind()
  } catch (e) {
    error.value = e?.message || 'Failed to load launch options.'
  } finally {
    loading.value = false
  }
})

// A draft kind this install cannot serve (terminal switched off since, or an
// SDK-less install) moves to the one it can, rather than leaving Launch aimed
// at a disabled option.
function settleKind() {
  const ok = { sdk: !!opts.value.enabled, terminal: !!opts.value.terminal_available }
  if (ok[kind.value]) return
  const other = kind.value === 'terminal' ? 'sdk' : 'terminal'
  if (ok[other]) kind.value = other
}

const chosenModel = computed(() => (
  model.value === CUSTOM_MODEL ? modelCustom.value.trim() : model.value))

function launchBody() {
  const sdk = !isTerminal.value
  return {
    kind: sdk ? undefined : 'terminal',
    prompt: prompt.value.trim(),
    cwd: cwd.value || undefined,
    model: chosenModel.value || undefined,
    effort: effort.value || undefined,
    permission_mode: mode.value || undefined,
    one_shot: (sdk && oneShot.value) || undefined,
    resume: (sdk && resume.value?.session_id) || undefined,
    worktree: worktreeActive.value
      ? { branch: worktreeBranch.value.trim(), base: worktreeBase.value }
      : undefined,
  }
}

async function launch() {
  if (!canLaunch.value) return
  launching.value = true
  refusal.value = null
  try {
    const data = await api.post('/agent-runs', launchBody())
    if (!data.launched) {
      refusal.value = data.detail || 'Launch refused.'
      return
    }
    // A resumed run comes back under its own `sdk-…` id, which is not the id
    // the card is open on when that is the child's — same session, different
    // name for it, so the caller must not navigate away. The picked row is
    // matched on both of its ids for that reason.
    const picked = resumeApplies.value ? resume.value : null
    const launched = {
      traceId: data.trace_id,
      kind: data.kind || 'sdk',
      sameSession: !!picked && !!props.resumeFrom
        && (picked.session_id === props.resumeFrom
          || picked.run_trace_id === props.resumeFrom),
    }
    // The run exists now, so the draft that produced it is spent — reset before
    // either exit, or the held-warning path would leave it to seed the next
    // launch with a prompt that has already been sent.
    resetDraft()
    if (data.warning) {
      held.value = { launched, warning: data.warning }
      return
    }
    emit('launched', launched)
  } catch (e) {
    refusal.value = e?.message || 'Launch failed.'
  } finally {
    launching.value = false
  }
}
</script>

<template>
  <div class="live-launch">
    <div v-if="error" class="live-empty" data-testid="live-launch-error">{{ error }}</div>
    <div v-else-if="loading" class="live-empty">loading…</div>
    <div
      v-else-if="!opts.enabled && !opts.terminal_available"
      class="live-empty"
      data-testid="live-launch-disabled"
    >
      The agent SDK tier is off. Enable <code>agent_sdk.enabled</code> in settings to
      launch sessions from here, or turn on the bridge terminal
      ({{ opts.terminal_detail }}).
    </div>
    <template v-else-if="held">
      <p class="live-launch-warn" data-testid="live-launch-held">{{ held.warning }}</p>
      <Button
        class="live-launch-go"
        data-testid="live-launch-open"
        @click="emit('launched', held.launched)"
      >
        Open the run anyway
      </Button>
    </template>

    <LiveResumePicker
      v-else-if="picking"
      :current-id="resumeFrom"
      :selected-id="resume?.session_id || ''"
      @select="pickResume"
      @cancel="picking = false"
    />

    <template v-else>
      <LaunchKindField v-model="kind" :opts="opts" />
      <Textarea
        v-model="prompt"
        class="live-launch-prompt"
        data-testid="live-launch-prompt"
        rows="4"
        :placeholder="resume
          ? 'What next? — optional, leave blank to just reopen the session'
          : 'What should the agent do? — optional, leave blank to just start the session'"
        aria-label="Prompt for the new run (optional)"
      />

      <label class="live-launch-lbl" for="live-launch-cwd">working directory</label>
      <Select
        id="live-launch-cwd"
        v-model="cwd"
        block
        :options="cwdOptions"
        data-testid="live-launch-cwd"
        aria-label="Working directory"
      />

      <LaunchWorktreeFields
        v-model:enabled="useWorktree"
        v-model:branch="worktreeBranch"
        v-model:base="worktreeBase"
        :cwd="cwd"
        :disabled="resumeApplies"
      />

      <label class="live-launch-lbl" for="live-launch-mode">permission mode</label>
      <Select
        id="live-launch-mode"
        v-model="mode"
        block
        :options="modeOptions"
        data-testid="live-launch-mode"
        aria-label="Permission mode"
      />
      <p v-if="shadowWarning" class="live-launch-warn" data-testid="live-launch-shadow">
        {{ shadowWarning }}
      </p>

      <label class="live-launch-lbl" for="live-launch-model">model</label>
      <Select
        id="live-launch-model"
        v-model="model"
        block
        :options="modelOptions"
        data-testid="live-launch-model"
        aria-label="Model"
      />
      <Input
        v-if="model === CUSTOM_MODEL"
        v-model="modelCustom"
        class="live-launch-model-custom"
        data-testid="live-launch-model-custom"
        placeholder="e.g. claude-opus-4-8"
        aria-label="Custom model id"
      />
      <p
        v-if="customModelIsBlank"
        class="live-launch-warn"
        data-testid="live-launch-model-hint"
      >
        name the model, or pick one above — a blank custom id would launch on
        the server default.
      </p>

      <label class="live-launch-lbl" for="live-launch-effort">effort</label>
      <Select
        id="live-launch-effort"
        v-model="effort"
        block
        :options="effortOptions"
        data-testid="live-launch-effort"
        aria-label="Reasoning effort"
      />

      <label class="live-launch-check">
        <Checkbox v-model="oneShot" :disabled="isTerminal" data-testid="live-launch-oneshot" />
        <span>end after the first turn</span>
      </label>
      <p v-if="isTerminal" class="live-launch-kind-note" data-testid="live-launch-terminal-note">
        a terminal session stays open until you exit claude — one-shot and
        continuing a session are SDK-run options.
      </p>
      <p
        v-if="oneShotNeedsPrompt"
        class="live-launch-warn"
        data-testid="live-launch-oneshot-hint"
      >
        a one-shot run needs a prompt — it ends with its first turn.
      </p>

      <template v-if="!isTerminal">
        <label class="live-launch-lbl">continue a session</label>
        <div v-if="resume" class="live-launch-resumed" data-testid="live-launch-resumed">
          <span class="live-launch-resumed-txt" data-testid="live-launch-resume-label">
            <span class="live-launch-resumed-title">{{ resumeTitle }}</span>
            <span class="live-launch-resumed-kind">{{ resumeKind }}</span>
          </span>
          <Button
            variant="ghost"
            size="icon"
            aria-label="Start a fresh session instead"
            data-testid="live-launch-resume-clear"
            @click="clearResume"
          >
            <Icon name="x" :size="13" />
          </Button>
        </div>
        <Button
          v-else
          variant="ghost"
          class="live-launch-pick"
          data-testid="live-launch-resume-open"
          @click="picking = true"
        >Pick a session to continue…</Button>
      </template>

      <p v-if="refusal" class="live-launch-warn" data-testid="live-launch-refusal">
        {{ refusal }}
      </p>
      <Button
        class="live-launch-go"
        :disabled="!canLaunch"
        data-testid="live-launch-go"
        @click="launch"
      >
        {{ launching ? 'launching…' : 'Launch' }}
      </Button>
    </template>
  </div>
</template>
