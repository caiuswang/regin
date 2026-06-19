// Span → Tailwind background-color mapping, shared by the timeline tree row
// dots, the turns drill-down dots, and the per-turn tool chips so all three
// agree on one palette. Pure functions, no state.

// Dot/bar color for a span by its `name`. Known semantic names get fixed
// colors; any `tool.*` is cyan, `pre_tool.*` indigo, everything else gray.
export function barColor(name) {
  const map = {
    'skill.read': 'bg-blue-500',
    'skill.invoke': 'bg-green-500',
    'file.edit': 'bg-orange-500',
    'plan.edit': 'bg-green-600',
    'rule.check': 'bg-red-500',
    'memory.recall': 'bg-fuchsia-500',
    'plan session': 'bg-green-500',
    'plan.session': 'bg-green-600',
    'plan.draft': 'bg-green-500',
    'plan.review': 'bg-emerald-400',
    'plan.decision': 'bg-yellow-500',
    'plan.enter': 'bg-green-500',
    'plan.exit': 'bg-green-400',
    'prompt': 'bg-purple-500',
    'conversation': 'bg-slate-600',
    'compact.pre': 'bg-amber-500',
    'compact.post': 'bg-amber-600',
    'rewind': 'bg-rose-500',
  }
  if (map[name]) return map[name]
  if (name.startsWith('tool.')) return 'bg-cyan-500'
  if (name.startsWith('pre_tool.')) return 'bg-indigo-400'
  return 'bg-gray-400'
}

// Chip color for a tool, keyed by bare tool name. Normalizes to the `tool.*`
// form so the turns "Read×2·Bash" chips match the tree rows' palette.
export function toolBadgeColor(name) {
  return barColor(name.startsWith('tool.') ? name : 'tool.' + name)
}
