// Shared primitives for applying proposal draft topics.
//
// The per-topic DiffPanel and the bulk useProposalApplyAll both drive the same
// /apply contract; keeping the strategy pick, option defaults, endpoint shape,
// and "still pending?" rule here stops the two paths from drifting apart (e.g.
// one adding a 4th option the other never sends).

// A draft topic still needs applying unless it was accepted / merged / ignored.
export function isPendingTopic(topic) {
  return !topic.review_status || topic.review_status === 'pending'
}

// Natural starting strategy: replace when the id collides with an approved
// topic, otherwise create. Never returns 'merge' — that needs a human target.
export function initialApplyStrategy(topicId, approvedTopicIds) {
  return (approvedTopicIds || []).includes(topicId) ? 'replace' : 'create'
}

// Default resolution options — mirrors legacy behavior (prune orphan edges).
export function defaultApplyOptions() {
  return { prune_orphan_edges: true, drop_dead_refs: false, dedupe_aliases: false }
}

// Endpoint for a single proposed topic; `action` is 'diff' or 'apply'.
export function proposalTopicPath(repo, proposalId, topicId, action) {
  return `/repos/${repo}/topics/proposals/${proposalId}/topics/${topicId}/${action}`
}
