import asyncio
import json
from ten_ai_base.const import CMD_PROPERTY_RESULT
from ten_ai_base.mllm import (
    DATA_MLLM_IN_FUNCTION_CALL_OUTPUT,
    DATA_MLLM_IN_REGISTER_TOOL,
    DATA_MLLM_OUT_FUNCTION_CALL,
    DATA_MLLM_OUT_INTERRUPTED,
    DATA_MLLM_OUT_REQUEST_TRANSCRIPT,
    DATA_MLLM_OUT_RESPONSE_TRANSCRIPT,
    DATA_MLLM_OUT_SESSION_READY,
)
from ten_ai_base.struct import (
    MLLMClientFunctionCallOutput,
    MLLMClientRegisterTool,
    MLLMServerFunctionCall,
    MLLMServerInputTranscript,
    MLLMServerInterrupt,
    MLLMServerOutputTranscript,
    MLLMServerSessionReady,
)
from ..helper import _send_cmd, _send_data
from ten_runtime import AsyncTenEnv, Cmd, CmdResult, Data, StatusCode
from ten_ai_base.types import LLMToolMetadata, LLMToolResult
from .events import (
    AgentEvent,
    FunctionCallEvent,
    InputTranscriptEvent,
    OutputTranscriptEvent,
    ServerInterruptEvent,
    SessionReadyEvent,
    ToolRegisterEvent,
    UserJoinedEvent,
    UserLeftEvent,
)


class Agent:
    def __init__(self, ten_env: AsyncTenEnv):
        self.ten_env: AsyncTenEnv = ten_env
        self.stopped = False
        self.event_queue: asyncio.Queue[AgentEvent] = asyncio.Queue()
        self.tool_registry: dict[str, str] = {}

    async def on_cmd(self, cmd: Cmd):
        cmd_name = cmd.get_name()
        try:
            if cmd_name == "on_user_joined":
                event = UserJoinedEvent()
            elif cmd_name == "on_user_left":
                event = UserLeftEvent()
            elif cmd_name == "tool_register":
                tool_json, err = cmd.get_property_to_json("tool")
                if err:
                    raise RuntimeError(f"Invalid tool metadata: {err}")
                tool = LLMToolMetadata.model_validate_json(tool_json)
                event = ToolRegisterEvent(
                    tool=tool, source=cmd.get_source().extension_name
                )
            else:
                self.ten_env.log_warn(f"Unhandled cmd: {cmd_name}")
                return

            await self.event_queue.put(event)
            await self.ten_env.return_result(
                CmdResult.create(StatusCode.OK, cmd)
            )

        except Exception as e:
            self.ten_env.log_error(f"on_cmd error: {e}")
            await self.ten_env.return_result(
                CmdResult.create(StatusCode.ERROR, cmd)
            )

    async def on_data(self, data: Data):
        data_name = data.get_name()
        self.ten_env.log_info(f"on_data: {data_name}")
        try:
            if data_name == DATA_MLLM_OUT_REQUEST_TRANSCRIPT:
                transcript_json, _ = data.get_property_to_json(None)
                transcript = MLLMServerInputTranscript.model_validate_json(
                    transcript_json
                )
                event = InputTranscriptEvent(
                    delta=transcript.delta,
                    content=transcript.content,
                    metadata=transcript.metadata,
                    final=transcript.final,
                )
                await self.event_queue.put(event)
            elif data_name == DATA_MLLM_OUT_RESPONSE_TRANSCRIPT:
                response_json, _ = data.get_property_to_json(None)
                response = MLLMServerOutputTranscript.model_validate_json(
                    response_json
                )
                event = OutputTranscriptEvent(
                    delta=response.delta or "",
                    content=response.content,
                    metadata=response.metadata,
                    is_final=response.final,
                )
                await self.event_queue.put(event)
            elif data_name == DATA_MLLM_OUT_SESSION_READY:
                session_json, _ = data.get_property_to_json(None)
                session = MLLMServerSessionReady.model_validate_json(
                    session_json
                )
                event = SessionReadyEvent(metadata=session.metadata)
                await self.event_queue.put(event)
            elif data_name == DATA_MLLM_OUT_INTERRUPTED:
                interrupt_json, _ = data.get_property_to_json(None)
                interrupt = MLLMServerInterrupt.model_validate_json(
                    interrupt_json
                )
                event = ServerInterruptEvent(metadata=interrupt.metadata)
                await self.event_queue.put(event)
            elif data_name == DATA_MLLM_OUT_FUNCTION_CALL:
                function_call_json, _ = data.get_property_to_json(None)
                function_call = MLLMServerFunctionCall.model_validate_json(
                    function_call_json
                )
                event = FunctionCallEvent(
                    call_id=function_call.call_id,
                    function_name=function_call.name,
                    arguments=function_call.arguments,
                )
                await self.event_queue.put(event)
            else:
                self.ten_env.log_warn(f"Unhandled data: {data_name}")

        except Exception as e:
            self.ten_env.log_error(f"on_data error: {e}")

    async def get_event(self) -> AgentEvent:
        return await self.event_queue.get()

    async def register_tool(self, tool: LLMToolMetadata, source: str):
        """
        Register a tool with the agent.
        This method is typically called when a tool is registered by an extension.
        """
        self.ten_env.log_info(f"Registering tool: {tool.name} from {source}")
        self.tool_registry[tool.name] = source

        payload = MLLMClientRegisterTool(tool=tool).model_dump()
        await _send_data(
            self.ten_env,
            DATA_MLLM_IN_REGISTER_TOOL,
            "v2v",
            payload,
        )
        self.ten_env.log_info(
            f"[MainControlExtension] Registered tools: {tool.name} from {source}"
        )

    async def call_tool(self, tool_call_id: str, name: str, arguments: str):
        """
        Handle a tool call event.
        This method is typically called when the MLLM server makes a function call.
        """
        try:
            source = self.tool_registry.get(name)
            if source is None:
                raise ValueError("tool is not registered")
            parsed_arguments = json.loads(arguments)
            if not isinstance(parsed_arguments, dict):
                raise ValueError("tool arguments must be an object")
            result, error = await _send_cmd(
                self.ten_env,
                "tool_call",
                source,
                {"name": name, "arguments": parsed_arguments},
            )
            if (
                error
                or result is None
                or result.get_status_code() != StatusCode.OK
            ):
                raise ValueError("tool command failed")
            encoded, error = result.get_property_to_json(CMD_PROPERTY_RESULT)
            if error:
                raise ValueError("tool result is missing")
            tool_result: LLMToolResult = json.loads(encoded)
            if tool_result["type"] != "llmresult" or not isinstance(
                tool_result["content"], str
            ):
                raise ValueError("unsupported tool result")
            content = tool_result["content"]
        except Exception:
            self.ten_env.log_warn(
                "Tool execution failed; returning an error result"
            )
            content = json.dumps(
                {"error": "tool_failed", "message": "Tool execution failed"}
            )
        error = await _send_data(
            self.ten_env,
            DATA_MLLM_IN_FUNCTION_CALL_OUTPUT,
            "v2v",
            MLLMClientFunctionCallOutput(
                output=content, call_id=tool_call_id
            ).model_dump(),
        )
        if error:
            raise RuntimeError("tool result delivery failed")

    async def stop(self):
        """
        Stop the agent processing.
        This will stop the event queue and any ongoing tasks.
        """
        self.stopped = True
        # await self.llm_exec.stop()
        await self.event_queue.put(None)
