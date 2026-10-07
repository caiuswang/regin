"""A minimal ACP v1 client: one subprocess, one reader thread, request ids."""

from __future__ import annotations

import itertools
import json
import os
import queue
import shutil
import subprocess
import threading
from dataclasses import dataclass

from lib.activity_log import get_activity_logger

log = get_activity_logger("agent_acp")

DEFAULT_HANDSHAKE_TIMEOUT = 30.0


class AcpError(RuntimeError):
    """The subprocess died, the wire broke, or the server answered with an
    error other than the ones with dedicated types."""


class AcpTurnBusy(AcpError):
    """session/prompt while a turn is in flight: JSON-RPC -32600 with
    data.code 'turn.agent_busy' (no queueing on the ACP wire)."""


class AcpLaunchRefused(AcpError):
    """Structured refusal before anything is spawned or asked."""


@dataclass(frozen=True)
class PromptResult:
    """What settled the prompt request."""

    stop_reason: str


def resolve_acp_command(cmd: list[str] | None) -> list[str]:
    """The argv to spawn, refusing early when the binary is absent."""
    if cmd is not None:
        return list(cmd)
    binary = shutil.which("kimi")
    if not binary:
        raise AcpLaunchRefused("kimi CLI not found on PATH")
    return [binary, "acp"]


class AcpClient:
    """One `kimi acp` child process.

    `connect()` spawns; every public method after that is safe to call from
    any thread — a single reader thread owns the pipe and settles pending
    requests by id. Reverse-RPC the caller did not register a handler for
    answers methodNotFound, which is how the server learns this client does
    not serve fs/terminal/elicitation.
    """

    def __init__(self, cmd: list[str] | None = None,
                 env: dict[str, str] | None = None,
                 cwd: str | None = None) -> None:
        self._cmd = cmd
        self._env = env
        self._cwd = cwd
        self._proc: subprocess.Popen | None = None
        self._ids = itertools.count(1)
        self._pending: dict[int, queue.Queue] = {}
        self._handlers: dict[str, object] = {
            # Phase-1 policy (feature #140): the channel is unattended — no
            # human reads this wire. Routine calls never reach here (the
            # launcher sets yolo mode); what does is answered with the one
            # outcome that is neither allow nor a fabricated deny.
            "session/request_permission": _cancel_permission,
        }
        self._update_callbacks: list[object] = []
        self._send_lock = threading.Lock()
        self._closed = False

    # ── lifecycle ──────────────────────────────────────────────

    def connect(self) -> None:
        argv = resolve_acp_command(self._cmd)
        env = dict(os.environ)
        env.update(self._env or {})
        try:
            self._proc = subprocess.Popen(
                argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, bufsize=1,
                env=env, cwd=self._cwd)
        except OSError as exc:
            raise AcpLaunchRefused(str(exc)) from exc
        threading.Thread(target=self._read_loop, daemon=True,
                         name="acp-reader").start()

    @property
    def pid(self) -> int | None:
        """The child pid, for the `agent_runs` row. None before `connect()`
        and after `close()`."""
        proc = self._proc
        return proc.pid if proc is not None else None

    def close(self) -> None:
        self._closed = True
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001 — teardown must not raise
            proc.kill()

    def __enter__(self) -> "AcpClient":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ── ACP surface ────────────────────────────────────────────

    def initialize(self, timeout: float = DEFAULT_HANDSHAKE_TIMEOUT) -> dict:
        return self._request("initialize", {
            "protocolVersion": 1,
            # fs/terminal/elicitation are deliberately NOT advertised: bash
            # then runs engine-side and its output lands in the tool card.
            "clientCapabilities": {},
        }, timeout=timeout)

    def session_new(self, cwd: str,
                    timeout: float = DEFAULT_HANDSHAKE_TIMEOUT) -> str:
        result = self._request("session/new", {"cwd": cwd, "mcpServers": []},
                               timeout=timeout)
        return result["sessionId"]

    def session_set_mode(self, session_id: str, mode: str,
                         timeout: float = DEFAULT_HANDSHAKE_TIMEOUT) -> None:
        self._request("session/set_mode",
                      {"sessionId": session_id, "modeId": mode},
                      timeout=timeout)

    def session_set_model(self, session_id: str, model: str,
                          timeout: float = DEFAULT_HANDSHAKE_TIMEOUT) -> None:
        self._request("session/set_model",
                      {"sessionId": session_id, "modelId": model},
                      timeout=timeout)

    def session_prompt(self, session_id: str, text: str,
                       on_chunk: object = None,
                       timeout: float | None = None,
                       images: list | None = None) -> PromptResult:
        """`images` are `{media_type, data_b64}` entries, sent as ACP image
        content blocks after the text block."""
        if on_chunk is not None:
            self._update_callbacks.append(on_chunk)
        prompt = [{"type": "text", "text": text}]
        prompt += [{"type": "image", "mimeType": img["media_type"],
                    "data": img["data_b64"]} for img in images or ()]
        try:
            result = self._request("session/prompt", {
                "sessionId": session_id,
                "prompt": prompt,
            }, timeout=timeout)
        finally:
            if on_chunk is not None:
                self._update_callbacks.remove(on_chunk)
        return PromptResult(stop_reason=result.get("stopReason", ""))

    def session_cancel(self, session_id: str) -> None:
        self._notify("session/cancel", {"sessionId": session_id})

    # ── wire ───────────────────────────────────────────────────

    def on_request(self, method: str, handler: object) -> None:
        """Answer an agent→client reverse-RPC; the callable returns the
        `result` object echoed back."""
        self._handlers[method] = handler

    def _read_loop(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        for line in self._proc.stdout:
            line = line.strip()
            if line:
                self._dispatch(line)
        for pending in self._pending.values():
            pending.put(_DEAD)
        self._pending.clear()

    def _dispatch(self, line: str) -> None:
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            log.read("acp_garbage_line", detail=line[:200])
            return
        if "method" in msg:
            if "id" in msg:
                self._serve_reverse_rpc(msg)
            else:
                self._fan_out(msg)
        elif "id" in msg:
            pending = self._pending.pop(msg["id"], None)
            if pending is not None:
                pending.put(msg)

    def _serve_reverse_rpc(self, msg: dict) -> None:
        method = msg["method"]
        handler = self._handlers.get(method)
        if handler is None:
            self._send({"jsonrpc": "2.0", "id": msg["id"],
                        "error": {"code": -32601,
                                  "message": f"methodNotFound: {method}"}})
            return
        try:
            result = handler(msg.get("params") or {})
            self._send({"jsonrpc": "2.0", "id": msg["id"],
                        "result": result if result is not None else {}})
        except Exception as exc:  # noqa: BLE001 — answer, don't hang the agent
            log.error("acp_reverse_rpc_failed", method=method, exc_info=True)
            self._send({"jsonrpc": "2.0", "id": msg["id"],
                        "error": {"code": -32603, "message": str(exc)}})

    def _fan_out(self, msg: dict) -> None:
        if msg.get("method") != "session/update":
            return
        update = (msg.get("params") or {}).get("update") or {}
        if update.get("sessionUpdate") != "agent_message_chunk":
            return
        text = ((update.get("content") or {}).get("text")) or ""
        for callback in list(self._update_callbacks):
            callback(text)

    def _request(self, method: str, params: dict,
                 timeout: float | None) -> dict:
        if self._proc is None or self._closed:
            raise AcpError("ACP client is not connected")
        msg_id = next(self._ids)
        inbox: queue.Queue = queue.Queue(maxsize=1)
        self._pending[msg_id] = inbox
        try:
            self._send({"jsonrpc": "2.0", "id": msg_id, "method": method,
                        "params": params})
            reply = inbox.get(timeout=timeout)
        except queue.Empty as exc:
            raise AcpError(f"ACP request {method} timed out") from exc
        finally:
            self._pending.pop(msg_id, None)
        if reply is _DEAD:
            raise AcpError(f"ACP subprocess exited during {method}")
        if "error" in reply:
            self._raise_error(method, reply["error"])
        return reply.get("result") or {}

    @staticmethod
    def _raise_error(method: str, error: dict) -> None:
        data = error.get("data") or {}
        if error.get("code") == -32600 and data.get("code") == "turn.agent_busy":
            raise AcpTurnBusy(error.get("message") or "turn.agent_busy")
        raise AcpError(f"{method} failed: {error.get('message')}"
                       f" ({error.get('code')})")

    def _notify(self, method: str, params: dict) -> None:
        if self._proc is None:
            raise AcpError("ACP client is not connected")
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _send(self, msg: dict) -> None:
        with self._send_lock:
            assert self._proc is not None and self._proc.stdin is not None
            self._proc.stdin.write(json.dumps(msg) + "\n")
            self._proc.stdin.flush()


class _Dead:
    """Sentinel settled on every pending request when the pipe closes."""


def _cancel_permission(params: dict) -> dict:
    """The phase-1 answer to any approval/question on the unattended wire."""
    return {"outcome": {"outcome": "cancelled"},
            "message": "regin ACP channel: no human on the wire"}


_DEAD = _Dead()
