#!/usr/bin/env python3
"""Filter grit dry-run output to keep only violations from trigger-matched files.

Usage: filter_grit_output.py <rules-json> <rule-id> <repo-path> < grit_output

Reads grit output from stdin, drops violation blocks whose file does not
pass the rule's trigger check, rewrites the summary line, and prints to stdout.
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


def _content_trigger_matches(trig: str, content: str) -> bool:
    if trig.startswith('@'):
        return trig in content
    return re.search(r'\b' + re.escape(trig) + r'\b', content) is not None


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


def _split_blocks(lines: list) -> tuple:
    """Split grit output into violation blocks, each starting with
    "Log in <path>:". Returns ([(file_relpath, block_lines)], summary_line)."""
    blocks = []
    current_file = None
    current_lines = []
    summary_line = None

    for line in lines:
        m = re.match(r'^Log in (.+?):\s', line)
        if m:
            if current_file is not None:
                blocks.append((current_file, current_lines))
            current_file = m.group(1)
            current_lines = [line]
        elif re.match(r'^Processed \d+ files? and found \d+ matches?', line):
            if current_file is not None:
                blocks.append((current_file, current_lines))
                current_file = None
                current_lines = []
            summary_line = line
        else:
            current_lines.append(line)

    if current_file is not None:
        blocks.append((current_file, current_lines))
    return blocks, summary_line


def _read_file(abs_path: str) -> str:
    try:
        with open(abs_path, 'r', errors='ignore') as fh:
            return fh.read()
    except (OSError, IOError):
        return ''


def _kept_blocks(rule: dict, blocks: list, repo_path: str) -> list:
    content_cache = {}
    kept = []
    for file_path, block_lines in blocks:
        abs_path = os.path.join(repo_path, file_path)
        if file_path not in content_cache:
            content_cache[file_path] = _read_file(abs_path)
        if _rule_applies(rule, abs_path, content_cache[file_path]):
            kept.append(block_lines)
    return kept


def _print_summary(summary_line, match_count: int) -> None:
    if summary_line is not None:
        m = re.match(r'^Processed (\d+) files?', summary_line)
        file_count = m.group(1) if m else '?'
        print(f"Processed {file_count} files and found {match_count} matches")
    elif match_count == 0:
        print("Processed 0 files and found 0 matches")


def main():
    if len(sys.argv) != 4:
        print(f"Usage: {sys.argv[0]} <rules-json> <rule-id> <repo-path>", file=sys.stderr)
        sys.exit(2)

    rules_json_path, rule_id, repo_path = sys.argv[1], sys.argv[2], sys.argv[3]

    with open(rules_json_path) as f:
        data = json.load(f)

    rule = next((r for r in data.get('rules', []) if r['id'] == rule_id), None)
    if not rule:
        # No rule found — pass through unfiltered
        sys.stdout.write(sys.stdin.read())
        return

    blocks, summary_line = _split_blocks(sys.stdin.read().split('\n'))
    kept = _kept_blocks(rule, blocks, repo_path)
    for block_lines in kept:
        print('\n'.join(block_lines))
    _print_summary(summary_line, len(kept))


if __name__ == '__main__':
    main()
