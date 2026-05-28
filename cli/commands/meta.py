"""`regin doctor` — environment health check."""

from __future__ import annotations

import typer


def cmd_doctor() -> None:
    from lib.doctor import run_checks
    data = run_checks()
    OK, FAIL, WARN = '✓', '✗', '⚠'
    for group in data['groups']:
        print(f"\n=== {group['name']} ===")
        for item in group['items']:
            extra = item.get('version', '') or (f"({item['path']})" if item.get('path') else '')
            if item['present']:
                print(f"  {OK} {item['label']:<20s} {extra}")
            elif item.get('optional'):
                print(f"  {WARN} {item['label']:<20s} missing (optional)")
            else:
                print(f"  {FAIL} {item['label']:<20s} missing")
            if not item['present'] and item.get('install_hint'):
                print(f"    → Install: {item['install_hint']}")
    proj = data['project']
    print(f"\n=== {proj['name']} ===")
    for item in proj['items']:
        if item['present']:
            print(f"  {OK} {item['label']:<20s}")
        elif item.get('optional'):
            print(f"  {WARN} {item['label']:<20s} missing (optional)")
        else:
            print(f"  {FAIL} {item['label']:<20s} missing")
    print("")


def register(app: typer.Typer) -> None:
    app.command("doctor", help="Check environment health and missing CLI tools")(cmd_doctor)
