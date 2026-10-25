## Question

Nothing to decide — a fact to establish before the prototype can be honest: does the existing PTY path carry a plain `$SHELL` unchanged, or does something in it assume the pane runs claude?

Do, on a throwaway branch, AFK:

* Read `lib/agent_bridge/terminal.py` (`open_terminal`, the `pty.fork` + `execvpe` path, `child_env`) and `attach.py` (`take_over`, `_pane_identity`, `_record`). List every place that assumes the foreground command is claude — `allowed_pane_commands` at registration and in `delivery._verify_identity`, the `bridge_panes` row shape, `_authorize` in `web/blueprints/terminal.py`.
* Spike it: `tmux -L regin-bridge new-session -d -s shell-test -c <cwd> $SHELL`, then open a grouped client the way `open_terminal` does, and confirm keystrokes, resize (`window-size` behaviour noted in the module docstring) and reattach after killing the client all work. Note the tmux version.
* Report what the shell case needs that the claude case does not (a row without a claude identity check? a second table? a `kind` column on `bridge_panes`?) — as facts and a size estimate, not a design. The lifecycle ticket decides.

Resolution comment: the list of claude-assumptions, the spike outcome, and the estimate. Link the spike script if one was written.
