# Topic Proposals

Topic proposals are reviewable drafts for `.regin/topics/topic.json`. They let
you ask regin to suggest new topic graph entries without changing the approved
topic graph until a human accepts or merges each proposed topic.

## Provider

regin has one proposal provider, `external-agent`: it runs a configured
tool-using agent command (such as Claude Code), lets that agent inspect the
repo with its own Read/Glob/Grep tools, and monitors the run through session
tracing. There is no evidence pack — the agent reads the working tree
directly, and every path it proposes must exist on disk.

## Configure External Agents

Add external agents in `config/settings.local.json`:

```json
{
  "topic_proposal_external_agents": {
    "claude": {
      "command": "claude",
      "args": ["--print"],
      "timeout_seconds": 600
    }
  }
}
```

The key, such as `claude`, is the agent id used by the WebUI. `command` and
`args` are executed from the target repo root unless `cwd` is set.

During a run, regin also provides these environment variables to the external
agent:

| Variable | Meaning |
| --- | --- |
| `REGIN_TOPIC_PROPOSAL_DIR` | Proposal run directory. |
| `REGIN_TOPIC_PROPOSAL_OUTPUT` | Temp JSON output file the agent should write. |
| `REGIN_TOPIC_PROPOSAL_CANONICAL_OUTPUT` | Final artifact path; agents should not write it directly. |
| `REGIN_TOPIC_PROPOSAL_TRACE_ID` | Trace id for monitoring. |
| `REGIN_TOPIC_PROPOSAL_ID` | The run's proposal id. |
| `REGIN_TOPIC_PROPOSAL_FINISH_CMD` | Completion command the agent runs verbatim as its final step. |

The agent receives the generated `instructions.md` on stdin. It should write
final JSON to `REGIN_TOPIC_PROPOSAL_OUTPUT`, then signal completion by running
the `REGIN_TOPIC_PROPOSAL_FINISH_CMD` command (`regin topics proposal-finish
<id>`) exactly once as its last step. There is no fixed run timeout by default
— the finish signal is authoritative, and the
`topic_evolution.proposal_run_timeout_seconds` setting is only an optional
backstop. regin validates the temp output and then copies it to the canonical
`agent-output.json` in the proposal run directory.

## Start A Proposal

Proposals are created and reviewed from the WebUI:

1. Open the repo.
2. Go to `Topics`.
3. In `Proposal Runs`, choose a provider.
4. Enter a topic or focus in `Topic or focus`.
5. Click `Generate Proposal`.
6. For external-agent runs, watch the state badge and open `Trace` for details.
7. Review generated topics, then accept, merge, ignore, edit, or delete the run.

The topic/focus text is embedded directly in the agent's `instructions.md`
(as `topic_request`) and retained on the proposal output for review.

The same lifecycle is scriptable from the CLI (every command takes `--repo`,
and the read/diff/apply commands take `--json`): `regin topics propose`
drafts synchronously, then `proposal-list`, `proposal-show`,
`proposal-feedback`, `proposal-review-state`, `proposal-diff`, and
`proposal-apply` cover review through apply. See `regin topics --help` for
arguments.

## Proposal Artifacts

Each run writes under:

```text
.regin/topics/proposals/<run_id>/
```

| File | Meaning |
| --- | --- |
| `instructions.md` | Exact instructions sent to the agent. |
| `.tmp/agent-output.json` | Raw temp output written by the external agent. |
| `agent-output.json` | Validated copy of the temp output retained for review. |
| `wiki.md` | Draft wiki text generated from the proposal. |
| `stdout.log` / `stderr.log` | Capped command logs for debugging. |

The reviewable proposal itself (topics, revisions, feedback) and the run's
lifecycle state both live in the local SQLite database — the proposal ORM is
the source of truth and the WebUI reads from it. No `status.json` or
`topics.json` is written; a `status.json` found in an old run directory is
read only as a legacy fallback.

Proposal run states are:

```text
queued, running, completed, failed, cancelled, timed_out, waiting_for_permission
```

## Output Contract

External agents should produce JSON shaped like:

```json
{
  "version": 1,
  "topics": [
    {
      "id": "service",
      "label": "Service",
      "aliases": [],
      "intent": "Curated context for the service layer.",
      "status": "active",
      "refs": [{"path": "service/api.py", "role": "implementation"}],
      "edges": [],
      "commands": [],
      "include_globs": ["service/**"],
      "exclude_globs": [],
      "evidence_paths": ["service/api.py"],
      "wiki": "## Service\n\nThis topic's own wiki page."
    }
  ],
  "notes": []
}
```

Each topic carries its own `wiki` page — it becomes that topic's
`.regin/topics/wiki/<id>.md` on accept. A top-level `wiki` string is accepted
as a legacy fallback when no topic carries its own page. regin validates this
output before persisting the proposal.

## Architecture

The proposal pipeline has these layers:

1. `lib/topics/proposal_providers.py` is provider discovery — it advertises
   the single `external-agent` provider built from
   `settings.topic_proposal_external_agents`.
2. `lib/topics/proposal_external.py` orchestrates a run: builds
   `instructions.md`, spawns the agent, captures stdout/stderr, validates the
   agent's JSON output, guards the approved graph, and emits trace spans.
3. `lib/topics/proposal_drafting.py` validates the proposal against the
   `regin-topic-proposal-external-v1` contract and writes the run artifacts.
4. `lib/topics/proposals/` (package) owns proposal run directories, status
   load/save, listing, deletion, feedback threads, and review actions.

External-agent runs are asynchronous. The API creates the run directory,
writes a `queued` status row to the database, starts a background thread, and
returns immediately. The frontend polls:

```text
GET /api/repos/<repo>/topics/proposals/<run_id>/status
```

## Tracing

External-agent monitoring reuses the existing `session_spans` store. Each run
uses:

```text
trace_id = topic-proposal-<run_id>
```

The runner emits spans such as:

```text
proposal.agent.start
proposal.agent.instructions
proposal.agent.stdout
proposal.agent.stderr
proposal.agent.permission_request
proposal.agent.complete
proposal.agent.failure
```

The WebUI links proposal runs to the wrapper trace. When regin can correlate the
actual tool-using agent session, it also shows an `Agent Trace` link for the
richer Claude/Codex tool timeline.

## Safety Rules

External agents are treated as draft generators only:

- They must not modify `.regin/topics/topic.json`.
- regin snapshots the approved topic graph before running and fails the run if
  it changes.
- Generated refs and evidence paths must stay inside the repo and must exist in
  the working tree (there is no evidence pack to validate against).
- Permission prompts are not brokered in v1. If a command asks for interactive
  permission, the run becomes `waiting_for_permission` and shows the error in
  the Proposal Runs panel.
- Agents write to `.tmp/agent-output.json`; regin validates and copies only the
  accepted JSON into `agent-output.json` (and renders `wiki.md` from it).
- Generated proposals never auto-promote. A user must accept or merge topics
  explicitly through the WebUI.

## Review Actions

After a proposal completes, review it from the Topics page in the WebUI: edit,
comment on, regenerate, then accept, merge, ignore, or delete each proposed
topic. Accepting or merging is the only path that writes the approved
`topic.json`, and it is gated by a pre/post graph audit.

## MCP Tools

`lib/topics/mcp_server.py` exposes the same review loop to in-session agents
as a stdio MCP server named `topics`: `proposal_list`, `proposal_show`,
`proposal_diff`, `proposal_apply`, `proposal_review_state`,
`proposal_feedback_add`, and `proposal_feedback_list`. Each tool takes a
registered repo name or repo path and returns a plain-text summary; only
`proposal_apply` writes the approved graph, and drafting is deliberately not
exposed (start runs from the CLI or WebUI). It is registered the same way as
the memory server — via the regin-agents plugin's `.mcp.json`
(`regin-plugin/plugins/regin-agents/.mcp.json`), which launches it through
`bin/regin-mcp.sh` on the checkout's venv.
