"""Template for user-owned custom hook registrations.

This file IS git-tracked and contains NO personal information. It ships
as a starting template. To use custom hooks:

    cp hook_manager/custom_registry.example.py hook_manager/custom_registry.py
    # then edit custom_registry.py to uncomment / add what you want

The real `custom_registry.py` is gitignored — put your personal paths,
webhook tokens, ticket-specific pre-commit guards, etc. in there without
worrying about committing them.

`registry.py` loads `custom_registry.py` via `_load_custom_handlers()` with
try/except; a missing or broken custom_registry never brings down the
standard hooks.
"""

from __future__ import annotations

from .core import Handler  # noqa: F401
# Uncomment the imports you need:
# import re
# from .core import HookPayload, HookResponse, match_tool
# from .handlers.commit_guard import GuardedRepo, make_handler as make_commit_guard


CUSTOM_HANDLERS: list[Handler] = [
    # ────────────────────────────────────────────────────────────────
    # Example 1: Per-repo pre-commit guards.
    #   Fill in repo paths, branch names, and your check script. The
    #   handler only fires when Bash is running `git commit` AND the
    #   repo's current branch matches one of your entries.
    #
    # _MY_GUARDED_REPOS = [
    #     GuardedRepo(
    #         repo_dir='/path/to/your/repo',
    #         branch='feature/PT-XXXXX',
    #         check_script='/path/to/your/repo/.grit/check-staged.sh',
    #     ),
    # ]
    # Handler(
    #     name='commit_guard',
    #     events=['PreToolUse'],
    #     kind='gate',
    #     priority=20,
    #     fn=make_commit_guard(_MY_GUARDED_REPOS),
    # ),

    # ────────────────────────────────────────────────────────────────
    # Example 2: A toolchain gate written inline.
    #   This shape is useful for blocking project-specific shell tools
    #   (e.g. routing `mvn` through a Maven MCP, blocking direct `kubectl
    #   apply` to production, etc.). Define the handler function above
    #   CUSTOM_HANDLERS, then point `fn=` at it.
    #
    # _MVN_RE = re.compile(r'(?:^|[;&|]\s*)mvn\b')
    # _BLOCK_REASON = (
    #     'Direct `mvn` is not allowed. Use the maven MCP tools instead.'
    # )
    #
    # def block_mvn_handle(payload: HookPayload) -> HookResponse | None:
    #     if payload.tool_name != 'Bash':
    #         return None
    #     cmd = (payload.tool_input or {}).get('command', '')
    #     if isinstance(cmd, str) and _MVN_RE.search(cmd):
    #         return HookResponse(
    #             decision='block',
    #             decision_reason=_BLOCK_REASON,
    #             permission_decision='deny',
    #             permission_reason=_BLOCK_REASON,
    #         )
    #     return None
    #
    # Handler(
    #     name='block_mvn',
    #     events=['PreToolUse'],
    #     kind='gate',
    #     priority=10,
    #     predicate=match_tool('Bash'),
    #     fn=block_mvn_handle,
    # ),

    # ────────────────────────────────────────────────────────────────
    # Example 3: Your own handler (write it inline or import from anywhere).
    #
    # from .core import HookResponse, HookPayload
    # def my_stop_notifier(payload: HookPayload) -> HookResponse | None:
    #     import subprocess
    #     subprocess.run(['say', 'Claude is done'])
    #     return HookResponse(suppress_output=True)
    #
    # Handler(
    #     name='say_done',
    #     events=['Stop'],
    #     kind='notify',
    #     priority=150,
    #     fn=my_stop_notifier,
    # ),
]
