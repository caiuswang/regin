// Calendar days are plain `{ y, m, d }` triples in the user's LOCAL clock —
// never Date objects carried around — so a picked span can't shift by a
// timezone offset on its way to the naive-local-ISO bounds `/api/sessions`
// compares lexicographically against `last_seen`.

const MONTHS_SHORT = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                      'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const MONTHS_LONG = ['January', 'February', 'March', 'April', 'May', 'June', 'July',
                     'August', 'September', 'October', 'November', 'December']

export const WEEKDAY_INITIALS = ['S', 'M', 'T', 'W', 'T', 'F', 'S']

function pad(n) { return String(n).padStart(2, '0') }

export function daySerial(day) {
  return day ? day.y * 10000 + day.m * 100 + day.d : null
}

export function dayToIso(day) {
  return day ? `${day.y}-${pad(day.m + 1)}-${pad(day.d)}` : ''
}

export function dayFromIso(text) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec((text || '').trim())
  if (!m) return null
  const day = { y: Number(m[1]), m: Number(m[2]) - 1, d: Number(m[3]) }
  const probe = new Date(day.y, day.m, day.d)
  // Rejects Feb 31 and friends: the Date constructor rolls them forward.
  return probe.getMonth() === day.m && probe.getDate() === day.d ? day : null
}

export function todayDay() {
  const now = new Date()
  return { y: now.getFullYear(), m: now.getMonth(), d: now.getDate() }
}

export function formatDay(day) {
  return day ? `${MONTHS_SHORT[day.m]} ${day.d}` : ''
}

export function monthLabel(year, month) {
  return `${MONTHS_LONG[month]} ${year}`
}

export function shiftMonth(year, month, delta) {
  const total = year * 12 + month + delta
  return { year: Math.floor(total / 12), month: ((total % 12) + 12) % 12 }
}

// "Jul 3", "Jul 3 – 9" (same month), "Jul 30 – Aug 2", "From Jul 3" (open end).
export function formatSpan(start, end) {
  if (!start) return ''
  if (!end) return `From ${formatDay(start)}`
  if (daySerial(start) === daySerial(end)) return formatDay(start)
  const tail = start.y === end.y && start.m === end.m ? String(end.d) : formatDay(end)
  return `${formatDay(start)} – ${tail}`
}

// Six-or-five week grid for `month`, padded with the neighbouring months'
// days so every row holds seven cells.
export function monthGrid(year, month, { start, end, today } = {}) {
  const startDow = new Date(year, month, 1).getDay()
  const daysInMonth = new Date(year, month + 1, 0).getDate()
  const daysInPrev = new Date(year, month, 0).getDate()
  const cellCount = Math.ceil((startDow + daysInMonth) / 7) * 7
  const startSerial = daySerial(start)
  const endSerial = daySerial(end)
  const todaySerial = daySerial(today)

  const cells = []
  for (let i = 0; i < cellCount; i++) {
    const offset = i - startDow + 1
    let day
    let outside = false
    if (offset < 1) {
      outside = true
      const prev = shiftMonth(year, month, -1)
      day = { y: prev.year, m: prev.month, d: daysInPrev + offset }
    } else if (offset > daysInMonth) {
      outside = true
      const next = shiftMonth(year, month, 1)
      day = { y: next.year, m: next.month, d: offset - daysInMonth }
    } else {
      day = { y: year, m: month, d: offset }
    }
    const serial = daySerial(day)
    cells.push({
      key: `${serial}-${i}`,
      day,
      label: String(day.d),
      outside,
      future: todaySerial != null && serial > todaySerial,
      edge: serial === startSerial || serial === endSerial,
      between: startSerial != null && endSerial != null
        && serial > startSerial && serial < endSerial,
      isToday: serial === todaySerial,
    })
  }
  return cells
}
