"""Prism tool routing, failures, and task ownership outside shared control."""

import asyncio
import json
from pathlib import Path

import pytest
import pytest_asyncio

from test_extension import next_data


@pytest_asyncio.fixture
async def tool_dispatcher(ten_modules):
    environment = ten_modules.environment(
        {"provider_extension": "v2v", "greeting": ""}
    )
    dispatcher = ten_modules.dispatcher.RimeToolDispatcherExtension("tools")
    await dispatcher.on_init(environment)
    try:
        yield dispatcher, environment
    finally:
        await dispatcher.on_stop(environment)


async def register_tool(ten_modules, dispatcher, environment):
    command = ten_modules.message(
        "tool_register",
        {
            "tool": {
                "name": "weather",
                "description": "Weather",
                "parameters": [],
            }
        },
    )
    command.source = "weather_tool"
    await dispatcher.on_cmd(environment, command)
    return await environment.results.get()


def function_call(ten_modules, call_id="call", arguments="{}"):
    return ten_modules.message(
        "mllm_server_function_call",
        {"call_id": call_id, "name": "weather", "arguments": arguments},
    )


def test_dispatcher_addon_registration(ten_modules):
    addon = ten_modules.registered["rime_tool_dispatcher_python"]()
    environment = ten_modules.environment({})
    addon.on_create_instance(environment, "tools", "context")
    instance, context = environment.created
    assert isinstance(
        instance, ten_modules.dispatcher.RimeToolDispatcherExtension
    )
    assert context == "context"


@pytest.mark.asyncio
async def test_registration_forwards_metadata_and_retains_source(
    ten_modules, tool_dispatcher
):
    dispatcher, environment = tool_dispatcher
    result = await register_tool(ten_modules, dispatcher, environment)
    assert result.get_status_code() == 0
    assert json.loads(result.get_property_to_json("response")[0]) == "weather"
    message = await environment.data.get()
    assert message.get_name() == "mllm_client_request_register_tool"
    assert message.payload["tool"]["name"] == "weather"
    assert message.destinations == [("", "", "v2v")]
    assert dispatcher.tools == {"weather": "weather_tool"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source,metadata",
    [
        ("", {}),
        ("weather_tool", {}),
        ("", {"name": "weather", "description": "Weather", "parameters": []}),
    ],
)
async def test_invalid_registration_is_refused(
    ten_modules, tool_dispatcher, source, metadata
):
    dispatcher, environment = tool_dispatcher
    command = ten_modules.message("tool_register", {"tool": metadata})
    command.source = source
    await dispatcher.on_cmd(environment, command)
    assert (await environment.results.get()).get_status_code() == 1
    assert not dispatcher.tools and environment.data.empty()


@pytest.mark.asyncio
async def test_registration_delivery_failure_rolls_back_destination(
    ten_modules, tool_dispatcher
):
    dispatcher, environment = tool_dispatcher
    dispatcher.tools["weather"] = "old_tool"

    async def failed(_data):
        return RuntimeError("delivery failed")

    environment.send_data = failed
    result = await register_tool(ten_modules, dispatcher, environment)
    assert result.get_status_code() == 1
    assert dispatcher.tools == {"weather": "old_tool"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "registered,arguments", [(False, "{}"), (True, "not-json"), (True, "[]")]
)
async def test_missing_or_invalid_tool_produces_correlated_failure(
    ten_modules, tool_dispatcher, registered, arguments
):
    dispatcher, environment = tool_dispatcher
    if registered:
        await register_tool(ten_modules, dispatcher, environment)
    await dispatcher.on_data(
        environment, function_call(ten_modules, arguments=arguments)
    )
    output = await next_data(environment, "mllm_client_function_call_output")
    assert output["call_id"] == "call"
    assert json.loads(output["output"])["error"] == "tool_failed"
    assert environment.commands.empty()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,content",
    [
        (0, {"type": "llmresult", "content": "sunny"}),
        (1, {}),
        (0, {"type": "llmresult", "content": 12}),
        (0, {}),
        (0, []),
    ],
)
async def test_tool_results_and_failures_preserve_identity_and_destination(
    ten_modules, tool_dispatcher, status, content
):
    dispatcher, environment = tool_dispatcher
    await register_tool(ten_modules, dispatcher, environment)

    async def completed(command):
        assert command.destinations == [("", "", "weather_tool")]
        assert command.payload == {
            "name": "weather",
            "arguments": {"city": "Paris"},
        }
        result = ten_modules.message(
            status, {ten_modules.agent.CMD_PROPERTY_RESULT: content}
        )
        result.get_status_code = lambda: status
        return result, None

    environment.send_cmd = completed
    await dispatcher.on_data(
        environment, function_call(ten_modules, arguments='{"city":"Paris"}')
    )
    output = await next_data(environment, "mllm_client_function_call_output")
    assert output["call_id"] == "call"
    if (
        status == 0
        and isinstance(content, dict)
        and isinstance(content.get("content"), str)
    ):
        assert output["output"] == "sunny"
    else:
        assert json.loads(output["output"])["error"] == "tool_failed"


@pytest.mark.asyncio
async def test_command_transport_failure_produces_a_tool_failure(
    ten_modules, tool_dispatcher
):
    dispatcher, environment = tool_dispatcher
    await register_tool(ten_modules, dispatcher, environment)

    async def failed(_command):
        return None, RuntimeError("transport unavailable")

    environment.send_cmd = failed
    await dispatcher.on_data(environment, function_call(ten_modules))
    output = await next_data(environment, "mllm_client_function_call_output")
    assert json.loads(output["output"])["error"] == "tool_failed"


@pytest.mark.asyncio
async def test_concurrent_tools_leave_shared_control_responsive_and_are_joined(
    ten_modules, tool_dispatcher
):
    dispatcher, environment = tool_dispatcher
    await register_tool(ten_modules, dispatcher, environment)
    controller = ten_modules.controller.MainControlExtension("main")
    await controller.on_init(environment)
    try:
        for call_id in ["first", "second"]:
            await dispatcher.on_data(
                environment, function_call(ten_modules, call_id)
            )
            assert (
                await asyncio.wait_for(environment.commands.get(), 2)
            ).name == "tool_call"
        tasks = list(dispatcher.tasks)
        assert len(tasks) == 2 and all(not task.done() for task in tasks)
        await controller.agent.event_queue.put(
            ten_modules.events.ServerInterruptEvent(metadata={})
        )
        assert (
            await asyncio.wait_for(environment.commands.get(), 2)
        ).name == "flush"
        assert not environment.tool_gate.is_set()
        await dispatcher.on_stop(environment)
        assert all(task.cancelled() for task in tasks)
        assert not dispatcher.tasks
        await dispatcher.on_data(
            environment, function_call(ten_modules, "after-stop")
        )
        assert not dispatcher.tasks and environment.commands.empty()
    finally:
        await controller.on_stop(environment)
    assert controller._event_task.done()


@pytest.mark.asyncio
async def test_result_delivery_failure_is_not_reclassified_as_a_tool_failure(
    ten_modules, tool_dispatcher
):
    dispatcher, environment = tool_dispatcher

    async def failed(_data):
        return RuntimeError("delivery unavailable")

    environment.send_data = failed
    await dispatcher.on_data(environment, function_call(ten_modules))
    tasks = list(dispatcher.tasks)
    results = await asyncio.gather(*tasks, return_exceptions=True)
    assert isinstance(results[0], RuntimeError)
    await asyncio.sleep(0)
    assert "Tool result delivery failed" in environment.logs


def test_only_prism_graph_dispatches_tools_outside_shared_control():
    path = (
        Path(__file__).resolve().parents[4]
        / "examples/voice-assistant-realtime/tenapp/property.json"
    )
    graphs = json.loads(path.read_text())["ten"]["predefined_graphs"]
    for item in graphs:
        prism = item["name"] == "voice_assistant_prism"
        graph = item["graph"]
        assert (
            any(
                node["addon"] == "rime_tool_dispatcher_python"
                for node in graph["nodes"]
            )
            == prism
        )
        main = next(
            route
            for route in graph["connections"]
            if route["extension"] == "main_control"
        )
        assert (
            any(
                "mllm_server_function_call" in route.get("names", [])
                for route in main["data"]
            )
            != prism
        )
        assert (
            any(
                "tool_register" in route.get("names", [])
                for route in main["cmd"]
            )
            != prism
        )
        if prism:
            dispatcher = next(
                route
                for route in graph["connections"]
                if route["extension"] == "tool_dispatcher"
            )
            assert dispatcher["data"][0]["source"] == [{"extension": "v2v"}]
            assert dispatcher["cmd"][0]["source"] == [
                {"extension": "weatherapi_tool_python"}
            ]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [0, 1])
async def test_shared_agent_keeps_its_existing_success_and_failure_behavior(
    ten_modules, status
):
    environment = ten_modules.environment({})
    agent = ten_modules.agent.Agent(environment)
    agent.tool_registry["weather"] = "weather_tool"

    async def completed(_command):
        result = ten_modules.message(
            status,
            {
                ten_modules.agent.CMD_PROPERTY_RESULT: {
                    "type": "llmresult",
                    "content": "sunny",
                }
            },
        )
        result.get_status_code = lambda: status
        return result, None

    environment.send_cmd = completed
    await agent.call_tool("call", "weather", "{}")
    if status == 0:
        output = await next_data(
            environment, "mllm_client_function_call_output"
        )
        assert output["call_id"] == "call" and output["output"] == "sunny"
    else:
        assert environment.data.empty()
