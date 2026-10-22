"""A run's own "my work is finished" report, delivered the way `regin task
finish` delivers it — shared by that command and the `regin` MCP server.

The report goes to the server's loopback finish route first, so dependents
it releases launch in the long-lived server. Only when nothing is listening
does it settle in the calling process, where an `sdk` dependent is left
marked for the server's next fire (an SDK child of a short-lived caller would
die with it).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Callable, Optional


class ReportRefused(Exception):
    """The report was not accepted. `open_defects` names the defects that
    block a done report on a fix task, when that is the reason."""

    def __init__(self, message: str, open_defects=None) -> None:
        super().__init__(message)
        self.open_defects = list(open_defects or [])


def open_loopback(request, timeout: float):
    """`urlopen` without the environment's proxy: through one, a server that
    is down answers 502/503 instead of refusing the connection, and the
    no-server fallback never runs."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return opener.open(request, timeout=timeout)


def refused_connection(exc: OSError) -> bool:
    """Nothing is listening — the server is down, not slow. A timeout is not
    this: a server that may still be handling the report must not race a
    second settle from here."""
    return isinstance(getattr(exc, "reason", exc), ConnectionRefusedError)


def _request(url: str, run_task: Optional[int], status: str,
             reason: str) -> urllib.request.Request:
    payload = {"status": status, "reason": reason}
    if run_task is not None:
        payload["task_id"] = run_task
    return urllib.request.Request(
        url, data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json"})


def _server_refusal(exc: urllib.error.HTTPError) -> ReportRefused:
    raw = (exc.read() or b"").decode(errors="replace")
    try:
        body = json.loads(raw)
    except ValueError:
        body = {}
    detail = (body.get("error") if isinstance(body, dict) else None) or raw
    blocking = body.get("open_defects") if isinstance(body, dict) else None
    return ReportRefused(f"server refused ({exc.code}): {detail}", blocking)


def _settle_here(task_id: Optional[int], run_id: int, status: str,
                 reason: str) -> dict:
    from lib.tasks import runs  # noqa: PLC0415

    try:
        return runs.finish_run(run_id, status=status, reason=reason,
                               in_server=False, task_id=task_id)
    except runs.RunRefused as exc:
        raise ReportRefused(f"refused: {exc}", exc.blocking) from None
    except ValueError as exc:
        raise ReportRefused(f"refused: {exc}") from None


def report_finish(task_id: Optional[int], run_id: int, status: str,
                  reason: str, *, opener: Callable = open_loopback,
                  on_local: Callable[[], None] = lambda: None) -> dict:
    """Settle run `run_id` as `status`. Raises `ReportRefused` with the reason.

    `on_local` is called just before settling in this process, so a caller
    with a terminal can say the server was not running.
    """
    from lib.settings import settings  # noqa: PLC0415

    base = settings.agent_memory.inject_server_url.rstrip("/")
    url = f"{base}/api/tasks/runs/{run_id}/finish"
    try:
        with opener(_request(url, task_id, status, reason),
                    timeout=15) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        raise _server_refusal(exc) from None
    except OSError as exc:
        if not refused_connection(exc):
            raise ReportRefused(f"cannot reach regin at {url}: {exc}") from None
    on_local()
    return _settle_here(task_id, run_id, status, reason)
