#!/usr/bin/env python3
"""Find files in a repo that match a rule's trigger conditions.

Usage: find_applicable_files.py <rules-json> <repo-path> <rule-id>

Prints matching file paths (one per line) to stdout.
Exit 0 if files found, exit 1 if none.

Uses the same trigger matching logic as grit_post_edit_hook.py:
  - Filename globs (contain * or end with .java): file must match at least one
  - Content triggers (everything else): file must contain at least one
  - AND across kinds: both must pass if both are declared
"""

import fnmatch
import json
import os
import re
import sys


# Standalone script — runs inside the deployed grit-rules skill without
# access to regin's `lib.languages` registry. Extensions here must stay
# in sync with it; today "java" is the only language that can ship rules.
_EXTENSIONS_BY_LANGUAGE = {
    'java': ('.java',),
}
_DEFAULT_EXTENSIONS = _EXTENSIONS_BY_LANGUAGE['java']


def _extensions_for_rule(rule: dict) -> tuple:
    language = rule.get('language', 'java')
    return _EXTENSIONS_BY_LANGUAGE.get(language, _DEFAULT_EXTENSIONS)


def _content_trigger_matches(trig: str, file_content: str) -> bool:
    if trig.startswith('@'):
        return trig in file_content
    pattern = r'\b' + re.escape(trig) + r'\b'
    return re.search(pattern, file_content) is not None


def _rule_applies(rule: dict, file_path: str, file_content: str) -> bool:
    basename = os.path.basename(file_path)
    extensions = _extensions_for_rule(rule)

    filename_globs = []
    content_triggers = []
    for trig in rule.get('triggers', []):
        if '*' in trig or any(trig.endswith(ext) for ext in extensions):
            filename_globs.append(trig)
        else:
            content_triggers.append(trig)

    if filename_globs:
        if not any(fnmatch.fnmatch(basename, g) for g in filename_globs):
            return False

    if content_triggers:
        if not any(_content_trigger_matches(t, file_content) for t in content_triggers):
            return False

    return bool(filename_globs or content_triggers)


def main():
    if len(sys.argv) != 4:
        print(f"Usage: {sys.argv[0]} <rules-json> <repo-path> <rule-id>", file=sys.stderr)
        sys.exit(2)

    rules_json_path = sys.argv[1]
    repo_path = sys.argv[2]
    rule_id = sys.argv[3]

    with open(rules_json_path) as f:
        data = json.load(f)

    rule = None
    for r in data.get('rules', []):
        if r['id'] == rule_id:
            rule = r
            break

    if not rule:
        print(f"Rule '{rule_id}' not found", file=sys.stderr)
        sys.exit(2)

    extensions = _extensions_for_rule(rule)
    found = 0
    for dirpath, _dirnames, filenames in os.walk(repo_path):
        # Skip hidden dirs and build output
        _dirnames[:] = [d for d in _dirnames if not d.startswith('.') and d not in ('target', 'build', 'node_modules')]
        for fname in filenames:
            if not any(fname.endswith(ext) for ext in extensions):
                continue
            fpath = os.path.join(dirpath, fname)
            try:
                with open(fpath, 'r', errors='ignore') as fh:
                    content = fh.read()
            except (OSError, IOError):
                continue
            if _rule_applies(rule, fpath, content):
                # Print relative to repo_path
                print(os.path.relpath(fpath, repo_path))
                found += 1

    sys.exit(0 if found > 0 else 1)


if __name__ == '__main__':
    main()
