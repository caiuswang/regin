import { ref } from 'vue'

// App theme: 'light' | 'dark'. Persisted in localStorage and applied as
// `data-theme` on <html>, which drives three things at once:
//   1. the inverted Tailwind color ramp (src/assets/style.css),
//   2. every `var(--color-*)` reference in hand-written CSS, and
//   3. PrimeVue's darkModeSelector (configured in main.js).
// An inline script in index.html applies the stored value BEFORE Vue mounts
// to avoid a flash of the wrong theme; this composable keeps it in sync after.
const THEME_KEY = 'regin_theme'
const VALID_THEMES = ['light', 'dark']

function initialTheme() {
  const stored = localStorage.getItem(THEME_KEY)
  return VALID_THEMES.includes(stored) ? stored : 'light'
}

function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme)
}

// Module-level singleton so every component shares one reactive theme.
const theme = ref(initialTheme())
applyTheme(theme.value)

export function useTheme() {
  function setTheme(next) {
    if (!VALID_THEMES.includes(next)) return
    theme.value = next
    localStorage.setItem(THEME_KEY, next)
    applyTheme(next)
  }

  function toggleTheme() {
    setTheme(theme.value === 'dark' ? 'light' : 'dark')
  }

  return { theme, setTheme, toggleTheme }
}
