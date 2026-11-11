"""Protocol tests use a real local WebSocket without vendor credentials."""

import asyncio

import pytest

from prism_test_protocol.audio import InputAudio
from prism_test_protocol.config import (
    INPUT_FRAME_BYTES,
    RimeRealtimeConfig,
)
from prism_test_protocol.connection import PrismError
from prism_test_protocol.conversation import Conversation


async def receive(events, kind):
    while True:
        event = await asyncio.wait_for(events.get(), 2)
        if event["type"] == kind:
            return event


@pytest.mark.asyncio
async def test_initialization_gates_audio_and_uses_bearer(peer, connection):
    client, events = connection
    peer.auto_initialize = False
    starting = asyncio.create_task(client.start())
    try:
        setup = await peer.request("session.update")
        assert not starting.done()
        with pytest.raises(PrismError, match="not initialized"):
            await client.send_audio(bytes(INPUT_FRAME_BYTES))
        assert peer.authorizations == ["Bearer test-only"]
        assert set(setup["session"]) == {
            "voice",
            "instructions",
            "tools",
            "turn_detection",
        }
        assert setup["session"]["turn_detection"] == {
            "interrupt_response": True,
            "eagerness": "auto",
        }
        await peer.acknowledge(setup, "session.updated")
        await starting
        assert client.ready
        await client.send_audio(bytes(INPUT_FRAME_BYTES))
        assert (await peer.request("input_audio_buffer.append"))["audio"]
    finally:
        starting.cancel()
        await asyncio.gather(starting, return_exceptions=True)


@pytest.mark.asyncio
async def test_greeting_waits_for_typed_readiness_and_is_not_replayed(
    peer, connection
):
    client, events = connection
    await client.start()
    await peer.request("session.update")
    await client.trigger("Say hello")
    assert peer.requests.empty()
    await peer.send({"type": "prism.typed_input.ready"})
    request = await peer.request("response.create")
    assert request["response"]["prism_input_text"] == "Say hello"
    assert request["response"]["metadata"]["prism_cause"] == "user_text"
    await peer.send(
        {
            "type": "response.created",
            "prism_request_event_id": request["event_id"],
            "response": {"id": "greeting"},
        }
    )
    await receive(events, "response.created")
    await peer.send({"type": "prism.typed_input.ready"})
    await receive(events, "prism.typed_input.ready")
    assert peer.requests.empty()


@pytest.mark.asyncio
@pytest.mark.parametrize("parent_done_first", [False, True])
async def test_tool_continuation_waits_for_parent_and_every_ack(
    peer, connection, parent_done_first
):
    client, events = connection
    await client.start()
    await peer.request("session.update")
    await peer.send({"type": "response.created", "response": {"id": "parent"}})
    results = []
    for call in ["first", "second"]:
        await peer.send(
            {
                "type": "response.function_call_arguments.done",
                "response_id": "parent",
                "call_id": call,
                "name": "lookup",
                "arguments": "{}",
            }
        )
        event = await receive(events, "response.function_call_arguments.done")
        assert event["call_id"] == call
        if call == "first":
            await peer.send(event)
        results.append(asyncio.create_task(client.tool_result(call, "result")))
    requests = [
        await peer.request("conversation.item.create") for _ in range(2)
    ]
    done = {
        "type": "response.done",
        "response": {"id": "parent", "status": "completed"},
    }
    if parent_done_first:
        await peer.send(done)
        await receive(events, "response.done")
    await peer.acknowledge(requests[1], "conversation.item.created")
    await receive(events, "conversation.item.created")
    assert peer.requests.empty()
    await peer.acknowledge(requests[0], "conversation.item.created")
    await receive(events, "conversation.item.created")
    if not parent_done_first:
        assert peer.requests.empty()
        await peer.send(done)
    continuation = await peer.request("response.create")
    await asyncio.gather(*results)
    assert continuation["response"]["metadata"] == {
        "prism_cause": "tool_continuation",
        "prism_parent_response_id": "parent",
    }
    await peer.acknowledge(requests[0], "conversation.item.created")
    await receive(events, "conversation.item.created")
    assert peer.requests.empty()
    with pytest.raises(ValueError, match="already submitted"):
        await client.tool_result("first", "duplicate")


@pytest.mark.asyncio
async def test_auth_refusal_is_not_retried(peer, connection):
    client, _ = connection
    peer.refuse_status = 401
    client.config.startup_retries = 3
    with pytest.raises(PrismError, match="HTTP 401"):
        await client.start()
    assert len(peer.authorizations) == 1
    assert not client.ready


@pytest.mark.asyncio
async def test_disconnect_is_terminal_and_does_not_reconnect(peer, connection):
    client, events = connection
    await client.start()
    await (await peer.connected).close()
    error = await receive(events, "error")
    assert error["error"]["scope"] == "session"
    assert len(peer.authorizations) == 1
    assert not client.ready


@pytest.mark.asyncio
async def test_transient_refusals_have_bounded_startup_retries(
    peer, connection
):
    client, _ = connection
    peer.refuse_status = 503
    client.config.startup_retries = 1
    with pytest.raises(PrismError, match="HTTP 503"):
        await client.start()
    assert len(peer.authorizations) == 2
    assert client._session.closed


@pytest.mark.asyncio
async def test_cancelled_startup_closes_transport(peer, connection):
    client, _ = connection
    peer.auto_initialize = False
    starting = asyncio.create_task(client.start())
    await peer.request("session.update")
    starting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await starting
    assert client._receiver is None
    assert client._session.closed
    assert not client.ready


@pytest.mark.asyncio
async def test_missing_ack_has_a_fatal_bounded_outcome(peer, connection):
    client, _ = connection
    await client.start()
    peer.auto_initialize = False
    client.config.startup_timeout = 0.01
    with pytest.raises(PrismError) as failure:
        await client.update_tools([])
    assert failure.value.code == "acknowledgment_timeout"
    assert failure.value.fatal
    assert not client._waiters


@pytest.mark.asyncio
async def test_disconnect_fails_pending_request_and_sanitizes_errors(
    peer, connection
):
    client, events = connection
    await client.start()
    peer.auto_initialize = False
    await peer.request("session.update")
    updating = asyncio.create_task(client.update_tools([]))
    await peer.request("session.update")
    await peer.send(
        {
            "type": "error",
            "error": {
                "scope": "session",
                "code": "voice_activity_detection_failed",
                "message": "test-only",
            },
        }
    )
    with pytest.raises(PrismError) as failure:
        await updating
    assert failure.value.fatal
    event = await receive(events, "error")
    assert event["error"]["scope"] == "session"
    assert "test-only" not in str(event)
    assert events.empty()
    assert not client.ready


@pytest.mark.asyncio
async def test_malformed_event_ends_session_cleanly(peer, connection):
    client, events = connection
    await client.start()
    await peer.send({"type": "response.created", "response": {}})
    event = await receive(events, "error")
    assert event["error"]["code"] == "protocol_error"
    await client._receiver
    assert client._session.closed


@pytest.mark.asyncio
async def test_rejected_tool_result_ends_instead_of_waiting(peer, connection):
    client, events = connection
    await client.start()
    await peer.send({"type": "response.created", "response": {"id": "parent"}})
    await peer.send(
        {
            "type": "response.function_call_arguments.done",
            "response_id": "parent",
            "call_id": "call",
            "name": "lookup",
            "arguments": "{}",
        }
    )
    await receive(events, "response.function_call_arguments.done")
    result = asyncio.create_task(client.tool_result("call", "result"))
    request = await peer.request("conversation.item.create")
    await peer.send(
        {
            "type": "error",
            "error": {
                "scope": "event",
                "code": "tool_call_not_found",
                "event_id": request["event_id"],
            },
        }
    )
    with pytest.raises(PrismError) as failure:
        await result
    assert failure.value.fatal
    assert (await receive(events, "error"))["error"]["scope"] == "session"
    assert not client.conversation.result_events


@pytest.mark.asyncio
async def test_superseded_typed_turn_does_not_block_later_turn(
    peer, connection
):
    client, events = connection
    await client.start()
    await client.trigger("First")
    await peer.send({"type": "prism.typed_input.ready"})
    await peer.request("response.create")
    await peer.send(
        {
            "type": "error",
            "error": {
                "code": "typed_turn_superseded",
                "scope": "utterance",
                "event_id": None,
                "owner": {"item_id": "typed"},
            },
        }
    )
    await receive(events, "error")
    await client.trigger("Second")
    await peer.send({"type": "prism.typed_input.ready"})
    assert (await peer.request("response.create"))["response"][
        "prism_input_text"
    ] == "Second"


@pytest.mark.asyncio
async def test_busy_typed_turn_waits_for_a_new_readiness_event(
    peer, connection
):
    client, events = connection
    await client.start()
    await client.trigger("First")
    await peer.send({"type": "prism.typed_input.ready"})
    request = await peer.request("response.create")
    await peer.send(
        {
            "type": "error",
            "error": {
                "code": "typed_turn_busy",
                "scope": "event",
                "event_id": request["event_id"],
            },
        }
    )
    await receive(events, "error")
    assert peer.requests.empty()
    await peer.send({"type": "prism.typed_input.ready"})
    assert (await peer.request("response.create"))["response"] == request[
        "response"
    ]


def test_expired_tool_round_accepts_result_without_continuing():
    state = Conversation()
    state.created("parent")
    state.function_call("parent", "call")
    state.done("parent", "completed")
    state.close_round("parent")
    state.result_sent("result", "call")
    state.acknowledged("result")
    assert state.continuations() == []


def test_interrupted_output_and_cancelled_tool_round_are_refused():
    state = Conversation()
    state.created("old")
    state.function_call("old", "call")
    state.interrupt()
    assert not state.accepts_output("old")
    state.created("new")
    assert state.accepts_output("new")
    state.result_sent("result", "call")
    state.acknowledged("result")
    state.done("old", "completed")
    assert state.continuations() == []
    state.done("old", "cancelled")
    assert state.continuations() == []
    with pytest.raises(ValueError, match="unknown tool"):
        state.result_sent("other", "missing")


def test_audio_queue_preserves_samples_fills_silence_and_is_bounded():
    audio = InputAudio()
    audio.append(b"\1\0" * 160)
    assert audio.frame() == b"\1\0" * 160 + bytes(320)
    assert audio.frame() == bytes(INPUT_FRAME_BYTES)
    with pytest.raises(ValueError, match="whole PCM16"):
        audio.append(b"\0")
    with pytest.raises(ValueError, match="one second"):
        audio.append(bytes(32002))
    assert audio.frame() == bytes(INPUT_FRAME_BYTES)


def test_config_redacts_credentials_and_refuses_unsafe_endpoint():
    config = RimeRealtimeConfig(api_key="test-only")
    assert "test-only" not in config.model_dump_json()
    with pytest.raises(ValueError):
        RimeRealtimeConfig(endpoint="ws://example.com/realtime")
    with pytest.raises(ValueError):
        RimeRealtimeConfig(startup_retries=-1)
    with pytest.raises(ValueError):
        RimeRealtimeConfig(endpoint="wss://example.com:invalid/realtime")
