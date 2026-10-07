import { computed, ref } from 'vue'
import api from '../api'

// Sentinel for "not one of the offered aliases" — the same escape hatch the
// /live launch sheet keeps, because the arm allowlist accepts any model string
// and an operator pinning an exact id must stay able to say so.
export const CUSTOM_MODEL = '__custom__'

// D1 of #140: an arm that names no provider runs on claude, whatever the
// install's active provider is.
export const DEFAULT_PROVIDER = 'claude'

const LAUNCH_MODES = ['sdk', 'tmux']
const LAUNCH_HINTS = { sdk: 'ends if the server restarts', tmux: 'survives a server restart' }

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
    // '' until a source names one, so the prefill can tell "unset" from a
    // pick; `prefilledArmDraft` settles it to DEFAULT_PROVIDER.
    provider: params.provider || '',
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

// No row means a server that serves no capabilities: every field is offered,
// exactly as before providers were declared.
export function declares(row, field) {
  return !row || row.fields.includes(field)
}

function launchDeclared(row, mode) {
  return !row || row.launch_modes.includes(mode)
}

// Invariant 4: a field the provider does not declare is not sent at all —
// the server refuses an undeclared model, and an absent key is the only
// honest way to say "this provider has no such setting".
function armBody(draft, row) {
  const body = {
    provider: draft.provider || DEFAULT_PROVIDER,
    cwd: draft.cwd,
    one_shot: draft.one_shot,
    verify_cmd: draft.verify_cmd.trim(),
    autorun: draft.autorun,
  }
  if (declares(row, 'model')) {
    body.model = draft.model === CUSTOM_MODEL ? draft.modelCustom.trim() : draft.model
  }
  if (declares(row, 'permission_mode')) body.permission_mode = draft.permission_mode
  if (declares(row, 'effort')) body.effort = draft.effort
  const launch = draft.launch || 'sdk'
  if (launchDeclared(row, launch)) body.launch = launch
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
    if (providers.value.length && !providerRow(draft.provider)) {
      draft.provider = DEFAULT_PROVIDER
      draft.model = ''
    }
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
    const models = providerModels(draft.provider)
    if (draft.model && !models.includes(draft.model)) {
      draft.modelCustom = draft.model
      draft.model = CUSTOM_MODEL
    }
  }

  // A cwd change can land after a newer one was asked for; only the latest
  // answer may describe the menu.
  // Keyed on the provider too: each CLI reads its own skill dirs, so a skill
  // claude resolves can be a dead slash command under kimi.
  async function loadSkills(cwd, provider = '') {
    const asked = ++skillsAsked
    const params = new URLSearchParams()
    if (cwd) params.set('cwd', cwd)
    if (provider) params.set('provider', provider)
    const query = params.toString() ? `?${params}` : ''
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

  const providers = computed(() => options.value?.providers || [])

  function providerRow(id) {
    return providers.value.find(p => p.id === (id || DEFAULT_PROVIDER)) || null
  }

  // A provider whose CLI is not on PATH stays listed, disabled, naming the
  // binary — the operator learns what to install instead of wondering why
  // kimi is not offered.
  const providerOptions = computed(() => providers.value.map(p => ({
    value: p.id,
    label: p.available ? p.display_name : `${p.display_name} — ${p.binary || p.id} not on PATH`,
    disabled: !p.available,
  })))

  function providerModels(id) {
    const row = providerRow(id)
    return row ? row.models || [] : options.value?.models || []
  }

  function launchOptions(id) {
    const row = providerRow(id)
    return LAUNCH_MODES.filter(mode => launchDeclared(row, mode)).map(mode => {
      const name = row?.labels?.[mode] || (mode === 'sdk' ? 'SDK' : mode)
      return {
        value: mode === 'sdk' ? '' : mode,
        label: mode === 'sdk' ? `${name} (default)` : name,
        hint: LAUNCH_HINTS[mode],
      }
    })
  }

  function modelOptions(id) {
    const row = providerRow(id)
    const configured = row ? row.default_model : options.value?.default_model
    return [
      deferOption(configured, 'CLI'),
      ...providerModels(id).map(m => ({ value: m, label: m })),
      { value: CUSTOM_MODEL, label: 'custom…' },
    ]
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
  const effortOptions = computed(() => [
    deferOption(options.value?.default_effort, 'CLI'),
    ...(options.value?.efforts || []).map(e => ({ value: e, label: e })),
  ])

  async function arm(task, draft) {
    saving.value = true
    const result = await api.post(`/tasks/${task.id}/arm`,
                                  armBody(draft, providerRow(draft.provider)))
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
    providers, providerRow, providerOptions, launchOptions,
    loadOptions, adoptUnlisted, pruneUnlisted, arm,
  }
}
