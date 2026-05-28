"""TraceSession harness: drive a real `claude` CLI session through tmux and
verify the session_spans it emits via hooks.

Usage (pytest):
    with TraceSession(workdir=tmp_path) as ts:
        ts.send("read README.md and summarise")
        ts.assert_span("prompt", count=1)
        ts.assert_span("tool.Read", min_count=1)

CLI smoke test:
    python tests/trace/harness.py --demo "read README.md"

Design notes
------------
- The authoritative "Claude is idle" signal is a `Stop` entry in
  `~/.claude/hook-payloads.jsonl` whose `session_id` matches this session —
  parsing the ANSI TUI is unreliable.
- `trace_id == session_id` (see lib/hook_plugin.py:251). The session_id is
  discovered from the first `UserPromptSubmit` jsonl entry after start.
- `GET /api/sessions/{trace_id}` is read-only — it projects parent/child
  relations in memory every call but never writes to the DB. If you need
  the projection persisted (e.g. for analytics), POST to
  `/api/sessions/{trace_id}/materialize`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

# Resolve hook-payloads.jsonl path through the active provider, not a
# hard-coded ~/.claude path: the same regin install can run with Claude
# OR Codex as the active provider, and `trace_payload.handle()` writes
# wherever `lib.providers.get_active_provider().hook_payload_log_path()`
# returns. Hard-coding the Claude path silently breaks the harness when
# the user has flipped `active_provider` in settings.local.json.
try:
    from lib.providers import get_active_provider as _get_active_provider
    HOOK_LOG = Path(_get_active_provider().hook_payload_log_path())
    TRACES_DIR = Path(_get_active_provider().traces_dir())
except Exception:
    HOOK_LOG = Path.home() / ".claude" / "hook-payloads.jsonl"
    TRACES_DIR = Path.home() / ".claude" / "traces"
DEFAULT_API = os.environ.get("REGIN_TRACE_API", "http://127.0.0.1:8321/api")


class TraceSessionError(RuntimeError):
    pass


class TraceSession:
    """Drive a real `claude` session inside a dedicated tmux window."""

    def __init__(
        self,
        name: str | None = None,
        workdir: str | Path | None = None,
        api_base: str = DEFAULT_API,
        test_name: str | None = None,
    ):
        self.name = name or f"trace-test-{uuid.uuid4().hex[:8]}"
        self.workdir = Path(workdir or Path.cwd()).resolve()
        self.api_base = api_base.rstrip("/")
        # Stamped onto spans as attributes.test_name so the UI / API can label
        # which test case produced each session.
        self.test_name = test_name
        self._trace_id: str | None = None
        self._baseline_offset = 0
        self._started = False

    # ------------------------------------------------------------------ tmux
    def _tmux(self, *args: str, check: bool = True) -> str:
        r = subprocess.run(
            ["tmux", *args], capture_output=True, text=True, check=False
        )
        if check and r.returncode != 0:
            raise TraceSessionError(
                f"tmux {' '.join(args)} failed ({r.returncode}): {r.stderr.strip()}"
            )
        return r.stdout

    def _send_keys(self, *keys: str) -> None:
        self._tmux("send-keys", "-t", self.name, *keys)

    def capture_pane(self, lines: int = 200) -> str:
        return self._tmux("capture-pane", "-pt", self.name, "-S", f"-{lines}")

    def _session_exists(self) -> bool:
        r = subprocess.run(
            ["tmux", "has-session", "-t", self.name],
            capture_output=True, text=True,
        )
        return r.returncode == 0

    # ------------------------------------------------------------- lifecycle
    # Marker strings used to detect Claude TUI state in pane captures.
    _TRUST_DIALOG_NEEDLE = "Is this a project you created or one you trust?"
    _PROMPT_NEEDLE = "❯"

    def start(
        self,
        permission_mode: str | None = "bypassPermissions",
        model: str | None = None,
        extra_args: list[str] | None = None,
        startup_timeout: float = 30.0,
    ) -> None:
        # Default to Haiku: trace tests exercise tool-call wiring and hook
        # plumbing, not anything model-quality-sensitive. Haiku is the
        # cheapest option and still fires every hook. Capital 'H' matches
        # the alias shown in `claude`'s own /model picker. Override with
        # REGIN_TRACE_TEST_MODEL= (e.g. 'Opus' for a one-off debug run).
        if model is None:
            model = os.environ.get('REGIN_TRACE_TEST_MODEL', 'Haiku')

        if self._started:
            raise TraceSessionError("TraceSession already started")
        if shutil.which("tmux") is None:
            raise TraceSessionError("tmux executable not found on PATH")
        if shutil.which("claude") is None:
            raise TraceSessionError("claude executable not found on PATH")

        self.workdir.mkdir(parents=True, exist_ok=True)
        HOOK_LOG.parent.mkdir(parents=True, exist_ok=True)
        HOOK_LOG.touch(exist_ok=True)
        self._baseline_offset = HOOK_LOG.stat().st_size

        self._tmux(
            "new-session", "-d", "-s", self.name,
            "-x", "240", "-y", "70",
            "-c", str(self.workdir),
        )

        # Use absolute path so the harness works regardless of whether the
        # tmux-spawned shell (fish/zsh) has claude's NVM-managed bin dir on
        # PATH. shutil.which() ran in conftest._environment_checks, so this
        # cannot be None at this point.
        claude_bin = shutil.which("claude") or "claude"
        cmd_parts = [claude_bin]
        if permission_mode:
            cmd_parts.append(f"--permission-mode={permission_mode}")
        if model:
            cmd_parts.append(f"--model={model}")
        if extra_args:
            cmd_parts.extend(extra_args)
        # Prefix with REGIN_TRACE_TEST=1 (+ optional REGIN_TRACE_TEST_NAME) so
        # every hook this claude fires stamps `is_test=true` and the test
        # nodeid on its spans — /api/sessions?include_tests=false (default)
        # then hides these test sessions from the Trace view, while the UI
        # shows the test name when it DOES include them.
        env_parts = ["REGIN_TRACE_TEST=1"]
        if self.test_name:
            # Escape single quotes for shell safety; nodeid strings come from
            # pytest and are normally quote-free, but be defensive.
            escaped = self.test_name.replace("'", "'\\''")
            env_parts.append(f"REGIN_TRACE_TEST_NAME='{escaped}'")
        cmd_str = "env " + " ".join(env_parts) + " " + " ".join(cmd_parts)
        self._send_keys(cmd_str, "Enter")

        self._wait_for_ready_prompt(timeout=startup_timeout)
        # A touch of slack so the input widget is definitely listening.
        time.sleep(0.3)
        self._started = True

    def _wait_for_ready_prompt(
        self, *, timeout: float, poll_interval: float = 0.3
    ) -> None:
        """Wait for Claude's input prompt, auto-dismissing any trust dialog."""
        deadline = time.monotonic() + timeout
        dismissed_trust = False
        while time.monotonic() < deadline:
            pane = self.capture_pane(lines=80)
            if not dismissed_trust and self._TRUST_DIALOG_NEEDLE in pane:
                # Default highlighted option is "Yes, I trust this folder".
                self._send_keys("Enter")
                dismissed_trust = True
                time.sleep(0.5)
                continue
            if self._PROMPT_NEEDLE in pane:
                return
            time.sleep(poll_interval)
        raise TraceSessionError(
            f"Claude TUI prompt did not appear within {timeout}s. Last pane:\n{self.capture_pane()}"
        )

    def stop(self, exit_timeout: float = 0.5) -> None:
        """End the Claude session cleanly, then tear down the tmux session.

        Sends `/exit` so Claude runs its SessionEnd hook and the session
        transitions from ACTIVE to ENDED in the trace DB. The prior
        implementation only sent Ctrl-C, which interrupts mid-flight and
        leaves every session stuck in ACTIVE because SessionEnd never fires.

        Ctrl-C is still sent as a safety net after `exit_timeout` — if
        /exit couldn't be processed (mid-turn, TUI hung, etc.) it
        unsticks Claude before tmux kill-session runs; if /exit already
        succeeded, Ctrl-C just hits the returned shell prompt harmlessly.
        """
        if not self._session_exists():
            return
        try:
            # Type /exit as a slash command and submit. wait_idle is not
            # appropriate here — we're not waiting on Stop, we're waiting
            # on the process itself to terminate.
            self._send_keys("-l", "/exit")
            time.sleep(0.15)
            self._send_keys("Enter")
            time.sleep(exit_timeout)
            # Safety net: mid-turn /exit can be intercepted; unstick.
            self._send_keys("C-c")
            time.sleep(0.15)
        except TraceSessionError:
            pass
        subprocess.run(
            ["tmux", "kill-session", "-t", self.name],
            capture_output=True, text=True,
        )

    def __enter__(self) -> "TraceSession":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    # ---------------------------------------------------------------- input
    def send(
        self,
        text: str,
        wait_idle: bool = True,
        idle_timeout: float = 180.0,
    ) -> None:
        if not self._started:
            raise TraceSessionError("call start() before send()")

        # Snapshot jsonl offset so we can scan for events emitted by this turn.
        turn_offset = HOOK_LOG.stat().st_size

        # Send text as literal keystrokes, then submit. We intentionally do NOT
        # clear the input buffer first — Ctrl-U is unreliable inside Claude's
        # TUI and at startup the buffer is empty.
        self._send_keys("-l", text)
        time.sleep(0.15)
        self._send_keys("Enter")

        # Learn trace_id from the first UserPromptSubmit entry after this offset.
        entry = self._wait_hook_entry(
            since_offset=turn_offset, event="UserPromptSubmit", timeout=20
        )
        if not self._trace_id:
            self._trace_id = entry["session_id"]
        if wait_idle:
            self._wait_hook_entry(
                since_offset=turn_offset,
                event="Stop",
                session_id=self._trace_id,
                timeout=idle_timeout,
            )

    def send_keys(self, *keys: str) -> None:
        """Send raw tmux keys (Enter, C-c, Tab, literal strings, etc.)."""
        self._send_keys(*keys)

    # -------------------------------------------------------- hook payloads
    def _iter_jsonl(self, since_offset: int):
        try:
            with open(HOOK_LOG, "r") as f:
                f.seek(since_offset)
                for raw in f:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        yield json.loads(raw)
                    except json.JSONDecodeError:
                        continue
        except FileNotFoundError:
            return

    def _wait_hook_entry(
        self,
        *,
        since_offset: int,
        event: str,
        session_id: str | None = None,
        timeout: float = 30.0,
        poll_interval: float = 0.3,
    ) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for entry in self._iter_jsonl(since_offset):
                if entry.get("hook_event") != event:
                    continue
                if session_id and entry.get("session_id") != session_id:
                    continue
                return entry
            time.sleep(poll_interval)
        raise TraceSessionError(
            f"no {event} hook event (session_id={session_id}) within {timeout}s. "
            f"last pane:\n{self.capture_pane(lines=60)}"
        )

    def hook_events(self, event: str | None = None) -> list[dict]:
        """All hook-payloads.jsonl entries for this session (since start)."""
        out = []
        for entry in self._iter_jsonl(self._baseline_offset):
            if self._trace_id and entry.get("session_id") != self._trace_id:
                continue
            if event and entry.get("hook_event") != event:
                continue
            out.append(entry)
        return out

    # ---------------------------------------------------------------- spans
    @property
    def trace_id(self) -> str:
        if not self._trace_id:
            raise TraceSessionError("trace_id unknown — send() a prompt first")
        return self._trace_id

    def _get_session_detail(self) -> dict:
        url = f"{self.api_base}/sessions/{self.trace_id}"
        try:
            with urlopen(Request(url), timeout=5) as r:
                return json.loads(r.read())
        except URLError as exc:
            raise TraceSessionError(
                f"failed to GET {url}: {exc}. Is Flask running on {self.api_base}?"
            ) from exc

    def fetch_spans(self) -> list[dict]:
        return self._get_session_detail().get("spans", [])

    def fetch_tree(self) -> dict:
        return self._get_session_detail()

    # ----------------------------------------------------------- assertions
    def assert_span(
        self,
        name: str,
        *,
        count: int | None = None,
        min_count: int | None = None,
        attrs: dict | None = None,
    ) -> list[dict]:
        all_spans = self.fetch_spans()
        spans = [s for s in all_spans if s["name"] == name]
        if count is not None and len(spans) != count:
            names = sorted({s["name"] for s in all_spans})
            raise AssertionError(
                f"expected {count} span(s) named {name!r}, got {len(spans)}. "
                f"span names in trace: {names}"
            )
        if min_count is not None and len(spans) < min_count:
            raise AssertionError(
                f"expected ≥{min_count} span(s) named {name!r}, got {len(spans)}"
            )
        if attrs:
            for s in spans:
                actual = s.get("attributes") or {}
                for k, v in attrs.items():
                    if actual.get(k) != v:
                        raise AssertionError(
                            f"span {name!r} attribute {k}={actual.get(k)!r}, "
                            f"expected {v!r}; full attrs: {actual}"
                        )
        return spans

    def assert_span_matching(self, predicate, *, min_count: int = 1) -> list[dict]:
        spans = self.fetch_spans()
        matches = [s for s in spans if predicate(s)]
        if len(matches) < min_count:
            raise AssertionError(
                f"expected ≥{min_count} spans matching predicate, got {len(matches)}. "
                f"names: {sorted({s['name'] for s in spans})}"
            )
        return matches

    def assert_parent_chain(
        self, child_name: str, ancestor_names: list[str]
    ) -> None:
        spans = self.fetch_spans()
        by_id = {s["span_id"]: s for s in spans}
        children = [s for s in spans if s["name"] == child_name]
        if not children:
            raise AssertionError(f"no span named {child_name!r} in trace")
        for child in children:
            chain: list[str] = []
            cur = child
            while cur and cur.get("parent_id"):
                cur = by_id.get(cur["parent_id"])
                if not cur:
                    break
                chain.append(cur["name"])
            for anc in ancestor_names:
                if anc not in chain:
                    raise AssertionError(
                        f"span {child_name!r} missing ancestor {anc!r}; "
                        f"chain upwards: {chain}"
                    )

    def wait_for_span(
        self,
        name: str,
        *,
        timeout: float = 120.0,
        poll_interval: float = 1.5,
    ) -> list[dict]:
        """Poll the session-spans API until at least one span with `name` exists.

        Useful for flows where `Stop` does not fire promptly (e.g. plan mode,
        where Claude's TUI sits on an approval dialog). Requires that at least
        one prompt has already been sent so `trace_id` is known.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                spans = self.fetch_spans()
            except TraceSessionError:
                time.sleep(poll_interval)
                continue
            matches = [s for s in spans if s["name"] == name]
            if matches:
                return matches
            time.sleep(poll_interval)
        raise TraceSessionError(
            f"span {name!r} did not appear within {timeout}s. "
            f"pane:\n{self.capture_pane(lines=60)}"
        )

    def assert_hook_event(
        self, event: str, *, count: int | None = None, min_count: int | None = None
    ) -> list[dict]:
        entries = self.hook_events(event=event)
        if count is not None and len(entries) != count:
            raise AssertionError(
                f"expected {count} {event} hook events, got {len(entries)}"
            )
        if min_count is not None and len(entries) < min_count:
            raise AssertionError(
                f"expected ≥{min_count} {event} hook events, got {len(entries)}"
            )
        return entries


# ----------------------------------------------------------------- CLI demo
def _print_tree(spans: list[dict]) -> None:
    by_parent: dict[str | None, list[dict]] = {}
    for s in spans:
        by_parent.setdefault(s.get("parent_id"), []).append(s)

    def walk(parent_id, indent):
        for s in sorted(by_parent.get(parent_id, []), key=lambda x: x["start_time"]):
            dur = s.get("duration_ms") or 0
            print(f"{'  ' * indent}• {s['name']} ({dur}ms)")
            walk(s["span_id"], indent + 1)

    walk(None, 0)


def _demo(prompts: list[str]) -> None:
    ts = TraceSession()
    print(f"[harness] tmux session: {ts.name}")
    print(f"[harness] workdir:      {ts.workdir}")
    ts.start()
    print("[harness] claude started, sending prompts…")
    try:
        for p in prompts:
            print(f"[harness] > {p}")
            ts.send(p, idle_timeout=180)
            print(f"[harness]   trace_id={ts.trace_id}")
        print("[harness] span tree:")
        _print_tree(ts.fetch_spans())
    finally:
        print("[harness] stopping tmux session…")
        ts.stop()


if __name__ == "__main__":
    import sys

    args = sys.argv[1:]
    if args and args[0] == "--demo":
        args = args[1:]
    if not args:
        args = ["read tests/trace/fixtures/sample.txt and say one word"]
    _demo(args)
