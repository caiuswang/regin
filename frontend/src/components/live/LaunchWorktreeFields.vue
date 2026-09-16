<script setup>
// The launch form's "run this in a new git worktree" section.
//
// Its own component rather than more rows inside LiveLaunchSheet because it is
// the one part of the form that has to ask the server a *second* question: the
// branches and the worktree root belong to whichever working directory is
// currently picked, and the directory menu spans every registered repo. Folding
// that fetch into the sheet would tie a per-repo lookup to a form that
// otherwise loads once.
//
// A directory that is not a git checkout is an ordinary answer, not an error
// state: the section says so and stays off, rather than offering branches it
// invented for a path git knows nothing about.
import { computed, ref, watch } from 'vue'
import api from '../../api'
import Checkbox from '../ui/Checkbox.vue'
import Input from '../ui/Input.vue'
import Select from '../ui/Select.vue'
import { slugifyBranch } from '../../utils/worktreeSlug.js'

const props = defineProps({
  // The launch form's working directory. '' means the server's own, which the
  // options route resolves to the install's project root.
  cwd: { type: String, default: '' },
  // A resumed session cannot move: `claude --resume` resolves its id against
  // the directory it ran in, so the whole section is held shut.
  disabled: { type: Boolean, default: false },
})

const enabled = defineModel('enabled', { type: Boolean, default: false })
const branch = defineModel('branch', { type: String, default: '' })
const base = defineModel('base', { type: String, default: '' })

const opts = ref(null)
const unavailable = ref('')
// Only a 400 is the server *answering* "this directory is not a checkout"; a
// 404, a 403 or a dead server means the question never got asked. Reporting
// both the same way tells an operator their own repo is not a checkout
// whenever the route is missing — which is a lie about their disk.
const refused = ref(false)
const loading = ref(false)
// Only the newest fetch may write: the directory menu can move twice before
// the first repo answers, and a late reply would leave the form offering
// another repo's branches under this one's name.
let latest = 0

async function loadOptions(cwd) {
  const token = ++latest
  loading.value = true
  try {
    const data = await api.get(
      `/agent-runs/worktree-options?cwd=${encodeURIComponent(cwd)}`)
    if (token !== latest) return
    opts.value = data
    unavailable.value = ''
    refused.value = false
    // Adopting the checkout's HEAD is the same default `git worktree add -b`
    // would have taken at a terminal — but only where there is nothing to
    // adopt over. This component remounts on every reopen of the dialog (the
    // portal unmounts closed content), so seeding unconditionally would revert
    // a base the operator deliberately picked every time they reopened the
    // form to fix a typo. A pick the *new* repo cannot honour is the one case
    // that must still be replaced.
    if (!base.value || !(data.branches || []).includes(base.value)) {
      base.value = data.default_base || ''
    }
  } catch (e) {
    if (token !== latest) return
    opts.value = null
    refused.value = e?.status === 400
    // Capped as well as wrapped: `overflow-wrap` alone still lets a long
    // unbroken body (compact JSON, a stack frame) run the note down the sheet.
    unavailable.value = (e?.message || 'the server did not answer').slice(0, 200)
    enabled.value = false
  } finally {
    if (token === latest) loading.value = false
  }
}

watch(() => props.cwd, (cwd) => loadOptions(cwd), { immediate: true })

const baseOptions = computed(() => (opts.value?.branches || []).map(
  b => ({ value: b, label: b })))

// A refusal already names the directory and reads as a sentence, so it is
// shown as the server wrote it; anything else is only a reason, and needs the
// directory said around it.
const unavailableNote = computed(() => {
  if (!unavailable.value) return ''
  if (refused.value) return unavailable.value
  const where = props.cwd || 'the server working directory'
  return `could not read the branches for ${where} — ${unavailable.value}`
})

// The directory name the server will derive, shown so the operator sees where
// the tree lands before they commit to it rather than after.
const slug = computed(() => slugifyBranch(branch.value))

const destination = computed(() => (
  opts.value && slug.value ? `${opts.value.worktrees_root}/${slug.value}` : ''))

// Shown here rather than left to the sheet's own blank check, which cannot see
// that a name of pure punctuation slugifies to nothing.
const branchMissing = computed(() => enabled.value && !slug.value)
</script>

<template>
  <div class="launch-wt">
    <label class="live-launch-check">
      <Checkbox
        v-model="enabled"
        :disabled="disabled || !opts"
        data-testid="live-launch-worktree"
      />
      <span>run in a new git worktree</span>
    </label>

    <p
      v-if="disabled"
      class="launch-wt-note"
      data-testid="live-launch-worktree-resume"
    >
      a continued session stays in the directory it ran in.
    </p>
    <p
      v-else-if="loading && !opts"
      class="launch-wt-note"
    >reading branches…</p>
    <p
      v-else-if="unavailableNote"
      class="launch-wt-note"
      data-testid="live-launch-worktree-unavailable"
    >{{ unavailableNote }}</p>

    <template v-if="enabled && opts && !disabled">
      <label class="live-launch-lbl" for="live-launch-wt-branch">branch</label>
      <Input
        id="live-launch-wt-branch"
        v-model="branch"
        data-testid="live-launch-worktree-branch"
        placeholder="e.g. feat/new-ui"
        aria-label="Branch for the new worktree"
      />
      <p
        v-if="branchMissing"
        class="live-launch-warn"
        data-testid="live-launch-worktree-branch-hint"
      >
        name the branch — the worktree is created for it.
      </p>

      <label class="live-launch-lbl" for="live-launch-wt-base">base branch</label>
      <Select
        id="live-launch-wt-base"
        v-model="base"
        block
        :options="baseOptions"
        data-testid="live-launch-worktree-base"
        aria-label="Base branch for the new worktree"
      />

      <p
        v-if="destination"
        class="launch-wt-note"
        data-testid="live-launch-worktree-path"
      >
        → <code>{{ destination }}</code>
      </p>
    </template>
  </div>
</template>

<style scoped>
.launch-wt { display: contents; }
.launch-wt-note {
  margin-top: 0.25rem;
  font-size: 0.75rem;
  color: var(--color-fg-muted);
  overflow-wrap: anywhere;
}
.launch-wt-note code {
  font-family: var(--font-mono);
  font-size: 0.7188rem;
}
</style>
