# The feature pipeline

*A design proposal. Fold the decisions into `ARCHITECTURE.md` and delete this
file once the pipeline exists.*

*The ledger half of this proposal — declaring a session's feature, rolling it up
into cost and escapes, and reading it back — was specified as CAI-242 and is
built: `lib/features/` and `regin feature attribute|ledger`. CAI-242 and its
nine decision tickets supersede this file wherever the two disagree.*

## The problem

A feature is rarely right the first time: it ships, it turns out broken, it is
patched, repeatedly. Nothing records those rounds, so the cost is invisible, no
bug is attached to the feature that produced it, and nothing is learned.

The unit of learning is **one feature and its defects**, not a rate.
`scripts/rework_probe.py` shows the aggregate shape from `git log` alone, but an
aggregate cannot be opened: it names no feature, no bug, and no missing check.
The per-defect records already exist unharvested — user reports sit in `prompt`
spans in the trace, and failed acceptance items sit in the memory store
(`lib/goal_feedback.py`) — but neither carries a feature identity. Supplying
that identity, and a round counter on it, is what this design is for.

## The spec stage owes nothing to the code layout

At authoring time you cannot know which files will be added or changed. A spec
that must name them is a spec that specifies the implementation before the
design exists, and it will be wrong.

So the spec is **not** a topic node, not a topic proposal, and is never
validated against the working tree. It holds what the feature is for, in prose
kept deliberately thin, and the behaviours it must exhibit as falsifiable
acceptance items. It mentions code only as orientation, never as a promise
something will bind to.

## The spec lives on the feature row, never in the project's git

A spec is revised many times an hour while it is being argued out, is edited by
whoever is in the conversation, and is largely inert once the feature lands.
Project git is the wrong home for all three properties: the churn pollutes
review and blame, the document does not belong in the shipped artifact, and at
spec time there is often no branch to commit against yet.

Its home is `features.body`, written at birth (`regin feature new --spec`),
replaced from a session (`regin feature spec <ID> --file -`) or from the
feature page, and read back wherever the feature is. A remotely tracked
feature keeps its spec in the tracker instead — the issue description, or a
tracker document for anything longer — because that is where its state,
priority and conversation already live, which keeps **one writer per field**.

**No spec file is written into `docs/` unless the user asks for one.** A file
is a third copy that nothing re-reads: no run's prompt carries it, no surface
serves it, and it states the design as it was the day someone typed it while
the row beside it moves on. `docs/` also grows one such file per feature
forever. A design document the user asks for is a different artifact and stays
welcome.

regin holds a **read-through cache**, not a copy: the same pattern
`agent_task_links` already uses, where a snapshot is refreshed on an event (the
attach, or an operator asking) and never on a timer or on read. The cache is
what keeps the tracker off the critical path of actually running a feature — a
dead API key degrades to "the spec is stale", not "nothing can run".

### Revision history is free evidence

However many times the spec was revised while the feature was being built is a
leading indicator, distinct from the defect count and available from the
tracker's own description or document history. It speaks to the real cause:
features take rounds because the understanding was wrong, not because the code
was sloppy.

## Acceptance items become tests, not anchors

The durable form of an acceptance item is **a test**. This is the pivot the rest
of the design rests on.

| Phase | State of the acceptance list | Meaning |
|---|---|---|
| Pre-code | tests absent or red | the checklist, mechanically |
| At ship | tests green | the gate |
| After ship | tests still green in CI | behavioural regression watch |

One list, three jobs — on a substrate that checks behaviour. An identifier
existing proves only that a shape was built; a passing test proves the thing
works. Writing the acceptance list as test names also makes the spec's bar
machine-checkable without anyone re-reading the spec.

This matters beyond convenience. Advice is optional and gets ignored; a gate is
mechanical and cannot be. An improvement that lands as a test compounds. One
that lands as a paragraph does not.

## Where the topic graph does belong

The topic graph, its wikis, and the anchor-drift detector are **post-ship**
machinery, and they are good at exactly that. `lib/topics/wiki_anchors.py`
extracts identifier tokens from a wiki's inline code spans and grounds them
token-exact against the topic's ref files; `lib/topics/content_drift.py` treats
*a cited anchor vanishing* as the materiality test. Both require the code to
exist, and `validate_topic` rightly refuses a ref that does not
(`graph.dead_ref`, severity `error`, enforced by the `pre-commit` hook).

So anchors do one job — noticing when code moves out from under its
documentation — and they do it after the feature lands. Do not try to author
them at spec time.

At ship, the feature's narrative is folded into the topic wiki of the subsystem
it belongs to, written against the code as it actually turned out. Most features
collapse into an existing subsystem's wiki; a new standing node is the rare
exception earned by becoming a real subsystem of its own. Without that default
the taxonomy becomes a log and stops being navigable — the same unbounded-growth
failure the auto-memory budget rule exists to prevent.

## Defect records

A defect gets its own row (`lib/defects/`), not a `kind='defect'` memory. It
carries lifecycle axes a memory has none of — open or closed, attributed or not
— and folding those into tags would put two axes on one column. The *lesson* a
defect yields when it closes is still a memory.

**A defect's address is the feature key.** That key is stable from the first day
of the spec, and a topic node for the feature may never exist at all.

Each record carries the symptom in the reporter's words and the session it was
reported in. On close it carries one more field: **what check would have caught
this** — and the answer is expected to name a test, which is then written.
Aggregated across a subsystem, those answers are a ranked list of missing gates:
the only output of this design that changes future behaviour rather than
describing past behaviour.

A defect may also point at the tracker item that will close it. That pointer is
for scheduling the fix; it is not the record of the defect, and the two are
never written from each other. `defects.status` answers *is this bug still
real*, a tracker item answers *is this work still scheduled*, and closing a
defect demands a missing-check answer that no state transition can supply.
Divergence between them is surfaced on request and left alone.

## Rounds come from declared transitions, not from inference

**rounds-to-done = 1 + every completion that was later walked back.** One
build, plus each time the work was called done and then reopened.

Each input is a fact somebody recorded: a tracker state the work occupied, and
the moment it left. `lib/defects/rounds.py` is a pure function over that
history — no prose is parsed, no clock is consulted, no proximity is assumed.

Two properties of the tracker make this the only reliable reading, both
verified against a live issue rather than assumed:

- **A completion that was *left* is the signal**, not the completion itself. A
  state history records each occupancy with its end, so a Done later vacated is
  a round and a Done still held is not.
- **The current row cannot substitute.** Reopening an issue *clears* its
  completion timestamp, so a feature reopened and re-completed looks, in the
  snapshot alone, exactly like one that was right the first time.

`canceled` is not a completion: abandoning work is not finishing it, so leaving
it says nothing about rework.

### Two numbers, one of which is not rounds-to-done

`Defect.round` counts **fix attempts on one defect** — how many times that bug
was called fixed and was not. It is not rounds-to-done and does not aggregate to
one: summing it across a feature derives "we were done" from "a defect was
reopened", which nobody declared. The two are orthogonal, not two readings of
the same quantity: the sum can be high on a feature that never shipped, and 1 on
a feature shipped and walked back twice.

### The five stages

`regin feature stage <ID>` names one of `spec`, `building`, `shipped`,
`escaped` or `abandoned`. `escaped` is `shipped` plus at least one defect
blamed on it since — it replaced `reworking`, which fired only on a completion
walked back and so could never fire once a shipped issue stayed Done.
`abandoned` is a capability taken down or a feature dropped; it costs no round,
because nothing was walked back, and it is reachable from every other stage and
is not terminal.

Every stage is read from the feature's own `feature_events` log: the last
event's kind, plus one question about escapes. `unknown` used to be a sixth
answer — what a reader saw when no tracker answered — and it is gone with the
fetch, because there is nothing left on this path that can fail to answer.

### Post-ship rework is `escapes`, not a reopened issue

A shipped feature stays Done — filing a defect never reopens its tracker issue,
because a board that flaps is a board nobody trusts. Rework after ship is read
from the ledger instead, as **escapes**: the count of defects blamed on a
feature that had already shipped. It is reported beside rounds and never folded
into it.

Blame is fixed at one declared instant rather than by comparing two clocks. When
a defect's feature is *first* set — on open, or on a later attribution — the row
is pointed at that feature's current last event, and an escape is
`blamed_event.kind == 'shipped'`. Comparing the report's timestamp against a
completion moment would compare two machines' clocks, so a defect near the
boundary would land on either side of it by host drift. The pointer is why the
metric reads *blamed*, not *found*: a bug found pre-ship but attributed later
counts as an escape, and that distortion is admitted rather than papered over.

A pointer at an immutable row also has no stale reading and no absent one. Its
predecessor cached the tracker's state *word*, which could be missing (nobody
looked), stale, or a vocabulary regin did not carry — three readings for one
column, and two gap counters to hold them apart.

### Why not detect defects from what people typed

A bug report is a human speech act. Matching prose for it — by regex or by
classifier — only moves the error bars, and an inferred row in the ledger means
the metric measures the inference as much as the work. An earlier revision of
this design harvested reports out of `prompt` spans, decided rounds from the
words *still* and *again*, and dated suspect files by a time window; it was
removed. If a defect is worth counting, it is worth declaring.

**Unattributed remains a first-class state.** A wrong parent inflates one
feature's rounds and exonerates another, and corrupted data that looks fine is
worse than an honest gap.

## Where each artifact lives

Each artifact lives where its natural editors and its natural cadence are:

| Artifact | Home | Why |
|---|---|---|
| spec prose and its revisions | `features.body`, or the tracker when the feature is tracked remotely | churns hourly, edited by whoever is in the room, not part of the shipped code |
| acceptance items, as tests | the repo | they are code, and they must run in CI |
| defect records and their missing-check answers | regin's own `defects` table | lifecycle state the tracker has no field for, plus the link back to the session |
| post-ship narrative | the subsystem's topic wiki | durable, reviewed with the code, drift-watched |

The join is the feature's id. A session is tied to a feature by one
declaration, `regin feature attribute` — **not** by its branch. Nothing captures a branch
name: not `sessions`, not `lib/trace/`, not `hook_manager/`. The declaration
lands in `session_features`, mirroring `session_repos`; an unattributed session
is the *absence* of a row, which is most of them and always will be.

`regin feature ledger <ID>` reads it back. Cost is the live sum of the declared
sessions' `sessions.cost_usd`, labelled an **upper bound** permanently, because
attribution binds a whole session and the data holds no sub-session boundary. A
session serving two features charges the primary in full and the secondary zero.
Every figure carries its own gap counter — `no-usage-captured`,
`model-unpriced`, `zero-duration`, `traceless-runs` — and a gap is always a
count of sessions, never a substituted zero. The two the escape verdict used to
need are gone with the cached word they described.

Do not mirror one into the other. One writer per field.

## How we will know it worked

**The metric is rounds-to-done per feature.** This design exists to produce it;
every other output is instrumentation.

State the bar before building: the pipeline is worth keeping only if
rounds-to-done falls on features that go through it, against features that do
not. Run it as a real comparison — `lib/experiments.py` exists for this — and
accept the answer.

This commitment is not decoration. A prior measurement in this repo found that
deliberate memory consultation produced no success lift and cost roughly 40%
more turns, and the loop was extended anyway. A system that only adds is not
improving, it is accumulating; the difference is whether a measurement is ever
allowed to remove something. If rounds-to-done does not move, delete this rather
than tune it.

## Limits

- Acceptance-as-tests works only where the behaviour is testable. Where it is
  not, record the item as a known gap rather than pretending prose is a gate.
- Only *declared* defects are counted. Work fixed without anyone opening a
  record stays invisible, and the count lower-bounds the problem. That is the
  deliberate trade: a smaller number that means something beats a larger one
  assembled by guesswork.
- Reading the history needs tracker access. Where none can answer the result
  is *unknown*, never a number: `1` is what a real never-reopened feature
  reads, so it must not double as "could not look". A feature tracked in regin
  itself is the exception in one direction only — its history is a column on
  its own row, so the read cannot be unreachable and its `1` is always the
  first reading.
