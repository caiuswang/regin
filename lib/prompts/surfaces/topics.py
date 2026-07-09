"""Stage 2 surface registrations for regin's topic-taxonomy agent prompts.

The two taxonomy-restructuring prompts in ``lib/topics/`` — the leaf *split*
proposer (`split_leaf.py`) and the top-level *group* proposer
(`group_topics.py`) — are migrated here into ``{{var}}``-placeholder default
bodies, registered as editable *surfaces*, and their call sites rewired to
``render_surface``.

Single- vs double-brace tokens — IMPORTANT
------------------------------------------
The originals were ``str.format()`` templates, so their dynamic slots were
single-brace (``{label}``, ``{lo}``, ``{topics}``) and the literal JSON braces
in the ``<output_format>`` example were **escaped** as ``{{ … }}`` (which
``.format`` collapses to a single brace). The prompt engine treats ONLY
``{{ … }}`` as a slot, so here the mapping inverts:

  - the old single-brace slots  ``{label}``   → engine ``{{label}}``;
  - the old escaped JSON braces  ``{{"label"}}`` → engine ``{"label"}`` (a plain
    single brace that passes through untouched).

Each body renders byte-identical to the old ``_PROMPT.format(...)`` output;
characterization tests in ``tests/prompts/`` assert this against a frozen copy
of the original ``.format`` template. Edit a body here and its parity test
together.
"""

from __future__ import annotations

from lib.prompts.registry import PromptVariable, register_surface

SPLIT_LEAF_SURFACE_ID = "topic-split-leaf"
GROUP_BUCKETS_SURFACE_ID = "topic-group-buckets"


# --- Leaf split proposer (lib/topics/split_leaf.py::propose_clusters) --------
_DEFAULT_BODY_SPLIT = """You are reorganizing one over-large topic in a repo's knowledge base.

The topic "{{label}}" has accumulated too many memories to navigate. Group them
into {{lo}}-{{hi}} coherent SUB-THEMES so a future reader can drill into the right
cluster. Rules:
- Each memory goes in exactly ONE sub-theme. Cover every memory id given.
- A sub-theme needs at least a few memories — don't make singletons.
- Give each a short Title-Case label and a one-line intent written as a router
  card ("drill in here when …"), not a description.
- Group by SUBJECT (what the lesson teaches), not by incidental file mentions.

<topic-intent>{{intent}}</topic-intent>

<memories>
{{memories}}
</memories>

<output_format>
Respond with ONLY a JSON array:
  [{"label": "...", "intent": "...", "memory_ids": ["<id>", ...]}, ...]
</output_format>
"""


# --- Top-level group proposer (lib/topics/group_topics.py::propose_buckets) --
_DEFAULT_BODY_GROUP = """You are organizing a repo's knowledge base whose topics are all
sitting at the top level, unbucketed. Group them into {{lo}}-{{hi}} coherent
top-level BUCKETS so a future reader can drill into the right area. Rules:
- Each topic goes in exactly ONE bucket. Cover every topic id given.
- Give each bucket a short Title-Case label and a one-line intent written as a
  router card ("drill in here when …"), not a description.
- Group by SUBJECT (what the topic is about), not by incidental overlaps.

<topics>
{{topics}}
</topics>

<output_format>
Respond with ONLY a JSON array:
  [{"label": "...", "intent": "...", "topic_ids": ["<id>", ...]}, ...]
</output_format>
"""


register_surface(
    SPLIT_LEAF_SURFACE_ID,
    label="Topics — split over-large leaf",
    area="topics",
    default_body=_DEFAULT_BODY_SPLIT,
    description=(
        "The prompt that clusters one over-large topic leaf's memories into "
        "coherent sub-themes (`lib/topics/split_leaf.py`)."
    ),
    applies_to=("topics",),
    variables=(
        PromptVariable("label", "The over-large topic's display label."),
        PromptVariable("lo", "Lower bound on the number of sub-themes to propose."),
        PromptVariable("hi", "Upper bound on the number of sub-themes to propose."),
        PromptVariable("intent", "The topic's whitespace-collapsed intent (clipped to 400 chars)."),
        PromptVariable("memories", "The leaf's memories rendered as `<memory id=…>` blocks."),
    ),
    tags=("topics", "split-leaf"),
)

register_surface(
    GROUP_BUCKETS_SURFACE_ID,
    label="Topics — group flat topics into buckets",
    area="topics",
    default_body=_DEFAULT_BODY_GROUP,
    description=(
        "The prompt that groups unbucketed top-level topics into coherent "
        "buckets (`lib/topics/group_topics.py`)."
    ),
    applies_to=("topics",),
    variables=(
        PromptVariable("lo", "Lower bound on the number of buckets to propose."),
        PromptVariable("hi", "Upper bound on the number of buckets to propose."),
        PromptVariable("topics", "The flat topics rendered as `<topic id=…>` blocks."),
    ),
    tags=("topics", "group-buckets"),
)

__all__ = [
    "GROUP_BUCKETS_SURFACE_ID",
    "SPLIT_LEAF_SURFACE_ID",
]
