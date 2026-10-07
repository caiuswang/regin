import { computed } from 'vue'

// Sentinel for "not one of the offered aliases". The launch routes accept any
// model string, so an operator pinning an exact id must stay able to say so —
// the menu decides what is one tap away, not what is legal.
export const CUSTOM_MODEL = '__custom__'

// D1 of #140: a launch that names no provider runs on claude, whatever the
// install's active provider is.
export const DEFAULT_PROVIDER = 'claude'

// No row means a server that serves no capabilities: every field is offered,
// exactly as before providers were declared.
export function declares(row, field) {
  return !row || row.fields.includes(field)
}

export function launchDeclared(row, mode) {
  return !row || row.launch_modes.includes(mode)
}

// Named for what it does (defer to the install) rather than "default", which
// the CLI also has as a real value — and naming the configured one, so an
// operator can tell whether picking something changed anything.
export function deferOption(configured, noun) {
  return {
    value: '',
    label: configured ? `use server setting (${configured})` : `${noun} default`,
  }
}

/**
 * useLaunchProviders — the provider half of `/agent-runs/launch-options`,
 * shared by every form that starts an agent (the task arm sheets and the
 * /live launch sheet), so a provider is offered, labelled and given models
 * the same way wherever a run is launched.
 *
 * `options` is the ref the form loaded launch-options into.
 */
export function useLaunchProviders(options) {
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

  function modelOptions(id) {
    const row = providerRow(id)
    const configured = row ? row.default_model : options.value?.default_model
    return [
      deferOption(configured, 'CLI'),
      ...providerModels(id).map(m => ({ value: m, label: m })),
      { value: CUSTOM_MODEL, label: 'custom…' },
    ]
  }

  return { providers, providerRow, providerOptions, providerModels, modelOptions }
}
