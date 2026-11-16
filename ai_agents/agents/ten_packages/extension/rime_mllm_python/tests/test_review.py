"""Review decisions stay correlated and never block the event reader."""

import asyncio
import json

import pytest
from pydantic import ValidationError

from prism_test_protocol.controls import ReviewDecision
from test_extension import next_data


@pytest.mark.parametrize(
    "decision",
    [
        {"action": "replace"},
        {"action": "replace", "text": " \t"},
        {"action": "allow", "text": "wrong"},
        {"action": "stop", "text": "wrong"},
        {"action": "unknown"},
        {"action": "allow", "extra": True},
    ],
)
def test_invalid_review_decisions_are_refused(decision):
    with pytest.raises(ValidationError):
        ReviewDecision.model_validate(decision)


def request(identity, index=0):
    return {
        "type": "prism.review.requested",
        "review_id": identity,
        "response_id": "reply",
        "item_id": "item",
        "index": index,
        "text": "Please check this.",
        "server_time_ms": 1792857632978,
    }


async def started(peer, runtime, policy="stop", timeout=1000):
    review = {"on_timeout": policy, "timeout_ms": timeout}
    if policy == "say":
        review["timeout_line"] = "One moment."
    environment = runtime.environment(
        {
            "endpoint": peer.url,
            "api_key": "test-only",
            "prism_review": review,
            "review_extension": "reviewer",
            "emit_server_events": True,
        }
    )
    provider = runtime.extension.RimeRealtimeExtension("v2v")
    await provider.on_init(environment)
    await provider.on_start(environment)
    await next_data(environment, "mllm_server_session_ready")
    await peer.send({"type": "response.created", "response": {"id": "reply"}})
    return provider, environment


@pytest.mark.asyncio
async def test_parallel_reviews_allow_replace_and_stop_without_blocking_reader(
    peer, ten_modules, monkeypatch
):
    provider, environment = await started(peer, ten_modules)
    decisions = {
        name: asyncio.get_running_loop().create_future()
        for name in ["first", "second", "third"]
    }

    async def callback(command):
        await environment.commands.put(command)
        decision = await decisions[command.payload["review_id"]]
        result = ten_modules.message(0, {"decision": decision})
        result.get_status_code = lambda: 0
        return result, None

    monkeypatch.setattr(environment, "send_cmd", callback)
    try:
        await peer.send(request("first"))
        await peer.send(request("second", 1))
        for _ in range(2):
            command = await asyncio.wait_for(environment.commands.get(), 2)
            assert command.name == "prism_review"
            assert command.payload["server_time_ms"] == 1792857632978
        await peer.send(
            {
                "type": "response.text.delta",
                "response_id": "reply",
                "delta": "Reader remains live",
            }
        )
        assert (await next_data(environment, "mllm_server_output_transcript"))[
            "content"
        ] == "Reader remains live"
        decisions["second"].set_result(
            {"action": "replace", "text": "x" * 5001}
        )
        second = await peer.request("prism.review.result")
        assert second["review_id"] == "second" and len(second["text"]) == 5001
        decisions["first"].set_result({"action": "allow"})
        first = await peer.request("prism.review.result")
        assert first["review_id"] == "first" and "text" not in first
        await peer.send(request("third", 2))
        await asyncio.wait_for(environment.commands.get(), 2)
        decisions["third"].set_result({"action": "stop"})
        assert (await peer.request("prism.review.result"))["action"] == "stop"
        await peer.send(request("first"))
        await peer.send(
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "transcript": "Barrier",
            }
        )
        await next_data(environment, "mllm_server_input_transcript")
        assert environment.commands.empty()
    finally:
        await provider.on_stop(environment)
    assert not provider._review_tasks


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["interrupt", "response_done", "shutdown"])
async def test_pending_reviews_are_cancelled_without_late_decisions(
    peer, ten_modules, monkeypatch, ending
):
    provider, environment = await started(peer, ten_modules)
    began = asyncio.Event()
    cancelled = asyncio.Event()

    async def waiting(_command):
        began.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(environment, "send_cmd", waiting)
    try:
        await peer.send(request("pending"))
        await asyncio.wait_for(began.wait(), 2)
        if ending == "interrupt":
            await peer.send({"type": "input_audio_buffer.speech_started"})
            await next_data(environment, "mllm_server_interrupted")
        elif ending == "response_done":
            await peer.send(
                {
                    "type": "response.done",
                    "response": {"id": "reply", "status": "cancelled"},
                }
            )
        else:
            await provider.on_stop(environment)
        await asyncio.wait_for(cancelled.wait(), 2)
    finally:
        await provider.on_stop(environment)
    assert not provider._review_tasks
    assert not any(
        event.get("type") == "prism.review.result"
        for event in list(peer.requests._queue)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["stop", "speak", "say"])
async def test_callback_timeout_defers_to_server_policy_and_preserves_review_errors(
    peer, ten_modules, monkeypatch, policy
):
    provider, environment = await started(peer, ten_modules, policy, 100)

    async def waiting(_command):
        await asyncio.Event().wait()

    monkeypatch.setattr(environment, "send_cmd", waiting)
    try:
        await peer.send(request("timeout"))
        error = await next_data(environment, "error")
        assert error["vendor_info"]["code"] == "review_callback_failed"
        assert error["code"] > 0 and provider.is_connected()
        server_error = {
            "type": "error",
            "server_time_ms": 124,
            "error": {
                "code": "review_timeout",
                "message": "vendor echoed test-only",
                "scope": "review",
                "owner": {
                    "kind": "review",
                    "response_id": "reply",
                    "review_id": "timeout",
                },
            },
        }
        await peer.send(server_error)
        while True:
            event = (await next_data(environment, "prism_server_event"))[
                "event"
            ]
            if event["type"] == "error":
                assert event["error"]["scope"] == "review"
                assert event["error"]["owner"]["review_id"] == "timeout"
                assert "test-only" not in json.dumps(event)
                break
        assert provider.is_connected()
        assert not any(
            event.get("type") == "prism.review.result"
            for event in list(peer.requests._queue)
        )
    finally:
        await provider.on_stop(environment)
