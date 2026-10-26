"""A scenario title's test name, derived from its words — never guessed.

The rule (spec Rules §7): exactly one whitespace-separated word that is a
`test_…` identifier is the name; two such words are ambiguous; with none,
an all-ASCII title becomes `test_<snake_case>`, and a title carrying any
non-ASCII character has no name. The last case used to drop the Chinese
words silently and keep whatever ASCII was left, which is how `test_tab`
and `test_feature` ended up as the names of whole scenarios.
"""

from __future__ import annotations

from typing import Optional

#: Punctuation allowed to hug an explicit name: `test_x`, (test_x), test_x,
_WRAPPING = "`'\"()[]{}<>,.;:，。；：（）「」"


def explicit_names(text: str) -> list[str]:
    """The distinct `test_…` identifiers among `text`'s whitespace-separated words."""
    seen: dict[str, None] = {}
    for word in text.split():
        word = word.strip(_WRAPPING)
        if word.startswith("test_") and word.isidentifier():
            seen.setdefault(word, None)
    return list(seen)


def first_word_name(title: str) -> Optional[str]:
    """A JS test title's name: its first word, when that word is `test_…`."""
    words = title.split()
    if words and words[0].startswith("test_") and words[0].isidentifier():
        return words[0]
    return None


def _ascii_words(title: str) -> list[str]:
    words: list[str] = []
    current: list[str] = []
    for char in title:
        if char.isascii() and char.isalnum():
            current.append(char.lower())
        elif current:
            words.append("".join(current))
            current = []
    if current:
        words.append("".join(current))
    return words


def derive_test_name(title: str) -> tuple[Optional[str], Optional[str]]:
    """`(test_name, why_none)` — exactly one of the two is set."""
    explicit = explicit_names(title)
    if len(explicit) == 1:
        return explicit[0], None
    if explicit:
        return None, ("the title names more than one test ("
                      + ", ".join(explicit) + ") — keep exactly one test_… word")
    if not title.isascii():
        return None, ("the title is not ASCII, so no name can be derived from "
                      "it — add an explicit test_… name to the title")
    words = _ascii_words(title)
    if not words:
        return None, "the title has no words — add an explicit test_… name"
    return "test_" + "_".join(words), None
