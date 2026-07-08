// One-line summary of a POST /api/memory/reflect response — shared by the
// Memory list header and the Doctor page so the same run reads the same.
export function formatReflectSummary(r) {
  const skipped = r.dream_skipped ? `, ${r.dream_skipped} plan-skipped` : ''
  return (
    `reflect: ${r.examined} examined, ${r.merged} merged, ` +
    `${r.pairs_checked} pairs judged, ${r.contradictions} contradicted, ` +
    `${r.obsoleted} obsoleted, ${r.synthesized} synthesized, ` +
    `${r.promoted} promoted, ${r.held} held, ${r.dropped} dropped, ` +
    `${r.embedded} embedded, ${r.edges} edges, ${r.topics} topics, ` +
    `${r.decayed} decayed${skipped}`
  )
}
