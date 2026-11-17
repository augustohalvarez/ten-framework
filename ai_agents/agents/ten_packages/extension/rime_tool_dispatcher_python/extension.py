"""Execute TEN tools without blocking the Prism graph's call controller."""

import asyncio
import json

from ten_runtime import AsyncExtension, Cmd, CmdResult, Data, Loc, StatusCode
from ten_ai_base.const import (
    CMD_PROPERTY_RESULT,
    CMD_PROPERTY_TOOL,
    CMD_TOOL_CALL,
    CMD_TOOL_REGISTER,
)
from ten_ai_base.mllm import (
    DATA_MLLM_IN_FUNCTION_CALL_OUTPUT,
    DATA_MLLM_IN_REGISTER_TOOL,
    DATA_MLLM_OUT_FUNCTION_CALL,
)
from ten_ai_base.struct import (
    MLLMClientFunctionCallOutput,
    MLLMClientRegisterTool,
    MLLMServerFunctionCall,
)
from ten_ai_base.types import LLMToolMetadata


class RimeToolDispatcherExtension(AsyncExtension):
    """Own registered destinations and concurrent tool tasks for one call."""

    def __init__(self, name):
        super().__init__(name)
        self.ten_env = None
        self.provider = None
        self.tools: dict[str, str] = {}
        self.tasks: set[asyncio.Task] = set()
        self.stopped = False

    async def on_init(self, ten_env):
        self.ten_env = ten_env
        provider, error = await ten_env.get_property_string(
            "provider_extension"
        )
        if error or not provider:
            raise ValueError("provider_extension is required")
        self.provider = Loc("", "", provider)

    async def on_cmd(self, ten_env, command):
        result = CmdResult.create(StatusCode.ERROR, command)
        if not self.stopped and command.get_name() == CMD_TOOL_REGISTER:
            try:
                encoded, error = command.get_property_to_json(CMD_PROPERTY_TOOL)
                if error:
                    raise ValueError("tool metadata is missing")
                tool = LLMToolMetadata.model_validate_json(encoded)
                source = command.get_source().extension_name
                if not source:
                    raise ValueError("tool extension is missing")
                previous = self.tools.get(tool.name)
                self.tools[tool.name] = source
                try:
                    await self._send_provider_data(
                        DATA_MLLM_IN_REGISTER_TOOL,
                        MLLMClientRegisterTool(tool=tool).model_dump(),
                    )
                except Exception:
                    if previous is None:
                        self.tools.pop(tool.name, None)
                    else:
                        self.tools[tool.name] = previous
                    raise
                result = CmdResult.create(StatusCode.OK, command)
                result.set_property_from_json("response", json.dumps(tool.name))
            except Exception:
                ten_env.log_warn("Tool registration failed")
        await ten_env.return_result(result)

    async def on_data(self, ten_env, data):
        if self.stopped or data.get_name() != DATA_MLLM_OUT_FUNCTION_CALL:
            return
        try:
            encoded, error = data.get_property_to_json(None)
            if error:
                raise ValueError("function call is missing")
            call = MLLMServerFunctionCall.model_validate_json(encoded)
        except ValueError:
            ten_env.log_warn("Invalid function call")
            return
        task = asyncio.create_task(self._execute(call))
        self.tasks.add(task)
        task.add_done_callback(self._finished)

    async def _execute(self, call):
        try:
            source = self.tools.get(call.name)
            if source is None:
                raise ValueError("tool is not registered")
            arguments = json.loads(call.arguments)
            if not isinstance(arguments, dict):
                raise ValueError("tool arguments must be an object")
            command = Cmd.create(CMD_TOOL_CALL)
            command.set_dests([Loc("", "", source)])
            command.set_property_from_json(
                None, json.dumps({"name": call.name, "arguments": arguments})
            )
            result, error = await self.ten_env.send_cmd(command)
            if (
                error
                or result is None
                or result.get_status_code() != StatusCode.OK
            ):
                raise ValueError("tool command failed")
            encoded, error = result.get_property_to_json(CMD_PROPERTY_RESULT)
            if error:
                raise ValueError("tool result is missing")
            payload = json.loads(encoded)
            if payload["type"] != "llmresult" or not isinstance(
                payload["content"], str
            ):
                raise ValueError("unsupported tool result")
            content = payload["content"]
        except Exception:
            self.ten_env.log_warn(
                "Tool execution failed; returning an error result"
            )
            content = json.dumps(
                {"error": "tool_failed", "message": "Tool execution failed"}
            )
        await self._send_provider_data(
            DATA_MLLM_IN_FUNCTION_CALL_OUTPUT,
            MLLMClientFunctionCallOutput(
                output=content, call_id=call.call_id
            ).model_dump(),
        )

    async def _send_provider_data(self, name, payload):
        data = Data.create(name)
        data.set_dests([self.provider])
        data.set_property_from_json(None, json.dumps(payload))
        error = await self.ten_env.send_data(data)
        if error:
            raise RuntimeError("provider delivery failed")

    def _finished(self, task):
        self.tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            self.ten_env.log_error("Tool result delivery failed")

    async def on_stop(self, _ten_env):
        self.stopped = True
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.tasks.clear()
