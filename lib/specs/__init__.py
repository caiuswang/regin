"""A spec read by parsers, not by line-matching.

Every fact this package reports — a section, a Gherkin scenario, a table
cell, a test name — comes from a parser's tokens or AST: `markdown-it-py`
for the document, `gherkin-official` for acceptance blocks, `ast` and
tree-sitter for test files. No module here imports `re`; a regular
expression over markdown is how a ```` fence nested in another, or a
`Scenario:` inside a docstring, used to be read as an acceptance item.

Kept import-free on purpose: `lib.tasks.slice_table` imports
`lib.specs.slice_rows`, and `lib.specs.reader` imports `lib.tasks.tail`,
so an `__init__` that pulled the reader in would close that loop.
"""
