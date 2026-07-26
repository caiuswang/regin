"""Kimi parser residue guards: one image-size cap, and no image bookkeeping
on the steer path.

The Claude counterparts are asserted alongside each Kimi case — the parsers
must agree on *where* a cap lives, or the two providers diverge silently.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

from hook_manager.handlers.turn_trace.span_posters import _cap_images
from lib.trace.kimi_transcript import read_usage_kimi
from lib.trace.transcript_usage import read_usage

_OVERSIZE = b"\x89PNG\r\n\x1a\n" + b"p" * 4096


def _wire(path: Path, recs: list[dict]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")
    return str(path)


def _step(uuid: str) -> list[dict]:
    return [
        {"type": "context.append_loop_event",
         "event": {"type": "step.begin", "uuid": uuid}, "time": 2},
        {"type": "context.append_loop_event",
         "event": {"type": "step.end", "uuid": uuid, "usage": {"output": 1}},
         "time": 3},
    ]


def _image_session(tmp_path: Path, blob: bytes) -> str:
    main = tmp_path / "main"
    (main / "blobs").mkdir(parents=True)
    digest = hashlib.sha256(blob).hexdigest()
    (main / "blobs" / digest).write_bytes(blob)
    return _wire(main / "wire.jsonl", [
        {"type": "turn.prompt", "time": 1, "input": [
            {"type": "image_url",
             "imageUrl": {"url": f"blobref:image/png;{digest}"}},
            {"type": "text", "text": "look"},
        ]},
        *_step("s1"),
    ])


def _shrink_cap(monkeypatch) -> int:
    from lib.settings import settings

    cap = len(_OVERSIZE) - 1
    monkeypatch.setattr(settings, "prompt_image_max_bytes", cap)
    return cap


def test_kimi_parser_does_not_pre_cap_oversized_blobs(tmp_path: Path, monkeypatch):
    """The byte cap belongs to the persistence step, which still records the
    image in `image_indices` when it drops it. A parser-side cap would erase
    that evidence."""
    cap = _shrink_cap(monkeypatch)
    u = read_usage_kimi(_image_session(tmp_path, _OVERSIZE))
    parts = u.prompt_image_parts["kprompt-0"]
    assert [p["idx"] for p in parts] == [1]
    assert base64.b64decode(parts[0]["data_b64"]) == _OVERSIZE
    assert _cap_images(parts, 10, cap) == []
    assert _cap_images(parts, 10, 0) == parts


def test_claude_parser_does_not_pre_cap_oversized_images(tmp_path: Path, monkeypatch):
    """Claude's parser has never capped by size — the Kimi parser now matches
    it, so both providers hit the same single downstream cap."""
    cap = _shrink_cap(monkeypatch)
    data_b64 = base64.b64encode(_OVERSIZE).decode("ascii")
    path = tmp_path / "t.jsonl"
    with open(path, "w") as f:
        for entry in (
            {"type": "user", "uuid": "u1", "parentUuid": None,
             "timestamp": "2026-06-01T03:22:52Z",
             "message": {"content": [
                 {"type": "text", "text": "look [Image #1]"},
                 {"type": "image", "source": {
                     "type": "base64", "media_type": "image/png",
                     "data": data_b64}},
             ]}},
            {"type": "assistant", "uuid": "a1", "parentUuid": "u1",
             "timestamp": "2026-06-01T03:22:55Z",
             "message": {"model": "claude-opus-4-7", "content": [],
                         "usage": {"input_tokens": 1, "output_tokens": 1}}},
        ):
            f.write(json.dumps(entry) + "\n")

    parts = read_usage(str(path)).prompt_image_parts["u1"]
    assert [p["idx"] for p in parts] == [1]
    assert base64.b64decode(parts[0]["data_b64"]) == _OVERSIZE
    assert _cap_images(parts, 10, cap) == []


def test_kimi_steer_keeps_typed_text_and_ignores_images(tmp_path: Path):
    """A steer becomes a `queued_command` attachment carrying only what the
    user typed; its image parts belong to the prompt anchor, not here."""
    digest = hashlib.sha256(_OVERSIZE).hexdigest()
    path = _wire(tmp_path / "main" / "wire.jsonl", [
        {"type": "turn.prompt", "time": 1,
         "input": [{"type": "text", "text": "go"}]},
        {"type": "turn.steer", "time": 2, "origin": {"kind": "user"}, "input": [
            {"type": "image_url",
             "imageUrl": {"url": f"blobref:image/png;{digest}"}},
            {"type": "text", "text": "actually stop"},
        ]},
        *_step("s1"),
    ])
    (steer,) = [a for a in read_usage_kimi(path).attachments
                if a.kind == "queued_command"]
    assert steer.payload == {"command_mode": "prompt", "prompt": "actually stop"}
    assert read_usage_kimi(path).prompt_image_parts == {}
