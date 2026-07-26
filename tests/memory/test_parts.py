"""Lead/part splitting — the contract `recall`'s brief mode depends on."""

from lib.memory import parts


def test_single_block_body_is_all_lead():
    body = "One self-contained paragraph that says the whole thing plainly."
    lead, rest = parts.split_lead(body)
    assert lead == body
    assert rest == ""


def test_lead_ending_in_colon_absorbs_the_next_block():
    """The `f3400a33` shape: a lead that trails into a list is useless alone."""
    body = (
        "**Correction to earlier cache advice.** Audited the UserPromptSubmit "
        "hook chain end to end, across every injector it runs. Two injectors "
        "actually emit anything:\n\n"
        "- memory_recall.py, hard-capped at 2000 chars\n"
        "- prompt_trace.py, a few bytes on a rare signal\n\n"
        "So the per-prompt injection is small and cannot be the cause of the "
        "history rewrites seen at the prompt boundary in this session.")
    lead, rest = parts.split_lead(body)
    assert lead.endswith("a few bytes on a rare signal"), \
        "a lead trailing into a list must absorb the list"
    assert "cannot be the cause" in rest


def test_short_lead_absorbs_rather_than_emitting_a_stub():
    body = "Too short.\n\n" + "x" * 300
    lead, _ = parts.split_lead(body)
    assert len(lead) > parts.LEAD_MIN_CHARS


def test_lead_never_ends_mid_sentence():
    body = ("First sentence here. " * 60).strip()
    lead, rest = parts.split_lead(body)
    assert len(lead) <= parts.LEAD_MAX_CHARS + 40
    assert lead.endswith(".")
    assert rest


def test_unpunctuated_wall_is_returned_whole_not_hard_cut():
    """No sentence boundary => returning it oversized beats cutting mid-word."""
    body = "word " * 400
    lead, rest = parts.split_lead(body)
    assert rest == ""
    assert lead.startswith("word")


def test_named_parts_finds_bold_labels_and_headings():
    body = ("Lead paragraph.\n\n**Why:** because of the thing.\n\n"
            "## How to apply\nDo the thing.")
    names = [name for name, _ in parts.named_parts(body)]
    assert "Why" in names
    assert "How to apply" in names


def test_flat_prose_has_no_named_parts():
    assert parts.named_parts("Just prose.\n\nMore prose. No labels.") == []


def test_find_part_matches_by_prefix_case_insensitively():
    body = "Lead.\n\n**How to apply:** run the command."
    assert "run the command" in parts.find_part(body, "how")
    assert parts.find_part(body, "nonexistent") is None
