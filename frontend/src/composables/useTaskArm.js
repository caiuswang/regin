import { computed, ref } from 'vue'
import api from '../api'

// Sentinel for "not one of the offered aliases" — the same escape hatch the
// /live launch sheet keeps, because the arm allowlist accepts any model string
// and an operator pinning an exact id must stay able to say so.
export const CUSTOM_MODEL = '__custom__'
// Same escape hatch for skills: regin only lists the ones it deploys, but the
// launched CLI resolves any skill on the operator's own disk.
export const CUSTOM_SKILL = '__custom__'

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
    skill: skill.name || '',
    skillCustom: '',
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
  const skillName = (draft.skill === CUSTOM_SKILL ? draft.skillCustom : draft.skill).trim()
  if (skillName) {
    body.skill = { name: skillName, args: draft.skillArgs.trim() }
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
  const skillIds = ref([])

  async function loadOptions() {
    try {
      const [launch, skills] = await Promise.all([
        api.get('/agent-runs/launch-options'),
        api.get('/skills').catch(() => null),
      ])
      options.value = launch
      skillIds.value = (skills?.rows || []).map(r => r.id).sort()
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
  // carry, or a skill regin does not deploy; adopting either into its custom
  // slot keeps it visible instead of silently resetting the picker to "none".
  function adoptUnlisted(draft) {
    const models = options.value?.models || []
    if (draft.model && !models.includes(draft.model)) {
      draft.modelCustom = draft.model
      draft.model = CUSTOM_MODEL
    }
    if (draft.skill && !skillIds.value.includes(draft.skill)) {
      draft.skillCustom = draft.skill
      draft.skill = CUSTOM_SKILL
    }
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
  const skillOptions = computed(() => [
    { value: '', label: 'none — send the task body as a plain prompt' },
    ...skillIds.value.map(id => ({ value: id, label: id })),
    { value: CUSTOM_SKILL, label: 'custom…' },
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
    options, loading, error, saving, armError, skillOptions,
    cwdOptions, modeOptions, modelOptions, effortOptions,
    loadOptions, adoptUnlisted, pruneUnlisted, arm,
  }
}
