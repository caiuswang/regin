"""Unit tests for lib.patterns.secret_scan.

Tokens are built by concatenation at runtime to keep the test file
itself from tripping the secret-scanner pre-commit hook that scans
tests/ on every commit.
"""

from __future__ import annotations

from lib.patterns.secret_scan import (
    ALLOWED, PATTERNS, scan_file, scan_text, should_scan,
)


# Build example tokens at runtime — concatenate a non-matching prefix
# with a hex suffix so the secret-scan hook that runs on this very
# file doesn't fire.
_OPENAI_LIKE = "s" + "k-" + "abcdefghij1234567890ABCDEF"
_GITHUB_PAT_LIKE = "g" + "hp_" + "A" * 36
_AWS_KEY_LIKE = "A" + "KIA" + "1234567890ABCDEF"
_GOOGLE_KEY_LIKE = "A" + "Iza" + "A" * 35


# ── scan_text ────────────────────────────────────────────────

def test_scan_text_detects_openai_style_key():
    hits = scan_text(_OPENAI_LIKE)
    assert len(hits) == 1
    label, _preview, line_no = hits[0]
    assert "API key" in label
    assert line_no == 1


def test_scan_text_detects_github_pat():
    hits = scan_text(_GITHUB_PAT_LIKE)
    labels = [h[0] for h in hits]
    assert any("GitHub" in lbl for lbl in labels)


def test_scan_text_detects_aws_access_key():
    hits = scan_text(_AWS_KEY_LIKE)
    assert any("AWS" in lbl for lbl, _, _ in hits)


def test_scan_text_detects_google_api_key():
    hits = scan_text(_GOOGLE_KEY_LIKE)
    assert any("Google" in lbl for lbl, _, _ in hits)


def test_scan_text_multiline_line_number():
    content = "\n\n" + _OPENAI_LIKE
    hits = scan_text(content)
    assert len(hits) == 1
    assert hits[0][2] == 3  # three-line offset


def test_scan_text_clean_returns_empty():
    assert scan_text("nothing to see here") == []


def test_scan_text_allows_redacted_placeholder():
    # "sk-REDACTED" is in ALLOWED but the pattern requires 20+ chars —
    # since it doesn't match anyway, confirm the fact.
    assert "sk-REDACTED" in ALLOWED
    assert scan_text("sk-REDACTED") == []


# ── should_scan ──────────────────────────────────────────────

def test_should_scan_skips_node_modules():
    assert should_scan("frontend/node_modules/foo/bar.js") is False


def test_should_scan_skips_dist():
    assert should_scan("build/bundle.js") is False


def test_should_scan_skips_images():
    assert should_scan("assets/logo.png") is False
    assert should_scan("doc.pdf") is False


def test_should_scan_skips_lockfiles():
    assert should_scan("package-lock.json") is False
    assert should_scan("yarn.lock") is False


def test_should_scan_accepts_source_files():
    assert should_scan("lib/auth.py") is True
    assert should_scan("frontend/src/App.vue") is True


# ── scan_file ────────────────────────────────────────────────

def test_scan_file_reads_and_scans(tmp_path):
    f = tmp_path / "fake.py"
    f.write_text(_OPENAI_LIKE)
    hits = scan_file(str(f))
    assert len(hits) == 1


def test_scan_file_skipped_for_excluded_ext(tmp_path):
    f = tmp_path / "image.png"
    f.write_text(_OPENAI_LIKE)
    # Even if the content matches, .png is in SKIP_EXTS.
    assert scan_file(str(f)) == []


def test_scan_file_missing_path_returns_empty():
    assert scan_file("/nonexistent/path/file.py") == []


# ── PATTERNS constant ────────────────────────────────────────

def test_pattern_set_has_expected_categories():
    labels = [label for _pat, label in PATTERNS]
    # These are load-bearing for the pre-commit hook.
    assert any("OpenAI" in lbl for lbl in labels)
    assert any("JWT" in lbl for lbl in labels)
    assert any("GitHub" in lbl for lbl in labels)
    assert any("AWS" in lbl for lbl in labels)
