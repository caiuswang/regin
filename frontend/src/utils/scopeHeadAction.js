import { DEFECTS_ANCHOR, SLICES_ANCHOR as SLICES } from './featureLinks.js'

//: The query flag that asks the feature page's Slices card to open its arm
//: sheet on arrival — the head cannot open a sheet that lives on another page.
export const ARM_SHEET_QUERY = 'arm'

// F155 R1: what the scoped Tasks head's primary button says and does, per
// `next_step.kind`. `to` is a route; `runTaskId` asks for `POST /run` on that
// task instead. `null` means no primary button at all.
const RULES = {
  write_spec: id => ({ label: 'Write the spec', to: { path: `/features/${id}`, hash: '#feature-spec' } }),
  slice: (id, s) => ({ label: s.label, to: { path: `/features/${id}`, hash: `#${SLICES}` } }),
  arm_slices: (id, s) => ({
    label: s.label,
    to: { path: `/features/${id}`, query: { [ARM_SHEET_QUERY]: '1' }, hash: `#${SLICES}` },
  }),
  run: (_id, s) => {
    const m = /^\/tasks\/(\d+)$/.exec(s.target || '')
    return m ? { label: 'Run the next task', runTaskId: Number(m[1]) }
      : { label: 'Run the next task', to: s.target }
  },
  watch: (_id, s) => ({ label: 'Watch the running task', to: s.target }),
  resolve_defects: (id, s) => ({ label: s.label, to: { path: `/features/${id}`, hash: `#${DEFECTS_ANCHOR}` } }),
  ship: id => ({ label: 'Ship', to: `/features/${id}` }),
  reopen: (id, s) => ({ label: s.label, to: `/features/${id}` }),
}

export function scopeHeadAction(featureId, step) {
  const rule = step && RULES[step.kind]
  return rule ? rule(featureId, step) : null
}
