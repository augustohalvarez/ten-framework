"""Call shutdown and caption lifecycle across the example graph."""

import asyncio
import base64
import json
from pathlib import Path

import pytest

from test_extension import next_data


def test_prism_graph_routes_provider_errors():
    path = (
        Path(__file__).resolve().parents[4]
        / "examples/voice-assistant-realtime/tenapp/property.json"
    )
    graph = next(
        item
        for item in json.loads(path.read_text())["ten"]["predefined_graphs"]
        if item["name"] == "voice_assistant_prism"
    )
    routes = next(
        item
        for item in graph["graph"]["connections"]
        if item["extension"] == "main_control"
    )
    assert any("error" in route.get("names", []) for route in routes["data"])


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [-1000, 1000])
async def test_provider_error_closes_only_fatal_calls(ten_modules, code):
    environment = ten_modules.environment({"greeting": ""})
    controller = ten_modules.controller.MainControlExtension("main")
    await controller.on_init(environment)
    try:
        await controller.on_data(
            environment,
            ten_modules.message(
                "error", {"code": code, "message": "provider unavailable"}
            ),
        )
        if code < 0:
            assert (
                await asyncio.wait_for(environment.commands.get(), 2)
            ).name == "flush"
            assert (
                await asyncio.wait_for(environment.commands.get(), 2)
            ).name == "ten:close_app"
            await asyncio.wait_for(controller._event_task, 2)
            flush = await next_data(environment, "flush")
            assert flush["preserve_final"]
        else:
            await controller.agent.event_queue.put(
                ten_modules.events.ServerInterruptEvent(metadata={})
            )
            assert (
                await asyncio.wait_for(environment.commands.get(), 2)
            ).name == "flush"
            assert not controller._event_task.done()
        assert not any(
            "Unhandled data: error" in line for line in environment.logs
        )
    finally:
        await controller.on_stop(environment)


@pytest.mark.asyncio
async def test_interruption_closes_partial_caption_once_and_filters_late_output(
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
        await peer.send({"type": "response.created", "response": {"id": "old"}})
        await peer.send(
            {
                "type": "response.text.delta",
                "response_id": "old",
                "delta": "A story",
            }
        )
        assert not (
            await next_data(environment, "mllm_server_output_transcript")
        )["final"]
        await peer.send({"type": "input_audio_buffer.speech_started"})
        await next_data(environment, "mllm_server_interrupted")
        closed = await next_data(environment, "mllm_server_output_transcript")
        assert closed["final"] and closed["content"] == "A story"
        assert closed["metadata"]["response_id"] == "old"
        assert closed["metadata"]["interrupted"]
        for event in [
            {
                "type": "response.text.done",
                "response_id": "old",
                "text": "Unheard replacement",
            },
            {
                "type": "response.audio.delta",
                "response_id": "old",
                "delta": base64.b64encode(b"\1\0").decode(),
            },
            {"type": "input_audio_buffer.speech_started"},
            {"type": "response.created", "response": {"id": "new"}},
            {
                "type": "response.text.done",
                "response_id": "new",
                "text": "Four.",
            },
        ]:
            await peer.send(event)
        await next_data(environment, "mllm_server_interrupted")
        new = await next_data(environment, "mllm_server_output_transcript")
        assert new["content"] == "Four." and new["final"]
        assert environment.data.empty() and environment.audio.empty()
    finally:
        await provider.on_stop(environment)
