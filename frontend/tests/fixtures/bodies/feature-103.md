# Fix repo wiki auto session — route the auto proposal-review agent through the Agent SDK

## Intent
The review agent that regin launches on its own after a topic/wiki proposal draft
completes (`maybe_generate_review_note` → `start_review_run`) must be a session regin
owns: SDK-launched, with an `agent_runs` row, steerable from its trace page while it
runs and resumable after. Today it is a raw `subprocess.Popen(["claude","--print",…])`
(`lib/topics/proposal_review.py::_review_agent_worker`), so its trace page
(e.g. `/trace/sessions/6fe5ed84-…`, `llm_surface=topic-proposal-review`) shows
HeldSessionBar: "Still running under the Claude CLI, on a terminal regin can't type into."

The drafting agent already solved this (`proposal_external._run_via_agent_sdk`, gated by
`_sdk_runner_enabled`). The review path never got the same branch.

Non-goals: the drafting path, the blocking `generate_review_note` (sync `complete()` path),
drift-judge / distiller / dreamer spawns, HeldSessionBar copy or UI, orphan take-over.

## Root cause
| # | Fact | Where |
|---|---|---|
| 1 | Review worker always Popens `spec.argv`; no SDK branch | `proposal_review.py:616-660` |
| 2 | Reviewer argv = `claude --print --permission-mode bypassPermissions --model sonnet --allowedTools Read,Glob,Grep` | `memory/adapters.py::resolve_proposal_reviewer` + configured agent |
| 3 | `_sdk_run_options` maps only `--model`, `--permission-mode`; any other flag ⇒ None ⇒ subprocess fallback. `--allowedTools` would force fallback even if a branch existed | `proposal_external.py:647-679` |
| 4 | `RunOptions` / `supervisor.launch_run` carry no `allowed_tools` | `agent_sdk/client.py:51`, `supervisor.py:232` |
| 5 | No `agent_runs` row ⇒ `resume_refusal` → `cli_held` while the child lives | `agent_sdk/resumable.py::_refusal` |

## Shape
```python
# lib/agent_sdk/client.py
@dataclass
class RunOptions:
    env: dict[str, str]; permission_mode: str; model: str; effort: str
    allowed_tools: list[str] = []          # NEW → ClaudeAgentOptions.allowed_tools

# lib/agent_sdk/supervisor.py
def launch_run(prompt, *, ..., allowed_tools: list[str] | None = None) -> RunHandle  # NEW kwarg

# lib/topics/proposal_external.py
_ARGS_AS_OPTIONS = {"--model": "model", "--permission-mode": "permission_mode",
                    "--allowedTools": "allowed_tools"}          # NEW entry
# gate + arg mapping reusable from an argv (SpawnSpec) not only an agent config

# lib/topics/proposal_review.py
def _review_agent_worker(repo, proposal_id, spec, prompt):
    if sdk gate passes for spec.argv:  _review_via_agent_sdk(...)   # NEW
    else:                              existing Popen path, unchanged
```
`allowed_tools` value is the comma-split list (`"Read,Glob,Grep"` → `["Read","Glob","Grep"]`).

## Rules
Runner choice for the review agent (same gate as drafting):

| `topic_evolution.proposal_sdk_runner` | `agent_sdk.enabled` | argv[0] basename is a claude command | every argv flag mappable/ignorable | Runner |
|---|---|---|---|---|
| off | * | * | * | subprocess (today) |
| on | off | * | * | subprocess |
| on | on | no (codex/kimi/wrapper) | * | subprocess |
| on | on | yes | no | subprocess |
| on | on | yes | yes | **agent SDK** |

SDK review run:

| Aspect | Behaviour |
|---|---|
| launch | `supervisor.launch_run(prompt, cwd=spec.cwd or repo, env=overlay, one_shot=True, permission_mode, model, allowed_tools)` |
| env overlay | `REGIN_TOPIC_REVIEW_ID`, `REGIN_TOPIC_REVIEW_OUTPUT`, `REGIN_TOPIC_REVIEW_FINISH_CMD`, `REGIN_LLM_SURFACE=spec.surface_id` (same keys as Popen path) |
| review status | after launch, `review-status.json` gains `agent_trace_id = handle.trace_id` (merge, not overwrite) |
| launch raises | status `state=failed` (merged, `signaled` preserved), logged; never raises into the run thread |
| completion | unchanged: `review-finish` is authoritative; worker `handle.wait()` then logs `review_agent_exited_unsignaled` if not signaled |
| outcome `failed` and not signaled | status `state=failed` (merged) |
| timeout | none added — no timer kill on the SDK path (operator stops it from the session page; root-cause discipline forbids a clock) |

## Invariants
- The Popen path is byte-for-byte unchanged when the gate says subprocess.
- `start_review_run` stays non-blocking (worker still on a daemon thread).
- `_sdk_run_options` for the drafting agent returns the same dict as before for every argv that contained no `--allowedTools`.
- An SDK-launched review session resolves `resume_refusal(session_id) != 'cli_held'` while running (`run_live`) and `''` after it ends.

## Acceptance
```gherkin
Scenario: Auto review launches through the SDK when the tier is on
  Given proposal_sdk_runner=on, agent_sdk.enabled=on, reviewer argv
        "claude --print --permission-mode bypassPermissions --model sonnet --allowedTools Read,Glob,Grep"
  When start_review_run runs for a drafted proposal
  Then supervisor.launch_run is called once with one_shot=True, model="sonnet",
       permission_mode="bypassPermissions", allowed_tools=["Read","Glob","Grep"]
  And env contains REGIN_LLM_SURFACE="topic-proposal-review" and the three REGIN_TOPIC_REVIEW_* keys
  And subprocess.Popen is not called
  And review-status.json has agent_trace_id equal to the handle's trace_id

Scenario: Tier off keeps the subprocess
  Given proposal_sdk_runner=off
  When start_review_run runs
  Then subprocess.Popen is called with spec.argv and launch_run is not

Scenario: Non-claude or unmappable reviewer falls back
  Given the tier is on and reviewer argv[0] is "codex" (or carries an unknown flag like "--foo x")
  When start_review_run runs
  Then subprocess.Popen is used

Scenario: allowed_tools reaches the SDK options
  Given RunOptions(allowed_tools=["Read","Glob","Grep"])
  Then _run_overrides returns {"allowed_tools": ["Read","Glob","Grep"]}
  And RunOptions() with no allowed_tools contributes no "allowed_tools" key

Scenario: Launch failure is recorded, not raised
  Given launch_run raises
  When the review worker runs
  Then review-status.json state == "failed" and the proposal run is unaffected

Scenario: Drafting arg mapping unchanged
  Given drafting agent args ["--print","--model","haiku","--permission-mode","bypassPermissions"]
  Then _sdk_run_options returns {"model":"haiku","permission_mode":"bypassPermissions"}

Scenario: Real path — the trace page is steerable (manual check)
  Given regin serve running with the tier on and auto_review_notes on
  When a proposal draft completes and the auto review starts
  Then its /trace/sessions/<child-id> page shows the composer, not HeldSessionBar,
       and /api resume_refusal for that id returns "run_live"
```
Tests: extend `tests/topics/test_proposal_review.py` and `tests/topics/test_proposal_sdk_runner.py`; add a `_run_overrides` case under the agent_sdk tests.
