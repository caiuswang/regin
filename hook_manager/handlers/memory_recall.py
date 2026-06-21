"""Handler: UserPromptSubmit → inject recalled experience.

Routes each real user prompt through the agent-memory store and, when
something relevant surfaces, returns it as a `<recalled_experience>`
additional-context block — the same injection mechanism that delivers
`<hindsight_memories>` today, but fed from regin's own store.

Recall defaults to asking the already-warm `regin serve` process for the
full dense + rerank pull (`inject_dense_via_server`): a hook is a fresh
short-lived process, so loading the embedder per prompt is a non-starter,
but a long-lived server already holds it. The handler POSTs to
`/api/memory/recall` over loopback with a short timeout and falls back to
in-process FTS-only recall when the server is down or slow. Injection
never reinforces the surfaced memories (`reinforce=False`) — speculatively
showing a memory is not the same signal as an agent deliberately pulling
it. The one reinforcement here is deferred and earned: a memory that was
injected earlier this session and *matches again* on a later prompt is
reinforced once (`reinforce_resurfaced`), because repeated relevance
across a session is the usefulness signal speculative inject otherwise
never gets. Same-session dedup (`inject_dedup_session`) keeps that repeat
match from re-rendering the same block every turn.
"""

from __future__ import annotations

from ..core import HookPayload, HookResponse

# Prompts (or slash-command argument text) shorter than this are
# greetings/approvals ("yes", "go ahead") — FTS matches on them are noise.
_MIN_PROMPT_CHARS = 12
_ENTRY_MAX_CHARS = 400


def _age_suffix(m: dict) -> str:
    """Return a compact age string like ', 3d old' based on updated_at or
    created_at. Returns '' when the stamp is absent or unparseable so the
    inject block never breaks."""
    stamp = m.get("updated_at") or m.get("created_at")
    if not stamp:
        return ""
    try:
        from datetime import datetime
        then = datetime.fromisoformat(stamp)
        age_secs = max(0.0, (datetime.now() - then).total_seconds())
        age_hours = age_secs / 3600.0
        if age_hours < 1:
            label = "fresh"
        elif age_hours < 24:
            label = f"{int(age_hours)}h old"
        elif age_hours < 24 * 60:
            label = f"{int(age_hours / 24)}d old"
        else:
            label = f"{int(age_hours / (24 * 30))}mo old"
        return f", {label}"
    except Exception:
        return ""


def _recall_query(prompt: str) -> str:
    """The text to actually recall on. A slash command (`/cmd args…`) is
    skill machinery, not a task, so recall on the argument text the user
    typed after the command — '' when the command carries no args, which
    keeps the bare `/cmd` out of recall."""
    text = (prompt or "").strip()
    if not text.startswith("/"):
        return text
    parts = text.split(None, 1)
    return parts[1].strip() if len(parts) > 1 else ""


def _command_name(prompt: str) -> str | None:
    """The slash-command token of `prompt`, lowercased and slash-prefixed
    (e.g. '/goal'), or None when the prompt is not a slash command. Only the
    first whitespace-delimited word counts, so '/goal do x' and a bare '/goal'
    both yield '/goal'."""
    text = (prompt or "").strip()
    if not text.startswith("/"):
        return None
    return text.split(None, 1)[0].lower()


def _skip_command(prompt: str) -> bool:
    """True when `prompt` is a slash command listed in `inject_skip_commands`
    — those commands run their own recall, so the auto-inject is suppressed
    entirely. Config entries are normalised to a single leading slash and
    lowercased ('goal' and '/GOAL' both match '/goal'); matching is exact on
    the command token, never a prefix, so '/goal' never silences '/goalpost'."""
    name = _command_name(prompt)
    if not name:
        return False
    from lib.settings import settings
    skip = {"/" + c.strip().lstrip("/").lower()
            for c in settings.agent_memory.inject_skip_commands if c and c.strip()}
    return name in skip


def _eligible_prompt(payload: HookPayload) -> bool:
    text = payload.prompt or ""
    if "<task-notification>" in text:
        return False  # system-injected background-task completion
    if payload.is_workflow_subagent:
        return False
    if _skip_command(text):
        return False  # command runs its own recall; auto-inject is noise
    return len(_recall_query(text)) >= _MIN_PROMPT_CHARS


def _format_entry(hit) -> str:
    m = hit.memory
    title = f"{m['title']}: " if m.get("title") else ""
    body = m["body"]
    if len(body) > _ENTRY_MAX_CHARS:
        body = body[:_ENTRY_MAX_CHARS] + "…"
    age = _age_suffix(m)
    return f"- [{m['kind']}] {title}{body} (memory {m['id'][:8]}{age})"


def _deeper_pull_line() -> str:
    """The 'pull deeper' instruction, reflecting the active recall_mode so
    the agent is steered to the right deliberate-recall protocol."""
    from lib.settings import settings
    if settings.agent_memory.recall_mode == "subagent":
        return "by dispatching the `memory-research` subagent (its skill)."
    return "with the memory `recall` MCP tool."


def _build_block(hits, max_chars: int) -> str:
    lines = [
        "<recalled_experience>",
        "Experience recalled from regin's past sessions. It may be stale —",
        "verify against the current code before relying on it. Pull deeper",
        _deeper_pull_line(),
    ]
    body_budget = max_chars - sum(len(l) + 1 for l in lines) - len("</recalled_experience>")
    for hit in hits:
        entry = _format_entry(hit)
        if len(entry) + 1 > body_budget:
            break
        lines.append(entry)
        body_budget -= len(entry) + 1
    lines.append("</recalled_experience>")
    return "\n".join(lines)


def handle(payload: HookPayload) -> HookResponse | None:
    if not _eligible_prompt(payload):
        return None
    try:
        hits, mode, routed = _recall(payload)
    except Exception:
        return None  # memory must never block a prompt
    hits = _cap_uncalibrated(hits)
    block_hits = _apply_session_memory(payload, hits) if hits else []
    from lib.settings import settings
    cfg = settings.agent_memory
    parts = []
    if routed:
        parts.append(_build_topic_context(routed, cfg.topic_context_max_chars))
        _record_topic_injection(payload, routed, cfg)
    if block_hits:
        parts.append(_build_block(block_hits, cfg.inject_max_chars))
    if not parts:
        return None  # no topic routed and every memory was a same-session repeat
    block = "\n".join(parts)
    if cfg.trace_recall:
        try:
            _emit_recall_span(payload, block, block_hits, mode, routed)
        except Exception:
            pass  # tracing the inject must never block the inject
    return HookResponse(suppress_output=True, additional_context=block)


def _topic_trim_to_word(text: str, limit: int) -> str:
    """`text` cut to at most `limit` chars on a word boundary — never a
    mid-word stub. '' when nothing fits."""
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    return (cut[:space] if space > 0 else cut).rstrip()


def _fit_ref_lines(refs: list, budget: int) -> tuple:
    """(ref-pointer lines, count shown) — whole refs that fit in `budget`
    chars, in order. A ref is never split across the budget edge."""
    lines, used = [], 0
    for r in refs:
        role = r.get("role")
        line = f"  - {r.get('path', '')}{f' ({role})' if role else ''}"
        if used + len(line) + 1 > budget:
            break
        lines.append(line)
        used += len(line) + 1
    return lines, len(lines)


def _topic_middle_lines(routed: dict, budget: int) -> list:
    """The intent + ref-pointer lines that fit in `budget` chars. Intent
    takes up to half the budget (word-trimmed); refs fill the rest whole,
    with a `(+N more)` marker when some are dropped so the agent knows the
    list is partial."""
    if budget <= 0:
        return []
    lines: list = []
    intent = " ".join((routed.get("intent") or "").split())
    trimmed = _topic_trim_to_word(intent, budget // 2 - len("intent: "))
    if trimmed:
        lines.append(f"intent: {trimmed}")
        budget -= len(lines[-1]) + 1
    refs = [r for r in routed.get("refs", []) if isinstance(r, dict)]
    ref_lines, shown = _fit_ref_lines(refs, budget - len("refs:\n"))
    if shown < len(refs):
        # Re-fit reserving room for the `(+N more)` marker so neither it nor
        # the caller's wiki-pull hint gets tail-clipped. `len(refs)` digits
        # is a safe upper bound on the marker width.
        marker_len = len(f"  (+{len(refs)} more — see wiki)") + 1
        ref_lines, shown = _fit_ref_lines(
            refs, budget - len("refs:\n") - marker_len)
    if ref_lines:
        lines.append("refs:")
        lines.extend(ref_lines)
        if shown < len(refs):
            lines.append(f"  (+{len(refs) - shown} more — see wiki)")
    return lines


def _build_topic_context(routed: dict, max_chars: int) -> str:
    """Pointer-only `<topic_context>` block for the authoritative topic the
    prompt routed to: label + intent + ordered ref paths, plus a hint to
    pull the full wiki on demand. Deliberately omits `wiki_pages` — the
    heavy content stays opt-in via the `/topic-router` skill. Budgeted by
    `max_chars`: the topic line and the wiki-pull hint are reserved first
    (the hint is the pointer to the full content, so it must survive
    trimming, never get tail-clipped), then the intent (word-trimmed) and
    ref pointers fill what's left. The final clip is only a safety net for
    a budget too tight even for the pointer."""
    tid = routed.get("id", "")
    label = routed.get("label") or tid
    header = f"topic: {tid} — {label}"
    hint = f"Run `regin topics route {tid} --wiki` for the full guide."
    middle = _topic_middle_lines(
        routed, max_chars - len(header) - len(hint) - 2)
    body = "\n".join([header, *middle, hint])
    if len(body) > max_chars:
        body = body[:max_chars].rstrip()
    return f"<topic_context>\n{body}\n</topic_context>"


def _cap_uncalibrated(hits):
    """When no surfaced hit carries a calibrated cross-encoder confidence
    (`score_kind == 'rerank'`) — i.e. the dense server path was unavailable
    and recall fell to FTS/RRF rank order — inject at most
    `inject_fts_top_k`. A rank score (~0.02) is meaningful only for
    ordering, not relevance, so we trust the single strongest lexical match
    and not a speculative top-k. Reranked surfaces keep their full top-k."""
    if not hits:
        return hits
    from lib.settings import settings
    if any(getattr(h, "score_kind", "") == "rerank" for h in hits):
        return hits
    cap = settings.agent_memory.inject_fts_top_k
    return hits[:cap] if cap > 0 else hits


def _apply_session_memory(payload: HookPayload, hits):
    """Same-session dedup + reinforce-on-resurface. Returns the hits to
    actually render. Any failure degrades to 'inject everything' — memory
    bookkeeping must never cost a useful injection."""
    from lib.settings import settings

    cfg = settings.agent_memory
    session_id = payload.session_id
    if not session_id:
        return hits
    try:
        import lib.memory as memory
        store = memory.get_store()
        already = store.injected_memory_ids(session_id)
        # A memory injected earlier this session that matched again is
        # reinforced once — repeated relevance is the usefulness signal.
        for h in hits:
            if h.memory["id"] in already:
                store.reinforce_resurfaced(session_id, h.memory["id"])
        block_hits = ([h for h in hits if h.memory["id"] not in already]
                      if cfg.inject_dedup_session else list(hits))
        query = _recall_query(payload.prompt or "")[:2000]
        store.record_injections(
            session_id, [h.memory["id"] for h in block_hits], query=query)
        return block_hits
    except Exception:
        return hits


def _record_topic_injection(payload: HookPayload, routed: dict, cfg) -> None:
    """Persist that this prompt's `<topic_context>` banner was injected, so a
    later `InjectedRelated` grade can stamp it relevant/not and the router can
    learn to withhold a recurringly-irrelevant route. Best-effort — recording
    the inject must never cost the inject."""
    if not cfg.topic_relevance_feedback:
        return
    session_id = payload.session_id
    topic_id = routed.get("id") if routed else None
    if not session_id or not topic_id:
        return
    try:
        import lib.memory as memory
        query = _recall_query(payload.prompt or "")[:2000]
        memory.get_store().record_topic_injection(
            session_id, topic_id, query=query)
    except Exception:
        pass


def _emit_recall_span(payload: HookPayload, block: str, hits, mode: str,
                      routed: dict | None = None) -> None:
    """Record the rendered injection as a `memory.recall` span so the
    trace shows exactly what was fed to this prompt — the
    `<recalled_experience>` memories and/or the `<topic_context>` block.
    Emitted whenever anything was injected, including a topic-only route
    that recalled no memory (`hit_count` 0, `topic` set). Parentless on
    purpose: it fires on UserPromptSubmit before the `prompt-<uuid>`
    anchor exists, so the serve-time graft (plus the `memory.recall`
    lookahead in `lib/trace/projection.py`) nests it under the prompt it
    was injected into — the same treatment `turn` spans get.
    """
    from lib.hook_plugin import post_span  # type: ignore

    attributes: dict = {
        'block': block,
        'hit_count': len(hits),
        'mode': mode,
        'hits': [{
            'id': h.memory.get('id'),
            'kind': h.memory.get('kind'),
            'title': h.memory.get('title'),
            'scope': h.memory.get('scope'),
            'score': round(float(h.score), 4),
        } for h in hits],
    }
    if routed:
        attributes['topic'] = {
            'id': routed.get('id'),
            'label': routed.get('label'),
        }
    # Mirror rule_check: when the prompt fired inside a subagent, carry
    # the agent_id so the projection's subagent pass nests this under the
    # right `subagent.start` instead of floating in the main thread.
    raw = payload.raw or {}
    agent_id = raw.get('agent_id')
    if agent_id:
        attributes['agent_id'] = agent_id
        agent_type = raw.get('agent_type')
        if agent_type:
            attributes['agent_type'] = agent_type
    post_span(trace_id=payload.session_id, name='memory.recall',
              attributes=attributes)


def _recall(payload: HookPayload):
    """Returns `(hits, mode, routed)` — `routed` is the authoritative topic
    the prompt keyword-matched (a `match_topic` detail dict) or None."""
    from lib.memory.scoping import resolve_recall_scope
    from lib.settings import settings

    cfg = settings.agent_memory
    if not (cfg.enabled and cfg.auto_inject):
        return [], "fts", None
    query = _recall_query(payload.prompt or "")[:2000]
    scope = resolve_recall_scope(payload.cwd)
    routed = _route_topic(query, payload.cwd, cfg)
    node_id = routed.get("id") if routed else None
    if cfg.inject_dense_via_server:
        hits, suppress = _recall_via_server(query, scope, cfg, node_id)
        if hits is not None:  # server authoritative even if empty
            if suppress:
                routed = None  # query-local topic suppression (negatives)
            return hits, "server", routed
    return _recall_fts(query, scope, cfg, node_id), "fts", routed


def _route_topic(query: str, cwd, cfg):
    """Keyword-route the prompt through the authoritative topic graph, or
    None when disabled / no match. `match_topic` is built for short queries,
    so the prompt text is the right input here (unlike the long-text
    `best_topic_for_text` used by the backfill). Never raises — a routing
    failure must not cost the recall."""
    if not cfg.topic_route_inject or not query:
        return None
    try:
        from pathlib import Path
        from lib.settings import settings
        from lib.topics.route import match_topic
        repo = Path(cwd).resolve() if cwd else Path(
            settings.project_root).resolve()
        routed = match_topic(repo, query)
        if routed and _topic_suppressed(routed.get("id"), cfg):
            return None
        return routed
    except Exception:
        return None


def _topic_suppressed(topic_id, cfg) -> bool:
    """True only when a human has *approved* suppression for this topic. The
    fail-rate threshold merely proposes (surfaced in the feedback summary as
    `proposed`); it never withholds a route on its own — the human gate is the
    same precision-first `proposed → approved` contract every memory write
    goes through. Best-effort: any failure (memory off, DB hiccup) returns
    False so a feedback fault never costs a useful route."""
    if not topic_id or not cfg.topic_relevance_feedback:
        return False
    try:
        import lib.memory as memory
        return memory.get_store().topic_decision(topic_id) == "suppressed"
    except Exception:
        return False


def _recall_fts(query: str, scope, cfg, boost_topic_node_id=None):
    """In-process lexical recall — the always-available fallback path.
    Never touches the dense models (`mode='fts'`)."""
    import lib.memory as memory
    return memory.recall(query, top_k=cfg.inject_top_k, scope=scope,
                         mode="fts", reinforce=False,
                         min_overlap=cfg.inject_min_overlap,
                         boost_topic_node_id=boost_topic_node_id)


def _recall_via_server(query: str, scope, cfg,
                       boost_topic_node_id=None):
    """Ask the warm `regin serve` process for dense + rerank recall.

    Returns `(hits, topic_suppress)`: hits is a list on success (possibly
    empty — a live server that found nothing is authoritative, so we do *not*
    fall back to noisier FTS), or None to signal 'server unavailable, use FTS'.
    `topic_suppress` is the server's query-local verdict on whether to withhold
    the routed topic banner (the warm embedder lives there, not in this hook)."""
    if not query:
        return [], False
    import json
    import urllib.request

    body = json.dumps({
        "query": query,
        "top_k": cfg.inject_top_k,
        "scope": scope,
        "mode": "auto",
        "min_overlap": cfg.inject_min_overlap,
        "boost_topic_node_id": boost_topic_node_id,
        "route_topic_id": boost_topic_node_id,
    }).encode()
    url = cfg.inject_server_url.rstrip("/") + "/api/memory/recall"
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(
                req, timeout=cfg.inject_server_timeout_seconds) as resp:
            if resp.status != 200:
                return None, False
            data = json.loads(resp.read().decode())
    except Exception:
        return None, False
    return (_hits_from_server(data.get("hits", []), cfg),
            bool(data.get("topic_suppress")))


# Top-1 dominance rescue, calibrated on the live store (2026-06): the
# cross-encoder's *absolute* scores on exact matches span 0.09-1.4, so no
# single threshold separates relevant from tangential. Its *relative*
# behavior does: a true match dominates the runner-up (observed 3.4-6.6x)
# while tangential tops are near-ties (~1.0-1.1x). When the absolute gate
# would silence everything, the top hit alone is rescued if it clears a
# low floor and dominates the runner-up.
_RESCUE_FLOOR = 0.05
_RESCUE_DOMINANCE = 2.0


def _rescue_dominant_top(reranked):
    """The [top hit] when it clears the rescue floor and dominates the
    runner-up (or is the only candidate); else []."""
    if not reranked or reranked[0].score < _RESCUE_FLOOR:
        return []
    if len(reranked) == 1 or reranked[0].score >= _RESCUE_DOMINANCE * reranked[1].score:
        return [reranked[0]]
    return []


def _hits_from_server(raw_hits, cfg):
    """Reshape the endpoint's flat `{**memory, score, score_kind}` rows into
    MemoryHit objects, dropping reranked hits below the confidence gate
    (`recall_min_score` gates reranked surfaces; FTS/RRF rows are rank-only
    and pass through, already top-k bounded server-side). When the gate
    silences every reranked hit, a clearly-dominant top hit is rescued —
    see `_rescue_dominant_top`."""
    from lib.memory.models import MemoryHit

    out, reranked = [], []
    for d in raw_hits:
        score = float(d.get("score") or 0.0)
        score_kind = d.get("score_kind") or "rerank"
        mem = {k: v for k, v in d.items()
               if k not in ("score", "score_kind")}
        hit = MemoryHit(memory=mem, score=score, score_kind=score_kind)
        if score_kind == "rerank":
            reranked.append(hit)
            if score >= cfg.recall_min_score:
                out.append(hit)
        else:
            out.append(hit)
    if not out:
        return _rescue_dominant_top(reranked)
    return out
