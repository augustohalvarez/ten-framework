"""Mutable controls, server-authored diagnostics, and protocol compatibility."""

import asyncio
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from prism_test_protocol.config import RimeRealtimeConfig, SessionSettingsUpdate
from prism_test_protocol.connection import PrismError
from test_extension import next_data


@pytest.mark.parametrize(
    "settings",
    [
        {"speed": 0.84},
        {"speed": 1.31},
        {"speed": float("nan")},
        {"interrupt_min_words": 0},
        {"interrupt_min_words": 11},
        {"interrupt_min_words": True},
        {"interrupt_phrases": [" "]},
        {"acknowledgment_phrases": ["yes"] * 101},
        {"end_of_turn_min_silence_ms": -1},
        {"prism_end_of_turn_timeout_ms": 999},
        {
            "end_of_turn_min_silence_ms": 2000,
            "prism_end_of_turn_timeout_ms": 2000,
        },
        {"prism_recording": {"keep": ["text", "text"]}},
        {"prism_recording": {"keep": ["unknown"]}},
        {"prism_review": {"on_timeout": "speak"}},
        {"prism_review": {"on_timeout": "say"}, "review_extension": "review"},
        {
            "prism_review": {"on_timeout": "stop", "timeout_line": "wrong"},
            "review_extension": "review",
        },
    ],
)
def test_invalid_initial_controls_are_refused(settings):
    with pytest.raises(ValidationError):
        RimeRealtimeConfig(**settings)


def test_session_wire_settings_preserve_defaults_and_explicit_empty_values():
    default = RimeRealtimeConfig().session_settings([])
    assert "speed" not in default and "prism_recording" not in default
    assert default["turn_detection"] == {
        "interrupt_response": True,
        "eagerness": "auto",
    }
    configured = RimeRealtimeConfig(
        speed=0.9,
        interrupt_min_words=2,
        interrupt_phrases=["stop"],
        acknowledgment_phrases=["uh-huh"],
        end_of_turn_min_silence_ms=800,
        prism_end_of_turn_timeout_ms=3000,
        prism_recording={"keep": []},
        prism_review={
            "on_timeout": "say",
            "timeout_ms": 500,
            "timeout_line": "One moment.",
        },
        review_extension="review",
    ).session_settings([])
    assert configured["speed"] == 0.9
    assert configured["prism_recording"] == {"keep": []}
    assert configured["turn_detection"]["interrupt_min_words"] == 2
    assert configured["turn_detection"]["end_of_turn_min_silence_ms"] == 800
    assert configured["prism_review"]["on_timeout"] == "say"
    assert (
        RimeRealtimeConfig(manual_turns=True).session_settings([])[
            "turn_detection"
        ]
        is None
    )


@pytest.mark.parametrize(
    "settings",
    [
        {"speed": None},
        {"manual_turns": True},
        {"end_of_turn_min_silence_ms": 100},
        {"unknown": True},
    ],
)
def test_runtime_updates_refuse_null_unknown_and_initial_only_settings(
    settings,
):
    with pytest.raises(ValidationError):
        SessionSettingsUpdate.model_validate(settings)


@pytest.mark.asyncio
async def test_runtime_update_commits_only_after_server_acceptance(
    peer, connection
):
    client, events = connection
    await client.start()
    await peer.request("session.update")
    peer.auto_initialize = False
    update = asyncio.create_task(
        client.update_session(
            SessionSettingsUpdate(speed=0.9, interrupt_phrases=["wait"])
        )
    )
    request = await peer.request("session.update")
    assert client.config.speed is None
    assert request["session"]["speed"] == 0.9
    applied = {
        "speed": 0.9,
        "version": "build-test",
        "request_id": "request-test",
    }
    await peer.send(
        {
            "type": "session.updated",
            "session": applied,
            "prism_request_event_id": request["event_id"],
            "server_time_ms": 123,
        }
    )
    assert await update == applied
    assert client.config.speed == 0.9
    rejected = asyncio.create_task(
        client.update_session(SessionSettingsUpdate(speed=1.1))
    )
    request = await peer.request("session.update")
    await peer.send(
        {
            "type": "error",
            "error": {
                "scope": "event",
                "code": "invalid_session",
                "event_id": request["event_id"],
            },
        }
    )
    with pytest.raises(PrismError, match="refused"):
        await rejected
    assert client.config.speed == 0.9 and client.ready
    assert client.session_info == applied


@pytest.mark.asyncio
async def test_cumulative_transcript_corrections_and_event_identity(
    peer, ten_modules
):
    environment = ten_modules.environment(
        {
            "endpoint": peer.url,
            "api_key": "test-only",
            "emit_server_events": True,
        }
    )
    provider = ten_modules.extension.RimeRealtimeExtension("v2v")
    await provider.on_init(environment)
    try:
        await provider.on_start(environment)
        await next_data(environment, "mllm_server_session_ready")
        for text, delta, final in [
            ("Book four", "Book four", False),
            ("Book five", "", False),
            ("Book five seats", "", True),
        ]:
            await peer.send(
                {
                    "type": "conversation.item.input_audio_transcription."
                    + ("completed" if final else "delta"),
                    "item_id": "caller-turn",
                    "event_id": "evt-words",
                    "server_time_ms": 123,
                    "transcript": text,
                    "delta": delta,
                }
            )
            data = await next_data(environment, "mllm_server_input_transcript")
            assert data["content"] == text and data["final"] == final
            assert data["metadata"]["item_id"] == "caller-turn"
            assert data["metadata"]["server_time_ms"] == 123
    finally:
        await provider.on_stop(environment)


@pytest.mark.asyncio
async def test_session_version_recording_and_reply_endings_are_exposed(
    peer, ten_modules
):
    environment = ten_modules.environment(
        {
            "endpoint": peer.url,
            "api_key": "test-only",
            "emit_server_events": True,
        }
    )
    provider = ten_modules.extension.RimeRealtimeExtension("v2v")
    await provider.on_init(environment)
    peer.auto_initialize = False
    try:
        started = asyncio.create_task(provider.on_start(environment))
        first = await peer.request("session.update")
        session = {
            "version": "release-test",
            "request_id": "request-test",
            "prism_effective_recording": [],
        }
        await peer.send(
            {
                "type": "session.updated",
                "prism_request_event_id": first["event_id"],
                "session": session,
                "server_time_ms": 10,
            }
        )
        second = await peer.request("session.update")
        await peer.send(
            {
                "type": "session.updated",
                "prism_request_event_id": second["event_id"],
                "session": session,
                "server_time_ms": 11,
            }
        )
        await started
        ready = await next_data(environment, "mllm_server_session_ready")
        assert ready["metadata"]["version"] == "release-test"
        assert ready["metadata"]["session"]["prism_effective_recording"] == []
        await peer.send(
            {"type": "response.created", "response": {"id": "reply"}}
        )
        done = {
            "type": "response.done",
            "event_id": "evt-done",
            "server_time_ms": 12,
            "response": {
                "id": "reply",
                "status": "completed",
                "status_details": {"reason": "audio_cut"},
            },
        }
        await peer.send(done)
        while True:
            event = (await next_data(environment, "prism_server_event"))[
                "event"
            ]
            if event["type"] == "response.done":
                assert event == done
                break
    finally:
        await provider.on_stop(environment)


@pytest.mark.asyncio
async def test_ten_command_updates_and_manual_controls(peer, ten_modules):
    environment = ten_modules.environment(
        {
            "endpoint": peer.url,
            "api_key": "test-only",
            "manual_turns": True,
            "interrupt_response": False,
        }
    )
    provider = ten_modules.extension.RimeRealtimeExtension("v2v")
    await provider.on_init(environment)
    try:
        await provider.on_start(environment)
        await next_data(environment, "mllm_server_session_ready")
        for command_name, wire_name in [
            ("prism_input_audio_start", "prism.input_audio_buffer.start"),
            ("prism_input_audio_commit", "input_audio_buffer.commit"),
            ("prism_create_response", "response.create"),
        ]:
            await provider.on_cmd(
                environment, ten_modules.message(command_name)
            )
            result = await asyncio.wait_for(environment.results.get(), 2)
            assert result.get_status_code() == 0
            request = await peer.request(wire_name)
            if wire_name == "response.create":
                assert request["response"] == {}
        await peer.send(
            {
                "type": "input_audio_buffer.speech_started",
                "item_id": "manual-turn",
                "server_time_ms": 13,
            }
        )
        assert (await next_data(environment, "mllm_server_interrupted"))[
            "metadata"
        ]["item_id"] == "manual-turn"
        await provider.on_cmd(
            environment,
            ten_modules.message(
                "prism_update_session", {"settings": {"speed": 0.9}}
            ),
        )
        assert (await environment.results.get()).get_status_code() == 0
        await provider.on_cmd(
            environment,
            ten_modules.message(
                "prism_update_session", {"settings": {"manual_turns": False}}
            ),
        )
        assert (await environment.results.get()).get_status_code() == 1
    finally:
        await provider.on_stop(environment)


def test_manifest_declares_configuration_commands_and_diagnostics():
    manifest = json.loads(
        (Path(__file__).parents[1] / "manifest.json").read_text()
    )
    assert set(RimeRealtimeConfig.model_fields) <= set(
        manifest["api"]["property"]["properties"]
    )
    assert "prism_update_session" in {
        item["name"] for item in manifest["api"]["cmd_in"]
    }
    assert manifest["api"]["cmd_out"][0]["name"] == "prism_review"


@pytest.mark.asyncio
async def test_long_typed_text_and_history_reach_the_server_and_its_limits_apply(
    peer, ten_modules
):
    environment = ten_modules.environment(
        {"endpoint": peer.url, "api_key": "test-only"}
    )
    provider = ten_modules.extension.RimeRealtimeExtension("v2v")
    await provider.on_init(environment)
    try:
        await provider.on_start(environment)
        await next_data(environment, "mllm_server_session_ready")
        text = "x" * 5001
        history = asyncio.create_task(
            provider.send_client_message_item(
                type("Message", (), {"role": "assistant", "content": text})()
            )
        )
        added = await peer.request("conversation.item.create")
        assert added["item"]["content"][0]["text"] == text
        await peer.acknowledge(added, "conversation.item.created")
        await history
        await provider.connection.trigger(text)
        await peer.send({"type": "prism.typed_input.ready"})
        requested = await peer.request("response.create")
        assert requested["response"]["prism_input_text"] == text
        await peer.send(
            {
                "type": "error",
                "error": {
                    "scope": "event",
                    "code": "invalid_typed_input",
                    "event_id": requested["event_id"],
                },
            }
        )
        error = await next_data(environment, "error")
        assert error["vendor_info"]["code"] == "invalid_typed_input"
        assert provider.is_connected()
    finally:
        await provider.on_stop(environment)


def test_empty_command_schemas_satisfy_native_runtime_property_parser():
    """TEN's native property parser requires properties even for no-argument commands."""
    manifest = json.loads(
        (Path(__file__).parents[1] / "manifest.json").read_text()
    )
    for command in manifest["api"]["cmd_in"]:
        assert isinstance(command["property"].get("properties"), dict)
        assert isinstance(command["result"]["property"].get("properties"), dict)
