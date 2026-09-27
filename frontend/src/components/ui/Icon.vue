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
  flag: ['M4 22V4', 'M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1'],
  image: ['M3 5.5h18v13H3z', 'm3 15 5-5 4 4 3-3 6 6'],
  'chevron-right': ['m9 18 6-6-6-6'],
  'chevron-down': ['m6 9 6 6 6-6'],
  'chevron-left': ['m15 18-6-6 6-6'],
  'arrow-up-right': ['M7 7h10v10', 'M7 17 17 7'],
  'arrow-up': ['M12 19V5', 'm5 12 7-7 7 7'],
  'arrow-down': ['M12 5v14', 'm19 12-7 7-7-7'],
  'arrow-right': ['M5 12h13', 'm12 5 7 7-7 7'],
  'layout-grid': ['M3 3h7v7H3z', 'M14 3h7v7h-7z', 'M14 14h7v7h-7z', 'M3 14h7v7H3z'],
  'rotate-ccw': ['M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8', 'M3 3v5h5'],
  'message-square': [
    'M21 11.5a8.4 8.4 0 0 1-8.5 8.4 8.7 8.7 0 0 1-3.9-.9L3 21l1.9-4.4A8.4 8.4 0 0 1 12.5 3 8.4 8.4 0 0 1 21 11.5Z',
    'M8.5 11.5h7M8.5 8.5h4',
  ],
  paperclip: [
    'm21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 18 8.84l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48',
  ],
  search: ['M19 11a8 8 0 1 1-16 0 8 8 0 0 1 16 0Z', 'm21 21-4.35-4.35'],
  check: ['M20 6 9 17l-5-5'],
  'circle-check': ['M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z', 'm8.5 12.5 2.5 2.5 4.5-5'],
  plus: ['M5 12h14', 'M12 5v14'],
  minus: ['M5 12h14'],
  pencil: [
    'M21.17 6.81a1 1 0 0 0-3.98-3.99L3.84 16.17a2 2 0 0 0-.5.83l-1.32 4.35a.5.5 0 0 0 .62.63l4.35-1.32a2 2 0 0 0 .83-.5Z',
    'm15 5 4 4',
  ],
  archive: [
    'M4 8v11a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1V8',
    'M2 4h20v4H2z',
    'M10 12h4',
  ],
  file: [
    'M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z',
    'M14 2v5h5',
  ],
  folder: [
    'M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z',
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
  'board-columns': ['M3 4h5v16H3z', 'M10 4h5v11h-5z', 'M17 4h4v7h-4z'],
  'dependency-tree': ['M12 3 15 6 12 9 9 6Z', 'M12 9v3', 'M7 12h10', 'M7 12v3', 'M17 12v3'],
  list: ['M3 12h.01', 'M3 18h.01', 'M3 6h.01', 'M8 12h13', 'M8 18h13', 'M8 6h13'],
  'list-checks': ['m3 17 2 2 4-4', 'm3 7 2 2 4-4', 'M13 6h8', 'M13 12h8', 'M13 18h8'],
  workflow: [
    'M12 3 15 6 12 9 9 6Z', 'M12 9v3', 'M7 12v3', 'M17 12v3', 'M7 12h10',
    'M5 17 7 15 9 17 7 19Z', 'M15 17 17 15 19 17 17 19Z',
  ],
  terminal: ['M4 17 10 11 4 5', 'M12 19h8'],
  'maximize-2': ['M15 3h6v6', 'M9 21H3v-6', 'm21 3-7 7', 'm3 21 7-7'],
  'minimize-2': ['M4 14h6v6', 'M20 10h-6V4', 'm14 10 7-7', 'm3 21 7-7'],
  'alert-triangle': [
    'm21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3',
    'M12 9v4',
    'M12 17h.01',
  ],
  square: ['M5 3h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z'],
  power: ['M12 2v10', 'M18.36 6.64a9 9 0 1 1-12.73 0'],
  play: ['M7 4.5 19 12 7 19.5Z'],
  pause: ['M9 4v16', 'M15 4v16'],
  'external-link': [
    'M15 3h6v6',
    'M10 14 21 3',
    'M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6',
  ],
  'plus': [
    'M5 12h14',
    'M12 5v14',
  ],
  'trash': [
    'M3 6h18',
    'M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6',
    'M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2',
    'M10 11v6',
    'M14 11v6',
  ],
  'refresh-cw': [
    'M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8',
    'M3 3v5h5',
    'M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16',
    'M16 16h5v5',
  ],
  // The three reading densities, drawn from one rule motif so the set reads as
  // a scale: all rules → every span; a broken middle rule → spans folded away;
  // a dot beside the last rule → the newest one only.
  'detail-full': ['M4 7h16M4 12h16', 'M4 17h16'],
  'detail-condensed': ['M4 7h16M4 17h16', 'M4 12h5M11.5 12h3M18 12h2'],
  'detail-latest': ['M9 7h11M9 12h11M9 17h11', 'M4.8 17h0.01'],
  // Two stacked chevrons pointing up — "fold this back up". Doubled so it
  // cannot be read as the single caret that opens one row; not a download
  // arrow, which promised an export.
  'chevrons-up': ['m17 11-5-5-5 5', 'm17 18-5-5-5 5'],
  'panel-right-close': ['M3 5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z', 'M15 3v18', 'm8 9 3 3-3 3'],
  'panel-right-open': ['M3 5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z', 'M15 3v18', 'm10 15-3-3 3-3'],
  eye: ['M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7Z', 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z'],
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
