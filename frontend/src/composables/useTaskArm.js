import { computed, ref } from 'vue'
import api from '../api'

// Sentinel for "not one of the offered aliases" — the same escape hatch the
// /live launch sheet keeps, because the arm allowlist accepts any model string
// and an operator pinning an exact id must stay able to say so.
export const CUSTOM_MODEL = '__custom__'

/** Every skill a task was armed with, in invocation order. */
export function armedSkills(skill) {
  if (!skill?.name) return []
  return [skill.name, ...(skill.also || [])]
}

/** A draft seeded from whatever the task was last armed with.
 *
 * Read off the task's PENDING ATTEMPT, not the task: an attempt is what spends
 * the parameters, and re-arming replaces that one row rather than editing a
 * column two attempts would have had to share.
 */
export function armDraft(task) {
  const armed = task?.armed || {}
  const params = armed.params || {}
  const worktree = params.worktree || {}
  const skill = params.skill || {}
  return {
    // Ordered: the first opens the run as its slash command, the rest are
    // named after it — so selection order is part of the value.
    skills: armedSkills(skill),
    skillArgs: skill.args || '',
    cwd: params.cwd || '',
    model: params.model || '',
    modelCustom: '',
    permission_mode: params.permission_mode || '',
    effort: params.effort || '',
    one_shot: !!params.one_shot,
    useWorktree: !!worktree.branch,
    worktreeBranch: worktree.branch || '',
    worktreeBase: worktree.base || '',
    verify_cmd: armed.verify_cmd || '',
    autorun: !!armed.autorun,
    // '' is the SDK default, so an unset launch stays empty for R4b's
    // first-non-empty-wins fill and only an explicit tmux carries over.
    launch: params.launch === 'tmux' ? 'tmux' : '',
  }
}

function armBody(draft) {
  const body = {
    cwd: draft.cwd,
    model: draft.model === CUSTOM_MODEL ? draft.modelCustom.trim() : draft.model,
    permission_mode: draft.permission_mode,
    effort: draft.effort,
    one_shot: draft.one_shot,
    verify_cmd: draft.verify_cmd.trim(),
    autorun: draft.autorun,
    launch: draft.launch || 'sdk',
  }
  if (draft.useWorktree && draft.worktreeBranch.trim()) {
    body.worktree = {
      branch: draft.worktreeBranch.trim(),
      base: draft.worktreeBase || '',
    }
  }
  const [first, ...also] = draft.skills
  if (first) {
    body.skill = { name: first, args: draft.skillArgs.trim() }
    if (also.length) body.skill.also = also
  }
  return body
}

function deferOption(configured, noun) {
  return {
    value: '',
    label: configured ? `use server setting (${configured})` : `${noun} default`,
  }
}

/**
 * useTaskArm — the arm sheet's server-side halves: what the install offers,
 * and what it says about a payload.
 *
 * The menus come from `/agent-runs/launch-options` for the same reason the
 * launch sheet's do: models, permission modes and efforts describe the install
 * this browser is driving, and a hardcoded list drifts from it silently.
 */
export function useTaskArm() {
  const options = ref(null)
  const loading = ref(true)
  const error = ref('')
  const saving = ref(false)
  const armError = ref(null)
  // Read off the repo the run will start in, not off regin's own deployments:
  // a skill only some other repo carries is a slash command nobody answers.
  const skillCatalog = ref([])
  const skillsLoaded = ref(false)
  let skillsAsked = 0

  async function loadOptions() {
    try {
      options.value = await api.get('/agent-runs/launch-options')
    } catch (e) {
      error.value = e?.message || 'Failed to load launch options.'
    } finally {
      loading.value = false
    }
  }

  // A draft recalled from the LAST arm may name a repo, permission mode or
  // effort this install no longer offers — the menus describe the server, and
  // it can change under a remembered pick. Dropping those three back to the
  // server default is the honest read of a stale preset: `permission_mode` and
  // `effort` would be refused outright by `lib/tasks/arm.py`, and a stale `cwd`
  // would aim the run at a directory nobody chose. Applied only to a recalled
  // draft — a value the TASK itself carries is a deliberate pick that must keep
  // showing, stale or not.
  function pruneUnlisted(draft) {
    const offered = {
      cwd: options.value?.cwds || [],
      permission_mode: options.value?.permission_modes || [],
      effort: options.value?.efforts || [],
    }
    for (const [field, values] of Object.entries(offered)) {
      if (draft[field] && !values.includes(draft[field])) draft[field] = ''
    }
  }

  // What the task was armed with may be a model id the alias menu does not
  // carry; adopting it into the custom slot keeps it visible instead of
  // silently resetting the picker to the default.
  function adoptUnlisted(draft) {
    const models = options.value?.models || []
    if (draft.model && !models.includes(draft.model)) {
      draft.modelCustom = draft.model
      draft.model = CUSTOM_MODEL
    }
  }

  // A cwd change can land after a newer one was asked for; only the latest
  // answer may describe the menu.
  async function loadSkills(cwd) {
    const asked = ++skillsAsked
    const query = cwd ? `?cwd=${encodeURIComponent(cwd)}` : ''
    const res = await api.get(`/agent-runs/skill-options${query}`).catch(() => null)
    if (asked !== skillsAsked) return
    skillCatalog.value = res?.skills || []
    skillsLoaded.value = !!res
  }

  const skillNames = computed(() => new Set(skillCatalog.value.map(s => s.name)))

  // Only a REMEMBERED pick is dropped: it was another task's, and in a repo
  // that lacks it would open the run with a dead slash command. A skill the
  // task itself carries stays, flagged, since someone chose it for this task.
  // A catalog that failed to load proves nothing missing, so nothing is dropped.
  function pruneUnavailableSkills(draft) {
    if (!skillsLoaded.value) return
    draft.skills = draft.skills.filter(name => skillNames.value.has(name))
  }

  function skillMissing(name) {
    return skillsLoaded.value && !skillNames.value.has(name)
  }

  const cwdOptions = computed(() => [
    { value: '', label: 'server working directory' },
    ...(options.value?.cwds || []).map(p => ({ value: p, label: p })),
  ])
  const modeOptions = computed(() => {
    const cfg = options.value?.default_permission_mode || 'default'
    return [
      { value: '', label: cfg === 'default' ? 'use server setting' : `use server setting (${cfg})` },
      ...(options.value?.permission_modes || []).map(m => ({ value: m, label: m })),
    ]
  })
  const modelOptions = computed(() => [
    deferOption(options.value?.default_model, 'CLI'),
    ...(options.value?.models || []).map(m => ({ value: m, label: m })),
    { value: CUSTOM_MODEL, label: 'custom…' },
  ])
  const effortOptions = computed(() => [
    deferOption(options.value?.default_effort, 'CLI'),
    ...(options.value?.efforts || []).map(e => ({ value: e, label: e })),
  ])

  async function arm(task, draft) {
    saving.value = true
    const result = await api.post(`/tasks/${task.id}/arm`, armBody(draft))
    saving.value = false
    if (result.ok === false) {
      armError.value = {
        message: result.msg || 'The server refused these parameters.',
        field: result.body?.field || '',
      }
      return false
    }
    armError.value = null
    return true
  }

  return {
    options, loading, error, saving, armError,
    skillCatalog, skillsLoaded, loadSkills, pruneUnavailableSkills, skillMissing,
    cwdOptions, modeOptions, modelOptions, effortOptions,
    loadOptions, adoptUnlisted, pruneUnlisted, arm,
  }
}
