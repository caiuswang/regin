"""Size caps, truncation and diff/hunk capture for PostToolUse tool spans
(Bash, Edit, Write, MultiEdit)."""

from __future__ import annotations

import difflib


_PREVIEW_MAX = 200
# Inline caps for span attributes. Sized to safely exceed what Claude
# Code itself sends through the hook (empirically capped around 30 KB
# for tool output, ~64 KB for tool input) so truncation never fires in
# practice. The `_truncated_bytes` markers remain in the schema in case
# a future upstream change pushes data past these ceilings — but at
# today's observed traffic they record zero drops.
#
# command/diff are model-bounded (the model has to fit them in its
# output context), so we set generous ceilings rather than chase
# theoretical maxima.
_BASH_COMMAND_MAX = 256 * 1024
_BASH_STDOUT_MAX = 64 * 1024
_BASH_STDERR_MAX = 32 * 1024
_EDIT_DIFF_MAX = 256 * 1024
# Bash edit diffs (Claude Code 2.1.270+). A per-SPAN ceiling, not a routine
# cut: over 562 real occurrences the serialized object is 3 KB at p50 and
# 11 KB at p90. It exists because the conversation view reads the *shallow*
# map (`sessions.py::_shallow_map_response`), which ships span attributes
# inline rather than lazily through `/spans/<id>/content`.
_BASH_EDIT_DIFF_MAX = 128 * 1024
# Read tool result: file content the model just saw. Cap matches Bash
# stdout — the model's own context can't fit much more than this on a
# typical turn, so storing the full payload past this point is waste.
_READ_CONTENT_MAX = 64 * 1024


def _bash_preview(tool_input: dict) -> str:
    cmd = (tool_input or {}).get('command') or ''
    return cmd[:_PREVIEW_MAX] + ('…' if len(cmd) > _PREVIEW_MAX else '')


def _truncate_output(text: str, limit: int) -> tuple[str, int]:
    if not isinstance(text, str) or not text:
        return '', 0
    if len(text) <= limit:
        return text, 0
    return text[:limit], len(text) - limit


def _truncate_diff(text: str, limit: int) -> tuple[str, int]:
    if len(text) <= limit:
        return text, 0
    # Cut at the last newline before the limit so the truncated diff
    # doesn't end mid-line (which would render as a partial -/+ line).
    cut = text.rfind('\n', 0, limit)
    if cut < 0:
        cut = limit
    return text[:cut], len(text) - cut


def _compute_unified_diff(old: str, new: str) -> tuple[str, int, int]:
    """Return (diff_text, added_lines, removed_lines).

    File-header lines (`--- `/`+++ `) are stripped because the WebUI
    synthesizes its own header from `file_path` + `edit_op`. Output is
    newline-joined with no trailing newline.
    """
    old_lines = (old or '').splitlines()
    new_lines = (new or '').splitlines()
    hunk_lines: list[str] = []
    started = False
    for line in difflib.unified_diff(old_lines, new_lines, n=3, lineterm=''):
        if not started:
            if line.startswith('@@'):
                started = True
                hunk_lines.append(line)
            continue
        hunk_lines.append(line)
    diff_text = '\n'.join(hunk_lines)
    added = sum(1 for l in hunk_lines if l.startswith('+'))
    removed = sum(1 for l in hunk_lines if l.startswith('-'))
    return diff_text, added, removed


def _attach_edit_diff(attrs: dict, diff_text: str, added: int, removed: int, op: str) -> None:
    truncated, dropped = _truncate_diff(diff_text, _EDIT_DIFF_MAX)
    attrs['diff'] = truncated
    if dropped:
        attrs['diff_truncated_bytes'] = dropped
    attrs['added_lines'] = added
    attrs['removed_lines'] = removed
    attrs['edit_op'] = op


def _hunk_int(h: dict, *keys: str) -> int:
    # Claude Code emits hunk bounds in camelCase (`oldStart`); accept the
    # snake_case spelling too. `or`-style truthy fallback (0/''/None skip to
    # the next key, then default 0) is preserved deliberately — int() stays
    # here so a non-numeric value raises into the caller's per-hunk guard.
    for k in keys:
        v = h.get(k)
        if v:
            return int(v)
    return 0


def _parse_hunk(h: object) -> dict | None:
    # One structured_patch entry → normalized line-range dict, or None if it
    # is not a dict or any bound is non-numeric (drop the whole hunk, never a
    # partial). Matches the original try/except-per-hunk behavior.
    if not isinstance(h, dict):
        return None
    try:
        return {
            'old_start': _hunk_int(h, 'oldStart', 'old_start'),
            'old_lines': _hunk_int(h, 'oldLines', 'old_lines'),
            'new_start': _hunk_int(h, 'newStart', 'new_start'),
            'new_lines': _hunk_int(h, 'newLines', 'new_lines'),
        }
    except (TypeError, ValueError):
        return None


def _parse_hunks(patch: object) -> list[dict]:
    if not (isinstance(patch, list) and patch):
        return []
    hunks: list[dict] = []
    for h in patch:
        parsed = _parse_hunk(h)
        if parsed is not None:
            hunks.append(parsed)
    return hunks


def _hunk_text(h: object) -> str | None:
    """One `bashEditDiff` hunk → `@@ … @@` header + its body lines.

    `lines` arrive already in unified-diff body form ("+…"/"-…"/" …"), the
    same form `_compute_unified_diff` emits, so nothing is re-diffed here.
    Bounds go through `_hunk_int`, which accepts both spellings — required,
    because `_normalize_payload` snake-aliases only one level into
    `tool_response` and never reaches these nested keys.
    """
    if not isinstance(h, dict):
        return None
    try:
        header = '@@ -{},{} +{},{} @@'.format(
            _hunk_int(h, 'oldStart', 'old_start'),
            _hunk_int(h, 'oldLines', 'old_lines'),
            _hunk_int(h, 'newStart', 'new_start'),
            _hunk_int(h, 'newLines', 'new_lines'),
        )
    except (TypeError, ValueError):
        return None
    lines = h.get('lines')
    body = [l for l in lines if isinstance(l, str)] if isinstance(lines, list) else []
    return '\n'.join([header, *body])


def _bash_edit_path(entry: object) -> str | None:
    if not isinstance(entry, dict):
        return None
    path = entry.get('filePath') or entry.get('file_path')
    return path if isinstance(path, str) and path else None


def _bash_edit_op(entry: dict) -> str:
    if entry.get('created'):
        return 'create'
    if entry.get('deleted'):
        return 'delete'
    return 'update'


def _bash_edit_diff_text(entry: dict) -> str:
    hunks = entry.get('hunks')
    texts = [_hunk_text(h) for h in hunks] if isinstance(hunks, list) else []
    return '\n'.join([t for t in texts if t])


def _diff_line_counts(diff: str) -> tuple[int, int]:
    added = removed = 0
    for line in diff.split('\n'):
        if line.startswith('+'):
            added += 1
        elif line.startswith('-'):
            removed += 1
    return added, removed


def _bash_file_edit(entry: object, budget: int) -> tuple[dict, int] | None:
    """One `bashEditDiff.files` entry → a `bash_edits` record, or None when the
    entry is unusable — dropped whole, never partial. Returns the record plus
    the budget it consumed.

    Line counts come off the FULL diff, not the truncated one: they describe
    what the command did, which truncation must not restate.
    """
    path = _bash_edit_path(entry)
    if path is None:
        return None
    diff_full = _bash_edit_diff_text(entry)
    diff, dropped = _truncate_diff(diff_full, max(budget, 0))
    added, removed = _diff_line_counts(diff_full)
    record = {
        'file_path': path,
        'edit_op': _bash_edit_op(entry),
        'diff': diff,
        'added_lines': added,
        'removed_lines': removed,
    }
    if dropped:
        record['diff_truncated_bytes'] = dropped
    # The budget is charged the FULL attempted length, so one truncated file
    # exhausts it outright rather than leaving a sliver that admits a later,
    # smaller diff — which would render file 3 in full while file 2 was cut.
    return record, len(diff) + dropped


def _bash_file_edits(files: list) -> tuple[list[dict], int]:
    """Every usable `files` entry as a record, plus how many were unusable.
    The diff budget is spent in file order, so the files a command touched
    first are the ones that keep their text."""
    budget = _BASH_EDIT_DIFF_MAX
    edits: list[dict] = []
    dropped = 0
    for entry in files:
        built = _bash_file_edit(entry, budget)
        if built is None:
            dropped += 1
            continue
        record, spent = built
        budget -= spent
        edits.append(record)
    return edits, dropped


def _bash_edit_totals(attrs: dict, edits: list[dict]) -> None:
    attrs['bash_edits'] = edits
    for key in ('added_lines', 'removed_lines'):
        total = sum(e[key] for e in edits)
        if total:
            attrs[key] = total


def _bash_changed_files(raw: dict) -> list[str]:
    changed = raw.get('changedFiles') or raw.get('changed_files')
    if not isinstance(changed, list):
        return []
    return [p for p in changed if isinstance(p, str)]


def _attach_bash_edit_diff(attrs: dict, tool_response: dict) -> None:
    """Capture the per-file diff Claude Code (2.1.270+) stamps on a Bash call
    that mutated files. Without it a silent `sed -i` — no stdout, no stderr, a
    command short enough to stay under the preview cap — carried no evidence at
    all of what it changed.

    Nothing is ever synthesised: no file is re-read, no diff recomputed. A diff
    reconstructed after the fact would describe a later state of the file,
    which is a different claim than "this call changed these lines".

    `shared` is read by nobody on purpose — it is Claude Code's own display
    bookkeeping, not a fact about what this call changed.
    """
    raw = tool_response.get('bash_edit_diff')
    if not isinstance(raw, dict):
        return
    files = raw.get('files')
    files = files if isinstance(files, list) else []
    changed = _bash_changed_files(raw)
    if not files and not changed:
        return

    edits, dropped = _bash_file_edits(files)
    if edits:
        _bash_edit_totals(attrs, edits)
    if changed:
        attrs['bash_edit_files'] = changed
    # A dropped entry is counted in, not silently lost: `bash_edit_more` is
    # what keeps `len(bash_edits) + more == len(bash_edit_files)` true.
    more = _hunk_int(raw, 'moreFiles', 'more_files') + dropped
    if more:
        attrs['bash_edit_more'] = more
    if raw.get('unavailable') is True:
        attrs['bash_edit_unavailable'] = True


def _attach_edit_metadata(attrs: dict, tool_response: dict) -> None:
    """Pull non-diff-derivable signals off `tool_response` and stash them
    on the span. `user_modified=True` means the user hand-edited the diff
    in the Claude Code UI before applying — high-signal for trace review
    because it explains divergences between what the model proposed and
    what actually landed. `replace_all` distinguishes a single-site Edit
    from a sweep. Hunk ranges (line numbers) make it possible for the
    trace UI to say "edited lines 740–752" without re-fetching the
    file.
    """
    if not isinstance(tool_response, dict):
        return
    if tool_response.get('user_modified') is True:
        attrs['user_modified'] = True
    if tool_response.get('replace_all') is True:
        attrs['replace_all'] = True
    hunks = _parse_hunks(tool_response.get('structured_patch'))
    if hunks:
        attrs['hunks'] = hunks
