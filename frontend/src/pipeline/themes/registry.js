// The pipeline shell's themes. A theme changes how the shell's tabs look and
// nothing about what they do, so a third one is a new folder plus a line here.
import classic from './classic/index.js'

export const DEFAULT_THEME = 'pixel'
// Bundled eagerly, so it is what renders when another theme's chunk fails.
export const FALLBACK_THEME = 'classic'

export const THEMES = {
  classic: { id: 'classic', label: 'Classic', load: () => Promise.resolve(classic) },
  pixel: { id: 'pixel', label: 'Pixel', load: () => import('./pixel/index.js').then(m => m.default) },
}

export const THEME_LIST = Object.values(THEMES)

export function isThemeId(value) {
  return typeof value === 'string' && Object.hasOwn(THEMES, value)
}

export { classic as classicTheme }
