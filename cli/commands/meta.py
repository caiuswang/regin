"""`regin doctor` — environment health check."""

from __future__ import annotations

import typer


OK, FAIL, WARN = '✓', '✗', '⚠'


def _item_line(item: dict, detail: str) -> str:
    """One doctor row. `status_text` lets a check say something truer than
    "missing" — an installed-but-drifted hook is present, just wrong."""
    if item['present']:
        return f"  {OK} {item['label']:<20s} {detail}"
    state = item.get('status_text', 'missing')
    if item.get('optional'):
        return f"  {WARN} {item['label']:<20s} {state} (optional)"
    return f"  {FAIL} {item['label']:<20s} {state}"


def _detail(item: dict) -> str:
    return item.get('version', '') or (f"({item['path']})" if item.get('path') else '')


def cmd_doctor() -> None:
    from lib.doctor import run_checks
    data = run_checks()
    for group in data['groups']:
        print(f"\n=== {group['name']} ===")
        for item in group['items']:
            print(_item_line(item, _detail(item)))
            if not item['present'] and item.get('install_hint'):
                print(f"    → Install: {item['install_hint']}")
    proj = data['project']
    print(f"\n=== {proj['name']} ===")
    for item in proj['items']:
        print(_item_line(item, ''))
    print("")


def register(app: typer.Typer) -> None:
    app.command("doctor", help="Check environment health and missing CLI tools")(cmd_doctor)
