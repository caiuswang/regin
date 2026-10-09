# regin

A workbench for building software with AI coding agents. This glossary fixes the
words regin's own pipeline uses — what a feature is, what a plan is, what counts as done — so two
sessions arguing about the same thing use the same noun for it.

Definitions only. How any of it is stored or enforced belongs in
`ARCHITECTURE.md`, not here.

## Language

### The pipeline

**Feature**:
A unit of delivery that holds a spec and outlives the work scheduled against it.
It is the address every defect is blamed on.
_Avoid_: epic, story, project

**Spec**:
What a feature is for, plus the behaviours it must exhibit. It lives on the
feature, and it is argued out hourly while the feature is being built.
_Avoid_: requirements doc, design doc, PRD

**Acceptance item**:
One falsifiable claim about a feature's behaviour. Its durable form is a test:
absent or red it is a checklist, green it is the ship gate, green afterwards it
is a regression watch.
_Avoid_: criterion, requirement, checklist item

**Task**:
One runnable unit of work belonging to a feature. Captured inert — a task
nobody armed has nothing to run.
_Avoid_: sub-issue, ticket, story

**Run**:
One execution of a task. A run settling says *this session finished*, never
*this feature shipped*.
_Avoid_: job, execution, session

**Done-claim**:
The declaration that a feature shipped. It is an act on the feature, performed
by an operator, and it is the boundary every later defect is blamed against.
Nothing derives it.
_Avoid_: completion, closed, finished

### Planning

**Plan**:
Not an entity. A plan is authored as a section of its feature's spec — a table
of slices and the edges between them — and transcribed into that feature's
unarmed tasks. What exists afterwards is a query over those tasks, not a row.
_Avoid_: task graph (when used to mean a stored object), draft, proposal

**Slice**:
One task within a plan, sized to one run. It is a whole task from birth, not a
lesser thing promoted later, and it keeps the identity its spec gave it.
_Avoid_: step, subtask, node

**Integration branch**:
The one branch a feature's slices fork from and merge back into, so that a
slice's dependencies are code it can actually see. A feature has exactly one,
and it is not the repo's default branch.
_Avoid_: develop, staging, trunk

**Transcription**:
Turning a spec's slice table into tasks and edges. It decides nothing — a
transcription that fills a gap the spec left has moved the approval point back
onto the graph.
_Avoid_: planning, generation, expansion

**Approval**:
The human act that makes a plan's slices runnable. It *is* arming — there is no
second approval concept beside it.
_Avoid_: sign-off, acceptance (which means something else here), review

**Arm**:
Giving a task the execution parameters that make it runnable. The privileged
step: captured tasks arrive inert and an authenticated human arms them.
_Avoid_: schedule, enqueue, activate

### Defects

**Defect**:
A declared record of something that went wrong, addressed to a feature. Declared
by a person — nothing infers one from what was typed.
_Avoid_: bug (when naming the record), issue, incident

**Unattributed**:
A defect blamed on no feature. A first-class state, not a gap waiting to be
filled: a wrong parent is worse than an honest blank.
_Avoid_: orphaned, unassigned, untriaged

**Escape**:
A defect blamed on a feature that had already made its done-claim. Reported
beside rounds, never folded into them.
_Avoid_: regression, post-release bug

**Round**:
One completion that was later walked back. Rounds-to-done is one build plus
every such reversal — the number the whole pipeline exists to produce.
_Avoid_: iteration, attempt, cycle

**Missing check**:
The answer a defect must carry when it closes: the check that would have caught
it. The answer is expected to name a test.
_Avoid_: root cause, lesson, postmortem

**Gate**:
A check a machine enforces. Advice is not a gate, and a gate that can only be
passed by faking it is worse than none.
_Avoid_: rule, guideline, standard

### Provenance

**Attribution**:
The declaration that a session was building a particular feature. Recorded,
never inferred — no branch name, directory or timestamp implies it.
_Avoid_: linking, tagging, association
