"""TEN interface tests use the real base helpers and a native-runtime double."""

import asyncio
import base64
import json

import pytest


def test_package_entrypoint_registers_and_creates_the_addon(ten_modules):
    assert set(ten_modules.registered) == {"rime_mllm_python"}
    addon = ten_modules.registered["rime_mllm_python"]()
    environment = ten_modules.environment({})
    addon.on_create_instance(environment, "v2v", "context")
    instance, context = environment.created
    assert isinstance(instance, ten_modules.extension.RimeRealtimeExtension)
    assert context == "context"


async def next_data(environment, name):
    while True:
        message = await asyncio.wait_for(environment.data.get(), 2)
        if message.get_name() == name:
            return message.payload


@pytest.mark.asyncio
async def test_pcm_captions_late_transcripts_and_interrupted_audio(
    peer, ten_modules
):
    environment = ten_modules.environment(
        {"endpoint": peer.url, "api_key": "test-only"}
    )
    extension = ten_modules.extension.RimeRealtimeExtension("v2v")
    await extension.on_init(environment)
    try:
        await extension.on_start(environment)
        await next_data(environment, "mllm_server_session_ready")
        await peer.send(
            {"type": "response.created", "response": {"id": "reply"}}
        )
        await peer.send(
            {
                "type": "response.text.delta",
                "response_id": "reply",
                "delta": "Hello",
            }
        )
        caption = await next_data(environment, "mllm_server_output_transcript")
        assert caption["content"] == "Hello" and not caption["final"]
        audio = base64.b64encode(b"\1\0" * 24).decode()
        await peer.send(
            {
                "type": "response.audio.delta",
                "response_id": "reply",
                "delta": audio,
            }
        )
        frame = await asyncio.wait_for(environment.audio.get(), 2)
        assert frame.get_sample_rate() == 24000
        assert frame.get_number_of_channels() == 1
        assert frame.get_bytes_per_sample() == 2
        assert bytes(frame.get_buf()) == b"\1\0" * 24
        await peer.send(
            {
                "type": "response.text.done",
                "response_id": "reply",
                "text": "Hello there",
            }
        )
        assert (await next_data(environment, "mllm_server_output_transcript"))[
            "final"
        ]
        await peer.send(
            {
                "type": "response.done",
                "response": {"id": "reply", "status": "completed"},
            }
        )
        await peer.send(
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "transcript": "A late transcript",
            }
        )
        transcript = await next_data(
            environment, "mllm_server_input_transcript"
        )
        assert (
            transcript["content"] == "A late transcript" and transcript["final"]
        )
        await peer.send({"type": "input_audio_buffer.speech_started"})
        await next_data(environment, "mllm_server_interrupted")
        await peer.send(
            {
                "type": "response.audio.delta",
                "response_id": "reply",
                "delta": audio,
            }
        )
        await peer.send(
            {"type": "response.created", "response": {"id": "next"}}
        )
        await peer.send(
            {
                "type": "response.audio.delta",
                "response_id": "next",
                "delta": base64.b64encode(b"\2\0").decode(),
            }
        )
        assert (
            bytes(
                (await asyncio.wait_for(environment.audio.get(), 2)).get_buf()
            )
            == b"\2\0"
        )
        assert environment.audio.empty()
        frame = ten_modules.message(
            "pcm_frame", {"metadata": {"session_id": "123"}}
        )
        frame.buffer = bytearray(b"\3\0" * 320)
        await extension.on_audio_frame(environment, frame)
        while True:
            request = await peer.request("input_audio_buffer.append")
            if base64.b64decode(request["audio"]) == bytes(frame.buffer):
                break
        frame.sample_rate = 24000
        await extension.on_audio_frame(environment, frame)
        error = await next_data(environment, "error")
        assert error["vendor_info"]["code"] == "invalid_audio"
        assert extension.is_connected()
        assert "test-only" not in "\n".join(environment.logs)
    finally:
        await extension.on_stop(environment)
    assert extension._audio_sender is None
    assert extension.connection._receiver is None
    assert not extension.is_connected()


@pytest.mark.asyncio
async def test_slow_tool_does_not_block_flush_and_shutdown_joins_tasks(
    ten_modules,
):
    environment = ten_modules.environment({"greeting": ""})
    controller = ten_modules.controller.MainControlExtension("main")
    await controller.on_init(environment)
    controller.agent.tool_registry["slow"] = "slow_tool"
    try:
        await controller.agent.event_queue.put(
            ten_modules.events.FunctionCallEvent(
                call_id="call", function_name="slow", arguments="{}"
            )
        )
        command = await asyncio.wait_for(environment.commands.get(), 2)
        assert command.name == "tool_call"
        await controller.agent.event_queue.put(
            ten_modules.events.ServerInterruptEvent(metadata={})
        )
        command = await asyncio.wait_for(environment.commands.get(), 2)
        assert command.name == "flush"
        assert not environment.tool_gate.is_set()
        tasks = list(controller._tool_tasks)
        assert tasks and any(not task.done() for task in tasks)
    finally:
        await controller.on_stop(environment)
    assert controller._event_task.done()
    assert all(task.done() for task in tasks)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "registered,arguments", [(False, "{}"), (True, "not-json"), (True, "[]")]
)
async def test_missing_or_invalid_tool_produces_a_result(
    ten_modules, registered, arguments
):
    environment = ten_modules.environment({})
    agent = ten_modules.agent.Agent(environment)
    if registered:
        agent.tool_registry["missing"] = "tool"
    await agent.call_tool("call", "missing", arguments)
    output = await next_data(environment, "mllm_client_function_call_output")
    assert output["call_id"] == "call"
    assert json.loads(output["output"])["error"] == "tool_failed"


@pytest.mark.asyncio
async def test_failed_rtc_flush_is_reported(ten_modules, monkeypatch):
    async def failed(*_args):
        return None, RuntimeError("flush refused")

    environment = ten_modules.environment({})
    controller = ten_modules.controller.MainControlExtension("main")
    controller.ten_env = environment
    monkeypatch.setattr(ten_modules.controller, "_send_cmd", failed)
    await controller._interrupt()
    assert any("RTC flush failed" in message for message in environment.logs)
    assert not any(
        "Interrupt signal sent" in message for message in environment.logs
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,content",
    [
        (0, {"type": "llmresult", "content": "sunny"}),
        (1, {}),
        (0, {"type": "llmresult", "content": 12}),
    ],
)
async def test_tool_success_and_failed_results(
    ten_modules, monkeypatch, status, content
):
    environment = ten_modules.environment({})
    agent = ten_modules.agent.Agent(environment)
    agent.tool_registry["weather"] = "tool"
    result = ten_modules.message(
        status, {ten_modules.agent.CMD_PROPERTY_RESULT: content}
    )
    result.get_status_code = lambda: status

    async def completed(*_args):
        return result, None

    monkeypatch.setattr(ten_modules.agent, "_send_cmd", completed)
    await agent.call_tool("call", "weather", "{}")
    output = await next_data(environment, "mllm_client_function_call_output")
    if status == 0 and isinstance(content.get("content"), str):
        assert output["output"] == "sunny"
    else:
        assert json.loads(output["output"])["error"] == "tool_failed"


@pytest.mark.asyncio
async def test_audio_backlog_is_fatal_and_clears_stale_input(peer, ten_modules):
    environment = ten_modules.environment(
        {"endpoint": peer.url, "api_key": "test-only"}
    )
    extension = ten_modules.extension.RimeRealtimeExtension("v2v")
    await extension.on_init(environment)
    await extension.on_start(environment)
    frame = ten_modules.message("pcm_frame")
    frame.buffer = bytearray(32002)
    await extension.on_audio_frame(environment, frame)
    failure = await next_data(environment, "error")
    assert failure["vendor_info"]["code"] == "audio_backlog"
    assert failure["code"] == -1000
    assert not extension.is_connected()
    assert not extension.input_audio.buffer
    assert extension._audio_sender is None
    await extension.on_stop(environment)


@pytest.mark.asyncio
@pytest.mark.parametrize("properties", [{}, {"endpoint": "ws://example.com"}])
async def test_invalid_configuration_and_missing_key_are_fatal(
    ten_modules, properties
):
    environment = ten_modules.environment(properties)
    extension = ten_modules.extension.RimeRealtimeExtension("v2v")
    await extension.on_init(environment)
    await extension.on_start(environment)
    failure = await next_data(environment, "error")
    assert failure["code"] == -1000
    assert not extension.is_connected()
    assert extension._audio_sender is None
    await extension.on_stop(environment)
