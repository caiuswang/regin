#!/usr/bin/env python3
"""Agent-bridge MVP spike: HTTP POST -> tmux send-keys into a live claude session.

Standalone feasibility probe for the agent-bridge design
(docs/agent-bridge-design.md). No regin imports; stdlib only. It proves the
delivery leg the design depends on:

  POST /send {"pane": ..., "text": ...}  ->  guarded keystrokes in that pane

Guards (each is a security control, not just hygiene — see design doc):
  * pane must exist and its foreground process must be claude (else a
    "message" typed at a shell prompt would EXECUTE as a shell command)
  * copy-mode is cancelled first (copy-mode eats keystrokes)
  * text is sanitized to printable single-line characters (no control
    bytes, no ANSI escapes) and sent with `send-keys -l` (literal mode)
  * after typing, the composer is capture-pane'd to verify the text
    actually landed before Enter is sent (the delivery "ack")

Self-test (launches a real `claude --model=Haiku` in tmux, ~3 tiny prompts):

    .venv/bin/python scripts/bridge_mvp.py --selftest

Server-only mode against an existing pane:

    BRIDGE_TOKEN=secret .venv/bin/python scripts/bridge_mvp.py --serve
    curl -sX POST http://127.0.0.1:8377/send \
         -H "Authorization: Bearer secret" \
         -H "Content-Type: application/json" \
         -d '{"pane": "mysession", "text": "status update please"}'
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BIND = ("127.0.0.1", 8377)
# Foreground process names accepted as "a claude session lives here".
# Observed empirically: the native build reports 'claude.exe' (even on
# macOS); NVM-managed installs report 'claude' or 'node'. The self-test
# prints the observed value for the design doc.
ALLOWED_COMMANDS = {"claude", "claude.exe", "node"}
MAX_TEXT_LEN = 4000
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b.")
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")


# --------------------------------------------------------------------- tmux
def _tmux(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["tmux", *args], capture_output=True, text=True)


def pane_state(pane: str) -> dict | None:
    """Foreground command + copy-mode flag for a pane, or None if absent."""
    r = _tmux(
        "display-message", "-p", "-t", pane,
        "#{pane_current_command}\t#{pane_in_mode}",
    )
    if r.returncode != 0:
        return None
    cmd, _, in_mode = r.stdout.strip().partition("\t")
    return {"command": cmd, "in_mode": in_mode == "1"}


def capture_pane(pane: str, lines: int = 40) -> str:
    return _tmux("capture-pane", "-pt", pane, "-S", f"-{lines}").stdout


def sanitize(text: str) -> str:
    """Printable single-line text only: no ANSI, no control bytes."""
    text = _ANSI_RE.sub("", text)
    text = text.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    text = _CTRL_RE.sub("", text)
    return text[:MAX_TEXT_LEN].strip()


def deliver(pane: str, raw_text: str) -> tuple[bool, str]:
    """Guarded delivery. Returns (delivered, detail)."""
    text = sanitize(raw_text)
    if not text:
        return False, "empty message after sanitization"

    state = pane_state(pane)
    if state is None:
        return False, f"pane {pane!r} not found"
    if state["command"] not in ALLOWED_COMMANDS:
        # THE critical guard: typing into a bare shell would execute.
        return False, f"refused: pane runs {state['command']!r}, not claude"
    if state["in_mode"]:
        _tmux("send-keys", "-t", pane, "-X", "cancel")
        time.sleep(0.1)

    r = _tmux("send-keys", "-l", "-t", pane, "--", text)
    if r.returncode != 0:
        return False, f"send-keys failed: {r.stderr.strip()}"

    # Ack: the typed text must be visible in the composer before we submit.
    time.sleep(0.3)
    probe = text[:30]
    if probe not in capture_pane(pane):
        return False, "typed text not visible in pane; not submitting"

    _tmux("send-keys", "-t", pane, "Enter")
    return True, f"delivered to {pane} (command={state['command']})"


# ------------------------------------------------------------- HTTP server
def make_handler(token: str) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:  # keep selftest output clean
            pass

        def _reply(self, code: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self) -> None:
            if self.path != "/send":
                self._reply(404, {"error": "unknown path"})
                return
            auth = self.headers.get("Authorization", "")
            if not secrets.compare_digest(auth, f"Bearer {token}"):
                self._reply(401, {"error": "bad token"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length))
                pane, text = payload["pane"], payload["text"]
            except (ValueError, KeyError) as exc:
                self._reply(400, {"error": f"bad payload: {exc}"})
                return
            ok, detail = deliver(pane, text)
            self._reply(200 if ok else 409, {"delivered": ok, "detail": detail})

    return Handler


def start_server(token: str) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(BIND, make_handler(token))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


# ---------------------------------------------------------------- selftest
PROMPT_NEEDLE = "❯"
TRUST_NEEDLE = "Is this a project you created or one you trust?"
BUSY_NEEDLE = "esc to interrupt"


def launch_claude(session: str, workdir: Path) -> None:
    claude_bin = shutil.which("claude")
    if claude_bin is None or shutil.which("tmux") is None:
        raise RuntimeError("selftest needs both `claude` and `tmux` on PATH")
    r = _tmux(
        "new-session", "-d", "-s", session, "-x", "200", "-y", "50",
        "-c", str(workdir),
    )
    if r.returncode != 0:
        raise RuntimeError(f"tmux new-session failed: {r.stderr.strip()}")
    # env -u: launching nested from inside a Claude session otherwise makes
    # the child transcript non-persistent (recorded lesson). Absolute claude
    # path: the tmux server's login shell may lack the NVM bin dir on PATH.
    cmd = (
        "env -u CLAUDE_CODE_SESSION_ID -u CLAUDE_CODE_CHILD_SESSION "
        f"REGIN_TRACE_TEST=1 {claude_bin} "
        "--permission-mode=bypassPermissions --model=Haiku"
    )
    _tmux("send-keys", "-l", "-t", session, "--", cmd)
    _tmux("send-keys", "-t", session, "Enter")
    wait_ready(session)


def wait_ready(session: str, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    trust_dismissed = False
    while time.monotonic() < deadline:
        pane = capture_pane(session, lines=60)
        if not trust_dismissed and TRUST_NEEDLE in pane:
            _tmux("send-keys", "-t", session, "Enter")
            trust_dismissed = True
            time.sleep(0.5)
            continue
        if PROMPT_NEEDLE in pane:
            time.sleep(0.5)
            return
        time.sleep(0.4)
    raise RuntimeError(
        f"claude prompt did not appear in {timeout}s; pane:\n"
        + capture_pane(session)
    )


def post_message(token: str, pane: str, text: str) -> dict:
    req = urllib.request.Request(
        f"http://{BIND[0]}:{BIND[1]}/send",
        data=json.dumps({"pane": pane, "text": text}).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:  # 401/409 still carry JSON bodies
        return json.loads(exc.read())


def poll_file(path: Path, needle: str, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists() and needle in path.read_text(errors="replace"):
            return True
        time.sleep(1.0)
    return False


def teardown(session: str) -> None:
    _tmux("send-keys", "-l", "-t", session, "/exit")
    time.sleep(0.2)
    _tmux("send-keys", "-t", session, "Enter")
    time.sleep(1.0)
    _tmux("send-keys", "-t", session, "C-c")
    _tmux("kill-session", "-t", session)


def _test_idle_injection(token: str, session: str, workdir: Path) -> bool:
    """T1: message posted while the agent idles at the prompt."""
    reply = post_message(
        token, session,
        "Create a file named bridge_ok.txt in the current directory "
        "containing exactly BRIDGE-OK and nothing else. Do nothing else.",
    )
    print(f"  T1 POST -> {reply}")
    ok = reply["delivered"] and poll_file(workdir / "bridge_ok.txt", "BRIDGE-OK", 120)
    print(f"  T1 idle injection: {'PASS' if ok else 'FAIL'}")
    return ok


def _test_busy_steering(token: str, session: str, workdir: Path) -> bool:
    """T2: message posted while the agent is mid-turn (steering queue)."""
    first = post_message(
        token, session,
        "Use Bash to run the command `sleep 12`, then create a file "
        "busy_done.txt containing DONE.",
    )
    print(f"  T2 task POST -> {first}")
    time.sleep(5.0)  # let the sleep start so the session is genuinely busy
    was_busy = BUSY_NEEDLE in capture_pane(session)
    steer = post_message(
        token, session,
        "Additional request: also create a file steer_ok.txt containing "
        "exactly STEER-OK.",
    )
    print(f"  T2 steer POST (busy={was_busy}) -> {steer}")
    ok = (
        steer["delivered"]
        and poll_file(workdir / "busy_done.txt", "DONE", 150)
        and poll_file(workdir / "steer_ok.txt", "STEER-OK", 150)
    )
    print(f"  T2 busy steering: {'PASS' if ok else 'FAIL'} (was_busy={was_busy})")
    return ok


def _test_shell_refusal(token: str) -> bool:
    """T3: a pane running a plain shell must be refused, not typed into."""
    shell_session = "bridge-mvp-shell"
    _tmux("new-session", "-d", "-s", shell_session, "-x", "80", "-y", "20")
    try:
        time.sleep(0.5)
        reply = post_message(token, shell_session, "echo pwned > /tmp/pwned")
        print(f"  T3 POST -> {reply}")
        ok = not reply["delivered"] and "refused" in reply["detail"]
        print(f"  T3 shell refusal: {'PASS' if ok else 'FAIL'}")
        return ok
    finally:
        _tmux("kill-session", "-t", shell_session)


def run_selftest() -> int:
    session = f"bridge-mvp-{os.getpid()}"
    workdir = Path(tempfile.mkdtemp(prefix="bridge_mvp_"))
    token = secrets.token_hex(16)
    server = start_server(token)
    print(f"[selftest] workdir={workdir} session={session}")
    try:
        launch_claude(session, workdir)
        state = pane_state(session)
        print(f"[selftest] claude ready; pane_current_command={state['command']!r}")
        results = [
            _test_idle_injection(token, session, workdir),
            _test_busy_steering(token, session, workdir),
            _test_shell_refusal(token),
        ]
    finally:
        teardown(session)
        server.shutdown()
    print(f"[selftest] {'ALL PASS' if all(results) else 'FAILURES PRESENT'}")
    return 0 if all(results) else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--selftest", action="store_true",
                      help="launch a real claude in tmux and prove delivery")
    mode.add_argument("--serve", action="store_true",
                      help="serve /send using $BRIDGE_TOKEN")
    args = ap.parse_args()
    if args.selftest:
        return run_selftest()
    token = os.environ.get("BRIDGE_TOKEN") or secrets.token_hex(16)
    print(f"[bridge] listening on http://{BIND[0]}:{BIND[1]}  token={token}")
    start_server(token).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
