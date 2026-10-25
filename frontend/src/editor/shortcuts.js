// The keys the editor answers to, as the head's Shortcuts list shows them.
// The bindings themselves are Tiptap's and `extensions.js`'s; this only names
// them. A chord is a list of keys, `Mod` being ⌘ on a Mac and Ctrl elsewhere;
// a row with several chords lists them in the order the action names them,
// apart by ` / ` — or by `joiner` where a key is itself `/`.
//
// No ProseMirror here: the list is on the page before the rich face loads.

export const SHORTCUTS = [
  { action: 'Bold / Italic / Strike / Inline code',
    chords: [['Mod', 'B'], ['Mod', 'I'], ['Mod', 'Shift', 'S'], ['Mod', 'E']] },
  { action: 'Link', chords: [['Mod', 'K']] },
  { action: 'Heading 1–4', chords: [['Mod', 'Alt', '1'], ['Mod', 'Alt', '4']], range: true },
  { action: 'Bulleted / Numbered / Checklist',
    chords: [['Mod', 'Shift', '8'], ['Mod', 'Shift', '7'], ['Mod', 'Shift', '9']] },
  { action: 'Quote / Code block', chords: [['Mod', 'Shift', 'B'], ['Mod', 'Alt', 'C']] },
  { action: 'Line break', chords: [['Shift', 'Enter']] },
  { action: 'Save', chords: [['Mod', 'Enter']] },
  { action: 'Focus mode on/off', chords: [['Mod', 'Shift', 'F'], ['Esc']] },
  { action: 'Slash menu / Reference', chords: [['/'], ['@']], joiner: ' · ' },
]

const MAC = { Mod: '⌘', Alt: '⌥', Shift: '⇧' }
const OTHER = { Mod: 'Ctrl', Alt: 'Alt', Shift: 'Shift' }

export function isMac(platform = globalThis.navigator?.platform ?? '') {
  return /mac|iphone|ipad|ipod/i.test(platform)
}

export function chordLabel(chord, mac) {
  const names = mac ? MAC : OTHER
  const keys = chord.map((key) => names[key] ?? key)
  return mac ? keys.join('') : keys.join('+')
}

export function shortcutLabel(row, mac) {
  const labels = row.chords.map((chord) => chordLabel(chord, mac))
  return labels.join(row.range ? ' … ' : row.joiner ?? ' / ')
}
