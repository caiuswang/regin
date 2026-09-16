"""What the SDK tier actually hands the CLI when a prompt carries images.

The point of this tier is that the model receives the picture, not a path it
would have to go and open — so these tests read the message that lands on the
CLI's stream-json stdin, not the call that produced it.
"""

from __future__ import annotations

import asyncio

from lib.agent_sdk.runner import _query_payload


def _drain(payload):
    async def _first():
        async for message in payload:
            return message
    return asyncio.run(_first())


def test_a_prompt_without_images_stays_the_bare_string():
    """Every existing turn must keep the shape it has always had — routing it
    through the block form to support a feature it never uses would change the
    wire format of the whole tier."""
    assert _query_payload("carry on", []) == "carry on"
    assert _query_payload("carry on", None) == "carry on"


def test_images_become_real_base64_content_blocks():
    payload = _query_payload("what is this?", [
        {"media_type": "image/png", "data_b64": "AAAA"},
    ])
    assert not isinstance(payload, str)
    content = _drain(payload)["message"]["content"]
    assert [block["type"] for block in content] == ["image", "text"]
    assert content[0]["source"] == {"type": "base64",
                                    "media_type": "image/png",
                                    "data": "AAAA"}


def test_the_question_follows_its_subject():
    """Text last: the image is what is being asked about, and a question reads
    better after the thing it is about."""
    content = _drain(_query_payload("and this?", [
        {"media_type": "image/png", "data_b64": "AAAA"},
        {"media_type": "image/jpeg", "data_b64": "BBBB"},
    ]))["message"]["content"]
    assert [block["type"] for block in content] == ["image", "image", "text"]
    assert content[-1] == {"type": "text", "text": "and this?"}


def test_the_message_is_shaped_as_a_user_turn():
    message = _drain(_query_payload("hi", [
        {"media_type": "image/gif", "data_b64": "AAAA"},
    ]))
    assert message["type"] == "user"
    assert message["message"]["role"] == "user"
    # `query` fills the session id in itself; sending one here would override it.
    assert "session_id" not in message


def test_every_image_keeps_its_own_media_type():
    content = _drain(_query_payload("x", [
        {"media_type": "image/png", "data_b64": "AAAA"},
        {"media_type": "image/webp", "data_b64": "BBBB"},
    ]))["message"]["content"]
    assert [b["source"]["media_type"] for b in content[:2]] == ["image/png",
                                                                "image/webp"]
