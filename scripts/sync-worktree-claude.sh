#!/usr/bin/env bash
#
# sync-worktree-claude.sh — copy gitignored Claude config into a git worktree.
#
# The entire .claude/ directory is gitignored (see .gitignore), so `git worktree add`
# never checks it out. Fresh worktrees therefore start with no skills, agents, commands,
# templates, etc. — only the worktree-local settings.local.json the tool writes itself.
# This script copies that config from the MAIN repo's .claude/ into a worktree's .claude/
# so skills and subagents work there too.
#
# Usage:
#   scripts/sync-worktree-claude.sh [TARGET_WORKTREE]   # default: current directory
#   scripts/sync-worktree-claude.sh --all               # sync every worktree
#   scripts/sync-worktree-claude.sh --dry-run [TARGET]  # show what would change
#   scripts/sync-worktree-claude.sh --auto [TARGET]     # hook mode: silent no-op when
#                                                       # TARGET is not a worktree, and
#                                                       # never overwrites files the
#                                                       # worktree has edited more recently
#
# Re-running is safe: it overwrites config files but never deletes worktree-local files
# (settings.local.json is preserved; nothing is removed from the target).

set -euo pipefail

DRY_RUN=""
ALL=""
AUTO=""
TARGET=""

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN="--dry-run" ;;
    --all)     ALL="1" ;;
    --auto)    AUTO="1" ;;
    -h|--help)
      grep '^#' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) TARGET="$arg" ;;
  esac
done

# The main repo is the worktree whose path is NOT nested under .../.claude/worktrees/.
MAIN_REPO="$(git worktree list --porcelain \
  | awk '/^worktree /{print substr($0,10)}' \
  | grep -v '/\.claude/worktrees/' \
  | head -n1)"

if [[ -z "${MAIN_REPO:-}" || ! -d "$MAIN_REPO/.claude" ]]; then
  echo "error: could not locate main repo .claude/ (are you inside the repo?)" >&2
  exit 1
fi

SRC="$MAIN_REPO/.claude"

# Config that should be shared into worktrees. Excludes:
#   worktrees/          — would recursively copy worktrees into themselves
#   settings.local.json — worktree-local, written per worktree
#   .DS_Store           — macOS noise
#   .venv/ node_modules/— heavy and non-relocatable (venvs bake absolute paths);
#                         skill setup scripts rebuild them on first use
#   *.lock              — runtime state (e.g. scheduled_tasks.lock), never config
RSYNC_EXCLUDES=(
  --exclude 'worktrees/'
  --exclude 'settings.local.json'
  --exclude '.DS_Store'
  --exclude '__pycache__/'
  --exclude '.venv/'
  --exclude 'node_modules/'
  --exclude '*.lock'
)

sync_one() {
  local dest_wt="$1"
  local dest=".claude"

  if [[ ! -d "$dest_wt" ]]; then
    echo "skip: not a directory: $dest_wt" >&2
    return
  fi

  # Resolve to an absolute path so rsync targets are unambiguous.
  dest_wt="$(cd "$dest_wt" && pwd)"

  if [[ "$dest_wt" == "$MAIN_REPO" ]]; then
    echo "skip: refusing to sync the main repo onto itself ($dest_wt)" >&2
    return
  fi

  if [[ -n "$AUTO" && "$dest_wt" != */.claude/worktrees/* ]]; then
    return
  fi

  # In auto mode --update keeps files the worktree itself edited more
  # recently; a manual run stays a full overwrite so it can force-reset.
  local extra_flags=()
  [[ -n "$AUTO" ]] && extra_flags+=(--update)

  mkdir -p "$dest_wt/$dest"
  echo "==> syncing .claude/ -> $dest_wt/$dest ${DRY_RUN:+(dry-run)}"
  rsync -a ${DRY_RUN} --itemize-changes \
    "${extra_flags[@]+"${extra_flags[@]}"}" \
    "${RSYNC_EXCLUDES[@]}" \
    "$SRC/" "$dest_wt/$dest/"
}

if [[ -n "$ALL" ]]; then
  git worktree list --porcelain \
    | awk '/^worktree /{print substr($0,10)}' \
    | grep '/\.claude/worktrees/' \
    | while read -r wt; do sync_one "$wt"; done
else
  sync_one "${TARGET:-$PWD}"
fi

echo "done."
