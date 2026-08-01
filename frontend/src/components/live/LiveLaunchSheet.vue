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
import LiveResumePicker from './LiveResumePicker.vue'
import Select from '../ui/Select.vue'
import Textarea from '../ui/Textarea.vue'
import { resumeKindLabel } from '../../utils/resumeKind.js'
import { shortTraceId } from '../../utils/traceFormatters.js'

const props = defineProps({
  resumeFrom: { type: String, default: '' },
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

const prompt = ref('')
const cwd = ref('')
const model = ref('')
const mode = ref('')
const oneShot = ref(false)
// The picked session row, or null for a fresh run. Holding the whole row (not
// just its id) is what lets the form say which trace a pick lands on and adopt
// its cwd without a second fetch.
const resume = ref(null)
const picking = ref(false)

// A resumed session's cwd is not a preference — `claude --resume` resolves the
// id relative to the working directory, so launching from anywhere else finds
// no session. The pick therefore sets it, and an entry is added for a path the
// install has not registered as a repo rather than silently falling back to the
// server default.
const cwdOptions = computed(() => {
  const known = opts.value?.cwds || []
  const picked = resume.value?.cwd
  const extra = picked && !known.includes(picked) ? [picked] : []
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

function pickResume(row) {
  resume.value = row
  // Adopting the session's cwd is not a convenience: resume resolves the id
  // relative to it, so keeping the previous selection would launch a run that
  // finds nothing to continue.
  if (row.cwd) cwd.value = row.cwd
  // The model is inherited for a weaker but real reason: continuing a
  // conversation on a different model than it was held on is a change the
  // operator did not ask for and would not see. Only a run regin launched
  // recorded one, so a terminal session still falls back to the install's.
  if (row.model) model.value = row.model
  picking.value = false
}

function clearResume() {
  resume.value = null
}

// Only worth saying when something is actually gated — otherwise "this mode
// skips the permission callback" warns about the loss of a control the install
// never turned on.
const shadowWarning = computed(() => (
  opts.value?.gating_active && ['acceptEdits', 'bypassPermissions'].includes(mode.value)
    ? `${mode.value} skips the permission callback — nothing will be held for approval.`
    : null
))

// A resume needs no prompt: reopening the session *is* the act, and the card
// it lands on has a composer. Requiring one would make "just pick this back
// up" impossible to express — the operator would have to invent a first turn.
const canLaunch = computed(() => (
  (!!prompt.value.trim() || !!resume.value) && !launching.value))

onMounted(async () => {
  try {
    opts.value = await api.get('/agent-runs/launch-options')
  } catch (e) {
    error.value = e?.message || 'Failed to load launch options.'
  } finally {
    loading.value = false
  }
})

function launchBody() {
  return {
    prompt: prompt.value.trim(),
    cwd: cwd.value || undefined,
    model: model.value.trim() || undefined,
    permission_mode: mode.value || undefined,
    one_shot: oneShot.value || undefined,
    resume: resume.value?.session_id || undefined,
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
    const picked = resume.value
    const launched = {
      traceId: data.trace_id,
      sameSession: !!picked && !!props.resumeFrom
        && (picked.session_id === props.resumeFrom
          || picked.run_trace_id === props.resumeFrom),
    }
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
    <div v-else-if="!opts.enabled" class="live-empty" data-testid="live-launch-disabled">
      The agent SDK tier is off. Enable <code>agent_sdk.enabled</code> in settings to
      launch sessions from here.
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
      <Textarea
        v-model="prompt"
        class="live-launch-prompt"
        data-testid="live-launch-prompt"
        rows="4"
        :placeholder="resume
          ? 'What next? — optional, leave blank to just reopen the session'
          : 'What should the agent do?'"
        aria-label="Prompt for the new run"
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
      <Input
        id="live-launch-model"
        v-model="model"
        data-testid="live-launch-model"
        :placeholder="opts.default_model || 'settings default'"
        aria-label="Model override"
      />

      <label class="live-launch-check">
        <Checkbox v-model="oneShot" data-testid="live-launch-oneshot" />
        <span>end after the first turn</span>
      </label>

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
