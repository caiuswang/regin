# Topic routing (keyword router over the approved graph + topic-router skill)

How a freeform query resolves to approved topic context for a repo. The entry point is `route_topic` in `lib/topics/route.py`: given a query string it returns a JSON envelope with an `approved`/`unmatched` status, the matched topic's refs ordered by role, bounded wiki pages, and first-degree related topics. The matcher is a deterministic keyword router over the **authoritative graph** (the git-tracked `.regin/topics/topic.json` merged with the machine-local `.regin/topics/topic.local.json` overlay, loaded by `load_authoritative_graph`) — no embeddings, no model call. Consumed by the CLI, the web API, and the `topic-router` skill that the routing entry hooks load before a repo task.

For the proposal -> review -> apply lifecycle that *fills* this graph see [[topic-proposal-pipeline]]; for how an approved graph travels between users see [[multi-user-topic-sharing]].

## The match pipeline (`_route_match`)

`_route_match(repo_path, query)` returns `(topic_id, explanation)` where the explanation records *which* strategy fired and the keywords behind it. The query is `normalize`d (lowercased, every run of non-alphanumeric chars collapsed to a single space — `lib/topics/core.py`) into a `needle`, then four precise strategies run in priority order, each scanning every topic before the next strategy is tried:

1. **alias / label (exact)** — `needle` equals the normalized `topic_id`, `label`, or any `alias`.
2. **ref path (exact)** — `needle` equals a normalized ref path.
3. **ref path (substring)** — `needle` is a substring of a normalized ref path.
4. **label / intent (substring)** — `needle` is a substring of the joined `id + label + intent + aliases`.

The first topic any strategy matches wins, and because strategies are applied in priority order, an alias-exact match on one topic beats a ref-substring match on another. A precise match carries no per-keyword breakdown (`keywords: []`) — it matched the whole phrase.

If none of the four fire, `_fuzzy_best` provides a multi-keyword fallback for prose-shaped queries ("trace skill reads view loading").

## The keyword-signal model

The fuzzy fallback scores keyword overlap by *informativeness*, not raw hit count, so a rare domain term outweighs several common words landing by coincidence. Three layers combine:

- **English-rarity prior (`_word_weight`).** Each token is weighted `max(0, _ZIPF_CEILING - zipf_frequency(word))` using `wordfreq`. Common filler (`the`, `too`, `yes`) sits at or above the Zipf ceiling (5.5) and weighs 0; rare domain terms (`recall`, `exemplar`) and coined identifiers (`rerank`=0 frequency) score higher the rarer they are. This is self-maintaining — no hand-kept stopword list. When `wordfreq` is unavailable the code degrades to a binary `_STOPWORDS` membership test (weight 1.0/0.0), with 2-char tokens folded in so genuine short keywords (`ui`, `db`, `go`) survive.
- **Repo-adaptive multiplier (`lib/topics/term_weights.py`).** The English prior can't see that `memory`/`topics`/`current` are rare in general English yet ubiquitous in *this* repo's prompts. `repo_factor(word, n, df)` is a bounded `[floor, 1.0]` multiplier — `log((n+1)/(d+1)) / log(n+1)` — that shrinks tokens saturating the repo's own routed-prompt corpus. The per-token document frequency over routed prompts (the union of `topic_injections.query` and recall `injection_events.query`, read via the memory ORM) is cached to `.regin/topics/query_df.json`, rebuilt on the reflect sweep and via `regin topics rebuild-query-df`, and read at route time. The factor is a no-op (1.0) until the corpus reaches `settings.agent_memory.topic_route_querylog_min_queries` (default 150), with `topic_route_querylog_floor` (default 0.2) as the floor; with no cache, routing is pure wordfreq.
- **Per-graph saturation filter (`_discriminating_keywords`).** A keyword present in more than `_TOPIC_DF_CEILING` (0.34) of *all* topics' identity text can't distinguish between them — it is a stopword for this graph no matter how rare it is in English (`ui` sits in many regin topics). Such terms are dropped before scoring *and* before the `_FUZZY_MIN_HITS` count, so a prose query whose only hits are corpus-common terms routes nowhere instead of landing on a coincidence. The filter stays off below `_TOPIC_DF_MIN_CORPUS` (3) topics, where a document-frequency fraction is meaningless.

`_fuzzy_best` requires at least `_FUZZY_MIN_HITS` (2) distinct meaningful, graph-discriminating keywords, and the winner must carry that many distinct hits — one coincidental word is not topical intent. Each topic is scored on a `(identity_weight, ref_weight)` tuple so identity-text hits dominate ref-path hits, and the matched keywords are returned so callers can explain the route, not just assert it.

## The route envelope (`route_topic`)

When a topic matches, `route_topic` returns:

- `status: "approved"`, the full `topic` detail, and `refs` sorted by `ordered_refs` along `ROLE_ORDER` (overview, architecture, entrypoint, api, schema, implementation, test, migration, config, docs).
- `wiki` — the on-disk wiki page paths for the topic (`.regin/topics/wiki/<slug>.md` plus a graph `index.md` when present), and `wiki_pages` — their content read with a `max_wiki_chars` budget (default 12000), each page flagged `truncated` when clipped.
- `related` — first-degree edge targets, each with its own ordered refs and wiki paths.

A miss returns `status: "unmatched"` with empty refs/wiki — the honest answer for prose with no topical word, rather than a forced coincidental match.

Two siblings of `route_topic` serve other callers: `route_explain` returns `{id, strategy, keywords}` for the topic-route playground (the basis of a route, with `id: None` when nothing matched), and `best_topic_for_text` is a high-precision reverse matcher for *long* text (a memory body, not a query). The `match_topic` strategies test `needle in haystack` and so over-link on long text; `best_topic_for_text` instead asks whether the text *contains* a topic's ref path — a specific file path being a strong "this is about that topic" marker — and returns the topic whose ref paths appear most often, requiring `min_ref_hits`.

## The topic-router skill

`generate_topic_router_skill` emits a static `topic-router` SKILL.md telling an agent to: pull 2-6 concise keywords from the request/changed files/feature name (stable nouns, not the whole sentence), route them via `regin topics route <keywords>`, read the returned `wiki_pages` first then `.regin/topics/wiki/<topic>.md` then refs in role order, retry once or twice with a narrower keyword set when a route is weak, follow only first-degree related edges by default, and treat proposal runs as unapproved context that must be flagged before use. It never promotes proposals into topics — that needs an explicit CLI/WebUI action.

## CLI surface (`cli/commands/topics.py`)

The `regin topics` Typer app exposes routing alongside the graph-maintenance commands:

- `regin topics route <query>` — print the `route_topic` JSON envelope (sorted keys).
- `regin topics route <query> --wiki` — print the routed topic's wiki markdown content-first via `_render_topic_wiki`, since the JSON envelope sorts the often-long `refs` list above `wiki_pages` and an agent piping the JSON through `head` never reaches the content. Each page is prefixed with an HTML path comment and truncated pages carry a note.
- `regin topics router-skill` — print the generated topic-router skill.
- `regin topics rebuild-query-df` — recompute `query_df.json` from the routed-prompt corpus and report the prompt count.
- `regin topics scan` — refresh refs on approved topics from the working tree (the inputs the matcher reads).

The `_repo_path` helper resolves `--repo` to an absolute repo root, accepting either a path or a registered repo name from `settings.repo_paths` so the common `--repo regin` typo still finds the right tree instead of resolving cwd-relative.