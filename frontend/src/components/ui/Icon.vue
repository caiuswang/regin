<script setup>
// Shared inline-SVG icon primitive (Lucide/Feather geometry: 24×24 viewBox,
// currentColor stroke, round caps). Replaces emoji-as-icon, which reads as
// unprofessional and ignores text color/size. Each name maps to its path
// `d` strings; add new icons here rather than inlining one-off SVGs.
const ICONS = {
  'thumbs-up': [
    'M7 10v12',
    'M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88Z',
  ],
  'thumbs-down': [
    'M17 14V2',
    'M9 18.12 10 14H4.17a2 2 0 0 1-1.92-2.56l2.33-8A2 2 0 0 1 6.5 2H20a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2.76a2 2 0 0 0-1.79 1.11L12 22a3.13 3.13 0 0 1-3-3.88Z',
  ],
  x: ['M18 6 6 18', 'm6 6 12 12'],
  'chevron-right': ['m9 18 6-6-6-6'],
  'chevron-down': ['m6 9 6 6 6-6'],
  'chevron-left': ['m15 18-6-6 6-6'],
  'arrow-up-right': ['M7 7h10v10', 'M7 17 17 7'],
  'arrow-up': ['M12 19V5', 'm5 12 7-7 7 7'],
  search: ['M19 11a8 8 0 1 1-16 0 8 8 0 0 1 16 0Z', 'm21 21-4.35-4.35'],
  check: ['M20 6 9 17l-5-5'],
  plus: ['M5 12h14', 'M12 5v14'],
  archive: [
    'M4 8v11a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1V8',
    'M2 4h20v4H2z',
    'M10 12h4',
  ],
  'folder-plus': [
    'M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z',
    'M12 11v6',
    'M9 14h6',
  ],
  filter: ['M22 3 2 3 10 12.46 10 19 14 21 14 12.46Z'],
  tag: [
    'M20.6 12.6 12 21 3.4 12.6a5.5 5.5 0 0 1 7.8-7.8L12 5.8l.8-.8a5.5 5.5 0 0 1 7.8 7.8Z',
  ],
  upload: ['M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4', 'M17 8l-5-5-5 5', 'M12 3v12'],
  copy: [
    'M10 8h10a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H10a2 2 0 0 1-2-2V10a2 2 0 0 1 2-2z',
    'M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2',
  ],
  list: ['M3 12h.01', 'M3 18h.01', 'M3 6h.01', 'M8 12h13', 'M8 18h13', 'M8 6h13'],
  'list-checks': ['m3 17 2 2 4-4', 'm3 7 2 2 4-4', 'M13 6h8', 'M13 12h8', 'M13 18h8'],
  workflow: [
    'M12 3 15 6 12 9 9 6Z', 'M12 9v3', 'M7 12v3', 'M17 12v3', 'M7 12h10',
    'M5 17 7 15 9 17 7 19Z', 'M15 17 17 15 19 17 17 19Z',
  ],
}

defineProps({
  name: { type: String, required: true },
  size: { type: [Number, String], default: 16 },
})
</script>

<template>
  <svg
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="2"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
    class="shrink-0"
  >
    <path v-for="(d, i) in ICONS[name] || []" :key="i" :d="d" />
  </svg>
</template>
