<script setup>
// Launch form for the /live card's "new run" bottom sheet — the client half of
// the SDK tier (`POST /api/agent-runs`), which until now had no consumer at
// all. The offered working directories, models and permission modes come from
// /agent-runs/launch-options rather than being hardcoded here: they describe
// the install this browser is driving, and a client that guessed them would
// drift from it.
//
// `resumeFrom` turns the same form into "continue this session": the run still
// gets its own trace id (whether `--resume` keeps the CLI's own session id is
// that build's business), so the sheet says so instead of implying the tail
// below will keep growing.
import { ref, computed, onMounted } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import Checkbox from '../ui/Checkbox.vue'
import Input from '../ui/Input.vue'
import Select from '../ui/Select.vue'
import Textarea from '../ui/Textarea.vue'
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
const resume = ref(false)

const cwdOptions = computed(() => [
  { value: '', label: 'server working directory' },
  ...(opts.value?.cwds || []).map(p => ({ value: p, label: p })),
])

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

// A run regin launched carries a synthetic `sdk-…` trace id, which is regin's
// own name for it and not a CLI session — `--resume` would fail on it. Only a
// session the user drove in a terminal has an id the CLI can reopen.
const canResume = computed(() => (
  !!props.resumeFrom && !props.resumeFrom.startsWith('sdk-')
))

// Only worth saying when something is actually gated — otherwise "this mode
// skips the permission callback" warns about the loss of a control the install
// never turned on.
const shadowWarning = computed(() => (
  opts.value?.gating_active && ['acceptEdits', 'bypassPermissions'].includes(mode.value)
    ? `${mode.value} skips the permission callback — nothing will be held for approval.`
    : null
))

const canLaunch = computed(() => !!prompt.value.trim() && !launching.value)

onMounted(async () => {
  try {
    opts.value = await api.get('/agent-runs/launch-options')
  } catch (e) {
    error.value = e?.message || 'Failed to load launch options.'
  } finally {
    loading.value = false
  }
})

async function launch() {
  if (!canLaunch.value) return
  launching.value = true
  refusal.value = null
  try {
    const body = {
      prompt: prompt.value.trim(),
      cwd: cwd.value || undefined,
      model: model.value.trim() || undefined,
      permission_mode: mode.value || undefined,
      one_shot: oneShot.value || undefined,
      resume: (resume.value && canResume.value && props.resumeFrom) || undefined,
    }
    const data = await api.post('/agent-runs', body)
    if (!data.launched) {
      refusal.value = data.detail || 'Launch refused.'
      return
    }
    if (data.warning) {
      held.value = { traceId: data.trace_id, warning: data.warning }
      return
    }
    emit('launched', { traceId: data.trace_id })
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
        @click="emit('launched', { traceId: held.traceId })"
      >
        Open the run anyway
      </Button>
    </template>

    <template v-else>
      <Textarea
        v-model="prompt"
        class="live-launch-prompt"
        data-testid="live-launch-prompt"
        rows="4"
        placeholder="What should the agent do?"
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
      <label v-if="canResume" class="live-launch-check">
        <Checkbox v-model="resume" data-testid="live-launch-resume" />
        <span>continue {{ shortTraceId(resumeFrom) }} (opens a new trace)</span>
      </label>

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
