"""The topic-proposal *drift-triage* agent prompt — asks whether a topic's
changed refs are MATERIAL (warrant a re-draft) or TRIVIAL. The per-item
fallback of the batched drift judge: same evidence-pointer contract, one
topic per call, and the older single-line ``VERDICT:`` answer format."""

from __future__ import annotations

from lib.prompts.registry import (PromptVariable, register_retired_default,
                                  register_surface)

SURFACE_ID = "topic-proposal-drift-triage"

# NOTE: a characterization test (tests/prompts/test_triage_parity.py) asserts
# render_surface == a frozen reference builder. Edit the wording here and the
# reference together, and append the superseded body's sha256 to the retired
# tuple below.
_DEFAULT_BODY = """A topic's ref files changed since its wiki was written. Decide whether the change is MATERIAL (the wiki narrative is now inaccurate or incomplete and should be re-drafted) or TRIVIAL (formatting, comments, renames, or edits that don't change what the wiki says).

The evidence below is a set of pointers: the topic's current wiki path, and each changed ref with the baseline commit its digest was captured at, any wiki-cited identifiers that vanished from it, and a one-line change summary. All paths are relative to the repo root {{repo_root}}. Pull the evidence yourself before judging:
- Read the wiki file — it is the narrative you are judging.
- Run `git -C {{repo_root}} diff <baseline> -- <path>` for the real old→new change, and `git -C {{repo_root}} log --oneline <baseline>..HEAD -- <path>` for the commits (and their intent) behind it.
- Read/Glob/Grep anything else you need; do not rubber-stamp the summaries.

<topic_id>{{topic_id}}</topic_id>

<wiki>{{wiki_pointer}}</wiki>

<changed_refs>
{{changed_refs}}
</changed_refs>

<task>
Answer with exactly one line:
VERDICT: MATERIAL|TRIVIAL
</task>"""


JUDGE_BATCH_SURFACE_ID = "topic-proposal-drift-judge-batch"

_JUDGE_BATCH_DEFAULT_BODY = """Several topics' ref files changed since their wikis were written. For EACH topic below, decide whether its change is MATERIAL (that topic's wiki narrative is now inaccurate or incomplete and should be re-drafted) or TRIVIAL (formatting, comments, internal refactors, or edits that don't change what the wiki says).

Each topic block is a set of evidence pointers recorded at detection time: the path of the topic's current wiki, each changed ref with the baseline commit its digest was captured at, any wiki-cited identifiers that vanished from it, and a one-line change summary. All paths are relative to the repo root {{repo_root}}. Pull the evidence yourself before judging:
- Read the wiki file — it is the narrative you are judging.
- Run `git -C {{repo_root}} diff <baseline> -- <path>` for the real old→new change, and `git -C {{repo_root}} log --oneline <baseline>..HEAD -- <path>` for the commits (and their intent) behind it.
- Read/Glob/Grep anything else you need; do not rubber-stamp the summaries.

{{topic_blocks}}

<task>
Answer with exactly one line per topic, nothing else:
<topic_id>: MATERIAL|TRIVIAL — <one-sentence reason>
</task>"""


register_surface(
    JUDGE_BATCH_SURFACE_ID,
    label="Topic proposal — batched drift judge",
    area="topic-proposal",
    default_body=_JUDGE_BATCH_DEFAULT_BODY,
    description=(
        "One prompt judging every pending content-drift refresh in a sweep — "
        "per-topic evidence pointers (wiki path, baseline commits, vanished "
        "anchors, change summaries) the agent pulls itself — instead of one "
        "triage call per topic."
    ),
    applies_to=("external-agent",),
    variables=(
        PromptVariable("topic_blocks", "One markdown block per pending topic: the wiki path, and each changed ref with its baseline commit, vanished-anchor notes, and a one-line change summary."),
        PromptVariable("repo_root", "Absolute path of the repo under judgment — anchors the relative pointers and `git -C` commands regardless of the agent's own cwd."),
    ),
    tags=("topic-proposal-agent", "drift-triage"),
)

# Retired default-body hashes: an un-edited stale row still hashing to one of
# these is healed to the current default by `seed_builtin_skeletons`, so a
# body change reaches existing installs instead of being pinned to the stale
# seed. Append a line each time the body changes.
for _sha in (
    # embedded per-path git-diff fences + truncated wiki excerpt, before the
    # evidence-pointer form (wiki path + baseline commit + shortstat)
    "de70600633bbf90d237d6493986243d217ba7d985b3945a1a058d69c88afbb85",
    # first evidence-pointer draft: bare `git diff`/`git log` with no
    # `-C {{repo_root}}` anchor (breaks under an agent-config cwd override)
    "a6347c28ff965d02377e2da7e53ccebd32a521fa104e157411efb5c1191a1df6",
):
    register_retired_default(JUDGE_BATCH_SURFACE_ID, sha256=_sha)


register_surface(
    SURFACE_ID,
    label="Topic proposal — drift triage",
    area="topic-proposal",
    default_body=_DEFAULT_BODY,
    description=(
        "The task prompt piped to the external agent that decides whether a "
        "topic's changed refs are MATERIAL (re-draft) or TRIVIAL (skip) — "
        "the per-item fallback of the batched drift judge, judging the same "
        "evidence pointers."
    ),
    applies_to=("external-agent",),
    variables=(
        PromptVariable("topic_id", "The id of the topic whose refs drifted."),
        PromptVariable("changed_refs", "One pointer line per changed ref: path, baseline commit, vanished-anchor notes, one-line change summary."),
        PromptVariable("wiki_pointer", "Repo-relative path of the topic's wiki, or `(no wiki on file)` / `(wiki unreadable)`."),
        PromptVariable("repo_root", "Absolute path of the repo under judgment — anchors the relative pointers and `git -C` commands regardless of the agent's own cwd."),
    ),
    tags=("topic-proposal-agent", "drift-triage"),
)

for _sha in (
    # embedded full-wiki excerpt + bare path bullets, before the
    # evidence-pointer form (wiki path + baseline commit + shortstat)
    "a184d215c6a027d9288ddd6c84b7cdf55ac5d2417949179d3eac23a0de6c0ef5",
):
    register_retired_default(SURFACE_ID, sha256=_sha)

__all__ = ["SURFACE_ID", "JUDGE_BATCH_SURFACE_ID"]
