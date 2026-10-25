The sessions list (/trace/sessions) must not give one top-level row to every
non-interactive agent run. A single fan-out writes hundreds of them — the
`memory-topic-classify` batch behind `/api/memory/link-orphans` left 133
`origin='llm-stage'` rows in 22 minutes (e.g. 266cc70a-ee84-410b-9ae2-704982509daa)
— and they bury the handful of sessions a human actually opened.

Not the recent subagent display/capture work. That work (dba4e5fc, 76429fc4)
only touched the trace conversation feed. The list regression is older and
independent: 7e27d0c5 shipped this list with runs hidden by default
(`workflowFilter` default 'hide' + an "N workflow runs hidden" pivot);
dc53fb66/b93ff013 replaced that control with the builtin-tag facet, which is
positive-selection only and defaults to ''. The default went out with the
control — `useSessionListQuery.buildQuery` stopped sending `workflow=` and the
server's default is 'show'.

Server side is unchanged and complete: `workflow=hide|show|only`,
`workflow_hidden_count`, and `tag_counts` computed off the pre-origin filter
chain (so the `system` facet keeps a truthful count while its rows are hidden).
The `system` builtin category and `_RUN_ORIGINS` are the same set by
construction (`session_tags.system_origins()`).

RULES

| condition                       | `workflow=` sent | list shows                  |
|---------------------------------|------------------|-----------------------------|
| default                         | `hide`           | interactive sessions only   |
| chip toggled on (`runsShown`)   | —                | sessions AND runs, interleaved |
| any `tag=` selected             | —                | that tag governs alone      |
| `trace_id=` prefix given        | —                | deep link resolves any row  |

The chip under the active-filter row is the whole control: "⚙ N system runs
hidden →" ⇄ "⚙ Hide system runs", persisted in `regin_sessions_runs`, reset by
Reset all. It toggles rather than pivoting into `tag=system`, so a mixed list
stays expressible. With a search term live it reads "N matching system runs
hidden" — those rows are hits, and an otherwise-empty result would read as "no
matches".

INVARIANTS
- Hiding is the default, so it never counts toward `filterCount` and never
  appears in the active-filter chip row.
- `tag=system` and `workflow=hide` are never sent together (they cancel to zero).
- A trace-id lookup always resolves a run's own row.

NOT BUILT: nesting run rows under the session that launched them. An llm-stage
session records no parent — `session.start` carries only cwd/source/agent_type/
llm_surface, and `agent_runs` holds no row for it — so true nesting needs new
capture, not a read-side change.
