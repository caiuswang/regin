"""Characterization of the per-ref helpers in lib.topics.scan:
`_validate_refs` (errors / warnings / absent paths) and `_under_dir_refs`
(folding glob matches into curated directory refs)."""

from __future__ import annotations

from lib.topics.scan import _under_dir_refs, _validate_refs


def test_validate_refs_reports_every_ref_problem(tmp_path):
    (tmp_path / "a.py").write_text("")
    topic = {"refs": [
        "str",
        {"path": "a.py", "role": "bogus", "tier": "weird"},
        {"path": "a.py"},
        {"role": "tests"},
        {"path": ""},
        {"path": "gone.py", "tier": "reference"},
    ]}
    errors: list[str] = []
    warnings: list[str] = []
    absent: list[tuple[str, str]] = []

    _validate_refs(tmp_path, "t", topic, errors, warnings, absent)

    assert (errors, warnings, absent) == (
        [
            "topic t refs must be objects",
            "topic t ref 'a.py' has invalid role 'bogus'",
            "topic t ref 'a.py' has invalid tier 'weird'",
            "topic t ref None has invalid role 'tests'",
            "topic t has ref with missing path",
            "topic t has ref with missing path",
        ],
        ["topic t has duplicate ref a.py"],
        [("t", "gone.py")],
    )


def test_under_dir_refs_cases():
    cases = [
        (([{"path": "x.py"}], {"refs": [{"path": "x.py"}]}),
         [{"path": "x.py"}]),
        (([{"path": "s/a.json"}, {"path": "s/b.json"}, {"path": "o.py"},
           {"path": "s/"}],
          {"refs": [{"path": "s/"}, {"path": "s/b.json"}, "junk", {"path": "d/"}]}),
         [{"path": "s/b.json"}, {"path": "o.py"}, {"path": "s/"}, {"path": "d/"}]),
        (([], {"refs": None}), []),
    ]
    got = [_under_dir_refs(*args) for args, _ in cases]
    assert got == [want for _, want in cases]
