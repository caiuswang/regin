import { computed, ref, watch } from 'vue'
import { parseLocalIso } from '../utils/sessionActivity.js'
import { primaryRepo } from '../utils/sessionRowFormat.js'

// Grouping is a PRESENTATION axis over the rows already loaded — the server
// order stays `last_seen DESC` and keyset pagination is untouched. That means
// a group's count is always "of what you can see", which is why the list
// footer reports the server-side total separately.
const GROUP_KEY = 'regin_sessions_group'

export const GROUP_OPTIONS = [
  { value: 'active', label: 'Active first' },
  { value: 'day', label: 'By day' },
  { value: 'repo', label: 'By repo' },
  { value: 'flat', label: 'Flat' },
]

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

function dayKey(d) {
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`
}

function dayLabel(d, today, yesterday) {
  const stamp = `${MONTHS[d.getMonth()]} ${d.getDate()}`
  if (dayKey(d) === today) return `Today · ${stamp}`
  if (dayKey(d) === yesterday) return `Yesterday · ${stamp}`
  return d.getFullYear() === new Date().getFullYear() ? stamp : `${stamp}, ${d.getFullYear()}`
}

// Bucket rows in encounter order, so within every group the server's
// newest-first ordering survives.
function bucket(rows, keyOf) {
  const out = []
  const index = new Map()
  for (const row of rows) {
    const { key, label, tone } = keyOf(row)
    let group = index.get(key)
    if (!group) {
      group = { key, label, tone, rows: [] }
      index.set(key, group)
      out.push(group)
    }
    group.rows.push(row)
  }
  return out
}

function byDay(rows) {
  const now = new Date()
  const today = dayKey(now)
  const yesterday = dayKey(new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1))
  return bucket(rows, (row) => {
    const d = parseLocalIso(row.last_seen)
    if (!d) return { key: 'unknown', label: 'Undated', tone: 'muted' }
    return { key: dayKey(d), label: dayLabel(d, today, yesterday), tone: 'muted' }
  })
}

function byRepo(rows) {
  const groups = bucket(rows, (row) => {
    const name = primaryRepo(row)
    return { key: name || '￿', label: name || 'No repo', tone: 'muted' }
  })
  // Alphabetical, with the unmatched bucket pinned last (￿ sorts after
  // every real repo name).
  return groups.sort((a, b) => a.key.localeCompare(b.key))
}

function byActive(rows, isActive) {
  const active = rows.filter(isActive)
  const rest = rows.filter(r => !isActive(r))
  const out = []
  if (active.length) out.push({ key: 'active', label: 'Active now', tone: 'live', rows: active })
  if (rest.length) out.push({ key: 'earlier', label: 'Earlier', tone: 'muted', rows: rest })
  return out
}

export function useSessionGrouping(rows, isActive) {
  const mode = ref(
    GROUP_OPTIONS.some(o => o.value === localStorage.getItem(GROUP_KEY))
      ? localStorage.getItem(GROUP_KEY)
      : 'active'
  )
  watch(mode, (v) => localStorage.setItem(GROUP_KEY, v))

  const groups = computed(() => {
    const list = rows.value || []
    if (!list.length) return []
    if (mode.value === 'day') return byDay(list)
    if (mode.value === 'repo') return byRepo(list)
    if (mode.value === 'active') return byActive(list, isActive)
    return [{ key: 'flat', label: null, tone: 'muted', rows: list }]
  })

  return { mode, groups }
}
