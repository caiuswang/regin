// The directory name a branch becomes under `<repo>/.claude/worktrees/`.
//
// This MUST stay byte-identical to `slugify` in lib/agent_sdk/worktree.py —
// the launch form shows the operator where the tree will land before they
// commit to it, and a preview that disagrees with the server by even one
// character is a lie about a path. Note the order: strip BEFORE the length cap
// and again after, so a name whose leading punctuation is dropped does not then
// borrow a character back from the far end.
const MAX = 60

export function slugifyBranch(text) {
  return String(text)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, MAX)
    .replace(/^-+|-+$/g, '')
}
