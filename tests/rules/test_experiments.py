"""Unit tests for lib.experiments.apply_conceal.

Kept pure — no SQLite or filesystem needed. The CRUD / activation
invariant is covered by the end-to-end smoke test in the implementation
flow, not by a unit test (to avoid mutating the live DB).
"""

from lib.experiments import apply_conceal


SAMPLE = """\
Intro paragraph describing the procedure.

## Disciplines
- rule one
- rule two

## Exemplar: FooController (example-service)

### Controller layer
```java
@RestController
class FooController {}
```

### Service layer
```java
class FooService {}
```

## Naming Conventions
- FooController for REST controllers

## Anti-Patterns
- Don't do X
- Don't do Y
"""


def test_conceal_single_section():
    out = apply_conceal(SAMPLE, ['## Disciplines'])
    assert '## Disciplines' not in out
    assert 'rule one' not in out
    assert '## Exemplar: FooController (example-service)' in out
    assert '## Anti-Patterns' in out


def test_conceal_multiple_sections():
    out = apply_conceal(SAMPLE, ['## Disciplines', '## Anti-Patterns'])
    assert '## Disciplines' not in out
    assert '## Anti-Patterns' not in out
    assert "Don't do X" not in out
    assert '## Exemplar: FooController (example-service)' in out
    assert '## Naming Conventions' in out


def test_conceal_preamble_untouched():
    out = apply_conceal(SAMPLE, ['## Disciplines'])
    assert 'Intro paragraph describing the procedure.' in out


def test_conceal_unknown_section_is_noop():
    out = apply_conceal(SAMPLE, ['## Does Not Exist'])
    assert out == SAMPLE


def test_conceal_empty_spec_is_noop():
    assert apply_conceal(SAMPLE, []) == SAMPLE


def test_conceal_last_section():
    out = apply_conceal(SAMPLE, ['## Anti-Patterns'])
    assert '## Anti-Patterns' not in out
    assert '## Naming Conventions' in out
    # Ensure the previous section's trailing newlines survive.
    assert 'FooController for REST controllers' in out


def test_exact_match_not_prefix():
    # '## Disc' should not match '## Disciplines' — exact match only.
    out = apply_conceal(SAMPLE, ['## Disc'])
    assert out == SAMPLE


def test_conceal_h3_only():
    # Hiding an H3 should leave the surrounding H2 header and its other
    # sub-sections intact.
    out = apply_conceal(SAMPLE, ['### Controller layer'])
    assert '### Controller layer' not in out
    assert 'FooController {}' not in out
    assert '### Service layer' in out
    assert 'FooService {}' in out
    assert '## Exemplar: FooController (example-service)' in out


def test_conceal_h2_removes_nested_h3s():
    # Hiding the H2 Exemplar should also remove both nested H3s.
    out = apply_conceal(SAMPLE, ['## Exemplar: FooController (example-service)'])
    assert '## Exemplar' not in out
    assert '### Controller layer' not in out
    assert '### Service layer' not in out
    assert '## Disciplines' in out
    assert '## Naming Conventions' in out


def test_conceal_h3_and_h2_together():
    out = apply_conceal(SAMPLE, ['### Service layer', '## Anti-Patterns'])
    assert '### Service layer' not in out
    assert 'FooService {}' not in out
    assert '### Controller layer' in out  # untouched
    assert '## Anti-Patterns' not in out
    assert "Don't do X" not in out


if __name__ == '__main__':
    test_conceal_single_section()
    test_conceal_multiple_sections()
    test_conceal_preamble_untouched()
    test_conceal_unknown_section_is_noop()
    test_conceal_empty_spec_is_noop()
    test_conceal_last_section()
    test_exact_match_not_prefix()
    test_conceal_h3_only()
    test_conceal_h2_removes_nested_h3s()
    test_conceal_h3_and_h2_together()
    print("all experiment tests passed")
