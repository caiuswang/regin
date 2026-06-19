/**
 * Single source of truth for status → Badge color mappings.
 *
 * The audit found the same mappings re-derived inline across 6+ files
 * (SchemaExpansionPanel KIND_COLOR, severity ternaries in PatternRulesPanel /
 * RuleDetailView, state maps in SkillsView / UsersView / proposal lists), so
 * the same status could read as different colors in different views. Import
 * the relevant helper instead of hand-writing the ternary.
 *
 * Each returns a Badge `color` value: green | yellow | red | blue | purple | gray.
 */

const SEVERITY = { error: 'red', warn: 'yellow', warning: 'yellow', info: 'blue' }
export function severityColor(sev) {
  return SEVERITY[String(sev || '').toLowerCase()] || 'gray'
}

const DRIFT_KIND = {
  unknown_field: 'blue',
  missing_required: 'red',
  type_mismatch: 'yellow',
  enum_violation: 'yellow',
  unknown_tool: 'gray',
  unknown_event: 'gray',
}
export function driftKindColor(kind) {
  return DRIFT_KIND[kind] || 'gray'
}

const PROPOSAL_STATE = {
  draft: 'gray',
  proposed: 'blue',
  pending_ratification: 'yellow',
  ratified: 'green',
  approved: 'green',
  rejected: 'red',
  superseded: 'gray',
}
export function proposalStateColor(state) {
  return PROPOSAL_STATE[String(state || '').toLowerCase()] || 'gray'
}

/** Generic boolean → ok/neutral (e.g. active vs inactive, healthy vs idle). */
export function boolColor(on, { onColor = 'green', offColor = 'gray' } = {}) {
  return on ? onColor : offColor
}

export function useBadgeColor() {
  return { severityColor, driftKindColor, proposalStateColor, boolColor }
}
