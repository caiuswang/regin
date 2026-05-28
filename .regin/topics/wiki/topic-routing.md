## 2. Topic routing — approved graph + candidate fallback

The topic router answers "which curated topic in `.regin/topics/topic.json` should this agent read first?" with no ML — just structured matching over a small JSON graph.

**Storage**
- `.regin/topics/topic.json` — approved topic graph (id, label, aliases, intent, refs, edges, …). Per-machine, gitignored.
- `.regin/topics/candidates.json` — unapproved candidates surfaced by `regin topics scan`.
- `.regin/topics/wiki/<topic>.md` — optional rendered wiki pages.

**Pipeline (in `lib/topics/route.py`)**

1. `match_topic(repo, query)` tries, in order:
   - exact alias / id / label match
   - exact ref path match
   - substring match on ref paths
   - substring match on combined id+label+intent+aliases
   - multi-keyword scoring (2+ tokens, scored against the topic's identity text and ref paths, best `(id_score, ref_score)` wins)
2. `route_topic(repo, query, include_unapproved=True, max_wiki_chars=12000)` returns a `status` of:
   - `approved` — matched topic, with `refs` ordered by role (overview, architecture, entrypoint, api, schema, implementation, test) via `ordered_refs`, `wiki_pages` read by `read_wiki_pages` (bounded by `max_wiki_chars`, safe against path traversal via `path.relative_to(repo)`), and first-degree `related` topics following `edges`.
   - `candidate` — no approved match, but `candidate_matches` found unapproved candidates whose evidence paths or labels contain the query.
   - `unmatched` — nothing found.

**Deployed agent contract** — `generate_topic_router_skill()` renders the `topic-router` SKILL.md that gets installed under the active provider's skills dir. It tells agents to: pick 2–6 keywords, call `regin topics route <keywords>`, prefer `wiki_pages` content, follow only first-degree edges, and treat candidates as explicitly unapproved context.

**Entrypoints**
- CLI — `regin topics route <query>`, plus the scan/bootstrap/install-hook/router-skill subcommands in `cli/commands/topics.py`.
- Pre-commit hook (`regin topics install-hook`) keeps the candidates file fresh against staged file changes.
