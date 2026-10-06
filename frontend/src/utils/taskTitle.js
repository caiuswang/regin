// A task title can cite its feature as `[F<id> …](feature:<id>)` (a feature's
// spec and build steps do), so it reads as words here rather than markdown: the
// feature's current title when the row carries one, else the label stamped
// when the title was written.
import { parseRefs } from './refs.js'

export function taskTitle(task, featureTitle = task?.feature_title) {
  const title = task?.title ?? ''
  let out = ''
  let at = 0
  for (const ref of parseRefs(title)) {
    const live = ref.kind === 'feature' && String(task.feature_id) === ref.key && featureTitle
    out += title.slice(at, ref.start) + (live ? `F${ref.key} ${featureTitle}` : ref.label)
    at = ref.end
  }
  return out + title.slice(at)
}
