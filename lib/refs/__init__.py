"""@-references in spec and task bodies (feature #131).

A reference is a markdown link whose href is `<kind>:<key>` —
`[F128 Film](feature:128)` — and it exists only as that text in the body that
holds it. No table stores one: every reader derives them from the body.

  * `grammar.py` — what a reference is, how one is written, file keys
  * `search.py`  — the `@` menu's one grouped search over the six kinds
  * `resolve.py` — a batch of references to their live items
  * `backlinks.py` — the features and tasks whose body cites a feature
"""
