"""Create the git worktree a launched session runs in.

An operator starting two sessions on one repo from the web UI has no terminal
in which to `git worktree add` first, so without this every run shares the
checkout's index and branch — two agents editing the same files, and the second
one's first commit carrying the first one's work. Launching into a worktree is
the only way the tier can offer more than one concurrent session per repo.

Worktrees land in `<repo>/.claude/worktrees/<slug>`, which is where Claude
Code's own worktree command puts them and what `scripts/sync-worktree-claude.sh`
already knows how to find — a second convention would leave half the tooling
looking in the wrong place.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from lib.activity_log import get_activity_logger
from lib.settings import settings

log = get_activity_logger("agent_sdk")

# Every call here is a metadata read or one `worktree add` against objects the
# repo already has; nothing fetches. A minute is generous for that and short
# enough that a wedged git does not hold the launch request open.
_GIT_TIMEOUT = 60
# Long enough to name a branch, short enough to stay a directory name on every
# filesystem the repo might live on.
_SLUG_MAX = 60
_WORKTREES_DIR = Path(".claude") / "worktrees"


class WorktreeRefused(RuntimeError):
    """A worktree that cannot be created — a reason to show, not a traceback."""


def _run(args: list[str], cwd: str | Path) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=str(cwd), capture_output=True, text=True,
                          timeout=_GIT_TIMEOUT, check=False)


def _failure_detail(proc: subprocess.CompletedProcess) -> str:
    """The one line of git's output worth showing the operator.

    `git worktree add` narrates its progress on stderr before it fails, so the
    first line is "Preparing worktree …" and the reason is further down — the
    naive first-or-last reading shows the wrong one about half the time.
    """
    lines = [ln.strip()
             for ln in ((proc.stderr or "") + (proc.stdout or "")).splitlines()
             if ln.strip()]
    for line in lines:
        if line.startswith(("fatal:", "error:")):
            return line
    return lines[-1] if lines else "git failed with no output"


def _git(args: list[str], cwd: str | Path) -> str:
    proc = _run(["git", *args], cwd)
    if proc.returncode != 0:
        raise WorktreeRefused(_failure_detail(proc))
    return proc.stdout.strip()


def repo_root(cwd: str | Path) -> Path:
    """The MAIN checkout owning `cwd`, even when `cwd` is itself a worktree.

    A linked worktree's `.git` is a *file* holding
    `gitdir: <main>/.git/worktrees/<name>`, so `--git-common-dir` is the only
    reading that lands on the checkout that owns the branches. Resolving
    `cwd`'s own top level instead would nest a worktree inside a worktree, one
    level deeper per launch.
    """
    path = Path(cwd)
    if not path.is_dir():
        raise WorktreeRefused(f"no such directory: {cwd}")
    common = (path / _git(["rev-parse", "--git-common-dir"], path)).resolve()
    if common.name != ".git":
        raise WorktreeRefused(f"{cwd} is not a normal git checkout")
    return common.parent


def worktrees_root(root: Path) -> Path:
    return root / _WORKTREES_DIR


def slugify(text: str) -> str:
    """A branch name as a directory name — `feat/new-ui` becomes `feat-new-ui`."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:_SLUG_MAX].strip("-")


def _validate_branch(root: Path, branch: str) -> None:
    """Reject a name git would reject, before a directory exists to clean up.

    The leading-dash guard is separate because `check-ref-format` would read
    such a name as one of its own flags and answer about the wrong thing.
    """
    if not branch:
        raise WorktreeRefused("name the branch for the new worktree")
    if branch.startswith("-"):
        raise WorktreeRefused(f"invalid branch name {branch!r}")
    if _run(["git", "check-ref-format", "--branch", branch], root).returncode:
        raise WorktreeRefused(f"git rejected the branch name {branch!r}")


def _ref_exists(root: Path, ref: str) -> bool:
    return _run(["git", "rev-parse", "--verify", "--quiet",
                 f"{ref}^{{commit}}"], root).returncode == 0


def _branch_exists(root: Path, branch: str) -> bool:
    return _run(["git", "show-ref", "--verify", "--quiet",
                 f"refs/heads/{branch}"], root).returncode == 0


def default_base(root: Path) -> str:
    """The branch a new one forks from when the caller names none — the
    checkout's own HEAD, which is what a `git worktree add -b` at a terminal
    would have used."""
    head = _run(["git", "symbolic-ref", "--short", "--quiet", "HEAD"], root)
    return head.stdout.strip() if head.returncode == 0 else "HEAD"


def branches(root: Path) -> list[str]:
    out = _git(["for-each-ref", "--format=%(refname:short)", "refs/heads"], root)
    return [line for line in out.splitlines() if line]


def _registered(root: Path) -> dict[str, str | None]:
    """Every worktree git knows for this repo, as path → branch.

    A key present with a `None` value is a real distinction, not an absence: a
    detached worktree occupies the path just as firmly as a branched one, and
    collapsing the two would let `ensure_worktree` mistake it for free space.
    """
    found: dict[str, str | None] = {}
    current: str | None = None
    for line in _git(["worktree", "list", "--porcelain"], root).splitlines():
        if line.startswith("worktree "):
            current = line[len("worktree "):]
            found[current] = None
        elif line.startswith("branch refs/heads/") and current:
            found[current] = line[len("branch refs/heads/"):]
    return found


def _add_arguments(root: Path, branch: str, base: str) -> list[str]:
    """`git worktree add` arguments for a branch that may or may not exist yet.

    An existing branch is checked out rather than re-created: an operator who
    names the branch they were on last week means "put that back in a tree for
    me", and `-b` on it would fail outright.
    """
    if _branch_exists(root, branch):
        return [branch]
    base = base or default_base(root)
    if not _ref_exists(root, base):
        raise WorktreeRefused(f"unknown base ref {base!r}")
    return ["-b", branch, base]


def _sync_claude_config(path: Path) -> None:
    """Copy the main checkout's gitignored `.claude/` into the fresh worktree.

    `.claude/` is gitignored, so `git worktree add` never checks it out and a
    new tree starts with no skills, agents or commands. Doing it at creation is
    what makes it land in time: a SessionStart hook finishes after the harness
    has already enumerated skills, so the first session in the tree would see
    none of them.

    Best-effort by design — a repo that carries no `.claude/` is an ordinary
    case, and a launch must not fail because config copying did.
    """
    script = settings.project_root / "scripts" / "sync-worktree-claude.sh"
    if not script.is_file():
        return
    proc = _run(["bash", str(script), "--auto", str(path)], path)
    if proc.returncode != 0:
        log.error("worktree_claude_sync_failed", path=str(path),
                  detail=_failure_detail(proc)[:400])


def ensure_worktree(cwd: str | Path, branch: str, base: str = "") -> dict:
    """The worktree for `branch`, creating it if this repo has none.

    Reuse rather than a uniquifying suffix: an operator launching a second
    session on a branch they already have a tree for means that tree, and a
    `-1` copy beside it would split their work across two directories neither
    of which holds all of it. A slug already taken by a *different* branch is
    refused for the same reason — silently running somewhere else is worse
    than saying no.
    """
    root = repo_root(cwd)
    branch = branch.strip()
    _validate_branch(root, branch)
    slug = slugify(branch)
    if not slug:
        raise WorktreeRefused(f"branch name {branch!r} has no usable slug")
    path = worktrees_root(root) / slug
    registered = _registered(root)
    if str(path) in registered:
        existing = registered[str(path)]
        if existing != branch:
            raise WorktreeRefused(f"{path} is already the worktree for "
                                  f"{existing or 'a detached HEAD'}")
        return _result(root, path, branch, created=False)
    if path.exists():
        raise WorktreeRefused(f"{path} already exists and is not a worktree")
    args = _add_arguments(root, branch, base.strip())
    path.parent.mkdir(parents=True, exist_ok=True)
    _git(["worktree", "add", str(path), *args], root)
    _sync_claude_config(path)
    log.write("worktree_created", path=str(path), branch=branch,
              repo_root=str(root))
    return _result(root, path, branch, created=True)


def existing_worktree(cwd: str | Path, branch: str) -> dict | None:
    """The tree already holding `branch`, or None — never creating one.

    `ensure_worktree`'s create-if-missing is right at launch and wrong
    afterwards: a tree materialised later holds the branch's committed state,
    not the work that ran in the tree that is now gone, and a caller asking
    "where did this run happen" must be told nowhere rather than handed a
    fresh checkout that looks like an answer.
    """
    root = repo_root(cwd)
    branch = branch.strip()
    path = worktrees_root(root) / slugify(branch)
    if _registered(root).get(str(path)) != branch:
        return None
    return _result(root, path, branch, created=False)


def _result(root: Path, path: Path, branch: str, *, created: bool) -> dict:
    return {"path": str(path), "branch": branch, "repo_root": str(root),
            "created": created}


def options(cwd: str | Path) -> dict:
    """What a client needs to offer the worktree form for one working
    directory — served rather than guessed, because the branches and the root
    belong to the checkout the server can see and the browser cannot."""
    root = repo_root(cwd)
    return {"repo_root": str(root), "worktrees_root": str(worktrees_root(root)),
            "branches": branches(root), "default_base": default_base(root)}
