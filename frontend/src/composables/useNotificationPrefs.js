import { reactive, watch } from 'vue'

// Per-browser, not per-account: OS notification permission is granted to an
// origin in one browser, and "how loud should this machine be" is a property
// of where you are sitting, not of who you are. Keeping it in localStorage
// also means no migration and no round-trip before the first toast can render.
const STORAGE_KEY = 'regin.notifications'

const DEFAULTS = {
  maxToasts: 3,
  toastDurationSec: 8,
  osEnabled: false,
  // Tier 2 is the only tier with anything to mute: tier 1 must interrupt and
  // tier 3 never pops in the first place.
  toastsEnabled: true,
  // Off by default: the pop-out takes focus, and an operator typing when an
  // agent parks should get the banner, not a modal over their cursor.
  autoPopout: false,
}

const LIMITS = {
  maxToasts: [1, 5],
  toastDurationSec: [3, 20],
}

function clamp(key, value) {
  const range = LIMITS[key]
  const n = Number(value)
  if (!range || !Number.isFinite(n)) return DEFAULTS[key]
  return Math.min(range[1], Math.max(range[0], Math.round(n)))
}

function load() {
  try {
    const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}')
    return {
      maxToasts: clamp('maxToasts', raw.maxToasts ?? DEFAULTS.maxToasts),
      toastDurationSec: clamp('toastDurationSec',
        raw.toastDurationSec ?? DEFAULTS.toastDurationSec),
      osEnabled: !!raw.osEnabled,
      toastsEnabled: raw.toastsEnabled !== false,
      autoPopout: !!raw.autoPopout,
    }
  } catch {
    return { ...DEFAULTS }
  }
}

const prefs = reactive(load())

watch(prefs, (value) => {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(value))
  } catch { /* private mode / quota — the session still honours the prefs */ }
}, { deep: true })

export function useNotificationPrefs() {
  function set(key, value) {
    if (key in LIMITS) prefs[key] = clamp(key, value)
    else if (key in DEFAULTS) prefs[key] = !!value
  }
  function reset() {
    Object.assign(prefs, DEFAULTS)
  }
  return { prefs, set, reset, LIMITS, DEFAULTS }
}
