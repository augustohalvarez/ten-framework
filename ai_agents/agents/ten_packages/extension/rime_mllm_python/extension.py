"""Translate TEN's MLLM interface to one Prism speech-to-speech call."""

import asyncio
import base64
import json

from ten_runtime import Cmd, CmdResult, Data, Loc, StatusCode

from ten_ai_base.message import (
    ModuleError,
    ModuleErrorCode,
    ModuleErrorVendorInfo,
)
from ten_ai_base.mllm import (
    AsyncMLLMBaseExtension,
    DATA_MLLM_IN_SET_MESSAGE_CONTEXT,
)
from ten_ai_base.struct import (
    MLLMServerFunctionCall,
    MLLMServerInputTranscript,
    MLLMServerInterrupt,
    MLLMServerOutputTranscript,
    MLLMServerSessionReady,
)

from .audio import AudioBufferOverflow, InputAudio
from .config import (
    CHANNELS,
    INPUT_FRAME_SECONDS,
    INPUT_SAMPLE_RATE,
    OUTPUT_SAMPLE_RATE,
    SAMPLE_WIDTH,
    RimeRealtimeConfig,
    SessionSettingsUpdate,
)
from .connection import PrismConnection, PrismError
from .controls import ProviderCommand, ReviewDecision, ReviewRequest


class RimeRealtimeExtension(AsyncMLLMBaseExtension):
    """Own the call's input task and reuse TEN's standard output helpers."""

    def __init__(self, name):
        super().__init__(name)
        self.config = None
        self.connection = None
        self.input_audio = InputAudio()
        self._audio_sender = None
        self._pending_text = None
        self._tools = {}
        self._review_tasks: dict[str, tuple[str, asyncio.Task]] = {}

    async def on_init(self, ten_env):
        # Input is clocked here; the base's detached queue consumer is unused.
        self.ten_env = ten_env
        properties, error = await ten_env.get_property_to_json(None)
        try:
            if error:
                raise ValueError("cannot read extension properties")
            self.config = RimeRealtimeConfig.model_validate_json(properties)
            ten_env.log_info(f"config: {self.config.model_dump_json()}")
            self.connection = PrismConnection(self.config, self._on_event)
        except ValueError:
            await self._report(
                "invalid_config", "Invalid Rime configuration", True
            )

    async def on_start(self, ten_env):
        if self.connection is not None:
            try:
                await self.start_connection()
            except Exception as error:
                server_event = getattr(error, "event", None)
                if server_event:
                    await self._publish_event(server_event)
                await self._report(
                    getattr(error, "code", "startup_failed"),
                    "Prism session could not start",
                    True,
                )

    async def start_connection(self):
        await self.connection.start()
        for message in self.message_context:
            await self._add_history(message)
        await self.connection.update_tools(list(self._tools.values()))
        if not self.is_connected():
            raise PrismError(
                "connection_closed",
                "Prism ended during initialization",
                fatal=True,
            )
        self._audio_sender = asyncio.create_task(self._send_input())
        await self.send_server_session_ready(
            MLLMServerSessionReady(
                metadata={
                    **self._event_metadata(self.connection.session_event),
                    "version": self.connection.session_info.get("version"),
                    "request_id": self.connection.session_info.get(
                        "request_id"
                    ),
                    "session": self.connection.session_info,
                }
            )
        )

    async def on_stop(self, ten_env):
        self.stopped = True
        await self.stop_connection()

    async def stop_connection(self):
        self.input_audio.buffer.clear()
        await self._cancel_reviews()
        if self._audio_sender is not None:
            if self._audio_sender is not asyncio.current_task():
                self._audio_sender.cancel()
                await asyncio.gather(self._audio_sender, return_exceptions=True)
            self._audio_sender = None
        if self.connection is not None:
            await self.connection.close()

    def is_connected(self):
        return (
            self.connection is not None
            and self.connection.ready
            and not self.stopped
        )

    def vendor(self):
        return "rime"

    def input_audio_sample_rate(self):
        return INPUT_SAMPLE_RATE

    def synthesize_audio_sample_rate(self):
        return OUTPUT_SAMPLE_RATE

    async def on_audio_frame(self, ten_env, audio_frame):
        if not self.is_connected():
            return
        try:
            await self.send_audio(audio_frame, self.session_id)
        except AudioBufferOverflow:
            await self._report("audio_backlog", "Prism input fell behind", True)
        except ValueError:
            await self._report(
                "invalid_audio", "Expected live 16 kHz mono PCM16", False
            )

    async def send_audio(self, frame, session_id):
        if (
            frame.get_sample_rate() != INPUT_SAMPLE_RATE
            or frame.get_number_of_channels() != CHANNELS
            or frame.get_bytes_per_sample() != SAMPLE_WIDTH
        ):
            raise ValueError("incorrect input audio format")
        metadata, _ = frame.get_property_to_json("metadata")
        if metadata:
            properties = json.loads(metadata)
            if not isinstance(properties, dict):
                raise ValueError("audio metadata must be an object")
            self.session_id = properties.get("session_id", session_id)
        self.input_audio.append(bytes(frame.get_buf()))
        return True

    async def _send_input(self):
        loop = asyncio.get_running_loop()
        deadline = loop.time()
        try:
            while self.is_connected():
                await self.connection.send_audio(self.input_audio.frame())
                deadline += INPUT_FRAME_SECONDS
                if deadline < loop.time():
                    deadline = loop.time() + INPUT_FRAME_SECONDS
                await asyncio.sleep(max(0, deadline - loop.time()))
        except (PrismError, OSError):
            await self._report("audio_send_failed", "Prism input stopped", True)

    async def on_data(self, ten_env, data):
        if self.connection is None or self.stopped:
            return
        try:
            if (
                data.get_name() == DATA_MLLM_IN_SET_MESSAGE_CONTEXT
                and self.connection.ready
            ):
                raise PrismError(
                    "unsupported_context_update",
                    "Live history replacement is not supported; start a new call",
                )
            await super().on_data(ten_env, data)
        except (PrismError, ValueError, asyncio.TimeoutError) as error:
            await self._report(
                getattr(error, "code", "invalid_request"),
                "Rime request could not be applied",
                isinstance(error, PrismError) and error.fatal,
            )

    async def on_cmd(self, ten_env, command):
        """Apply typed controls; only session updates promise a server acknowledgment."""
        result = CmdResult.create(StatusCode.OK, command)
        try:
            kind = ProviderCommand(command.get_name())
            if not self.is_connected():
                raise PrismError(
                    "not_ready", "Prism session is not initialized"
                )
            encoded, error = command.get_property_to_json(None)
            if error:
                raise ValueError("cannot read command properties")
            payload = json.loads(encoded or "{}")
            if not isinstance(payload, dict):
                raise ValueError("command properties must be an object")
            if kind == ProviderCommand.UPDATE_SESSION:
                if set(payload) != {"settings"}:
                    raise ValueError("session updates require settings")
                updates = SessionSettingsUpdate.model_validate(
                    payload["settings"]
                )
                applied = await self.connection.update_session(updates)
                self.config = self.connection.config
                result.set_property_from_json("session", json.dumps(applied))
            else:
                if payload:
                    raise ValueError("this control accepts no properties")
                if kind == ProviderCommand.CREATE_RESPONSE:
                    await self.send_client_create_response()
                    result.set_property_from_json("accepted", "true")
                elif kind == ProviderCommand.INPUT_START:
                    if not self.config.manual_turns:
                        raise PrismError(
                            "manual_turns_required",
                            "Input start requires manual turns",
                        )
                    identity = await self.connection.send(
                        {"type": "prism.input_audio_buffer.start"}
                    )
                    result.set_property_from_json(
                        "event_id", json.dumps(identity)
                    )
                elif kind == ProviderCommand.INPUT_COMMIT:
                    identity = await self.connection.send(
                        {"type": "input_audio_buffer.commit"}
                    )
                    result.set_property_from_json(
                        "event_id", json.dumps(identity)
                    )
                else:
                    event = (
                        "input_audio_buffer.clear"
                        if kind == ProviderCommand.INPUT_CLEAR
                        else "response.cancel"
                    )
                    await self.connection.acknowledged_request({"type": event})
        except (ValueError, PrismError) as error:
            result = CmdResult.create(StatusCode.ERROR, command)
            result.set_property_from_json(
                "error",
                json.dumps(
                    {
                        "code": getattr(error, "code", "invalid_request"),
                        "message": "Prism control could not be applied",
                    }
                ),
            )
            if isinstance(error, PrismError) and error.fatal:
                await self._report(error.code, str(error), True)
        await ten_env.return_result(result)

    @staticmethod
    def _event_metadata(event):
        """Preserve server identities and send times without inventing playback times."""
        return {
            key: event[key]
            for key in (
                "event_id",
                "server_time_ms",
                "prism_request_event_id",
                "response_id",
                "item_id",
            )
            if key in event
        }

    async def _publish_event(self, event):
        """Opt-in backend diagnostics omit binary audio payloads."""
        if self.config is None or not self.config.emit_server_events:
            return
        snapshot = dict(event)
        if snapshot.get("type") == "response.audio.delta":
            snapshot.pop("delta", None)
        data = Data.create("prism_server_event")
        data.set_property_from_json(None, json.dumps({"event": snapshot}))
        await self.ten_env.send_data(data)

    async def _cancel_reviews(self, response_id=None):
        tasks = [
            task
            for owner, task in self._review_tasks.values()
            if response_id is None or owner == response_id
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _review_chunk(self, request):
        """The review extension runs independently of the provider event reader."""
        command = Cmd.create("prism_review")
        command.set_dests([Loc("", "", self.config.review_extension)])
        command.set_property_from_json(
            None, request.model_dump_json(exclude_none=True)
        )
        try:
            result, error = await asyncio.wait_for(
                self.ten_env.send_cmd(command),
                self.config.prism_review.timeout_ms / 1000,
            )
            if (
                error
                or result is None
                or result.get_status_code() != StatusCode.OK
            ):
                raise ValueError("review command failed")
            payload, error = result.get_property_to_json("decision")
            if error:
                raise ValueError("review result is missing")
            decision = ReviewDecision.model_validate_json(payload)
            if not self.connection.conversation.accepts_output(
                request.response_id
            ):
                return
            await self.connection.send(
                {
                    "type": "prism.review.result",
                    "review_id": request.review_id,
                    **decision.model_dump(mode="json", exclude_none=True),
                }
            )
        except (ValueError, PrismError, asyncio.TimeoutError):
            await self._report(
                "review_callback_failed",
                "Review decision unavailable; server timeout policy applies",
                False,
            )

    async def _add_history(self, item):
        content_type = "input_text" if item.role == "user" else "text"
        await self.connection.acknowledged_request(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": item.role,
                    "content": [{"type": content_type, "text": item.content}],
                },
            }
        )

    async def send_client_message_item(self, item, session_id=None):
        if item.role == "user":
            if self._pending_text is not None:
                raise PrismError(
                    "pending_text", "Trigger the pending text first"
                )
            self._pending_text = item.content
        else:
            await self._add_history(item)

    async def send_client_create_response(self, session_id=None):
        text = self._pending_text
        await self.connection.trigger(text)
        self._pending_text = None

    async def send_client_register_tool(self, tool):
        self._tools[tool.name] = {
            "type": "function",
            "name": tool.name,
            "description": tool.description,
            "parameters": {
                "type": "object",
                "properties": {
                    parameter.name: {
                        "type": parameter.type,
                        "description": parameter.description,
                    }
                    for parameter in tool.parameters
                },
                "required": [
                    parameter.name
                    for parameter in tool.parameters
                    if parameter.required
                ],
            },
        }
        await self.connection.update_tools(list(self._tools.values()))

    async def send_client_function_call_output(self, function_call_output):
        await self.connection.tool_result(
            function_call_output.call_id, function_call_output.output
        )

    async def _on_event(self, event):
        kind = event["type"]
        await self._publish_event(event)
        if kind == "prism.review.requested":
            request = ReviewRequest.model_validate(event)
            if (
                self.config.prism_review is None
                or self.config.review_extension is None
            ):
                raise PrismError(
                    "unexpected_review", "Review was not configured"
                )
            reply = self.connection.conversation.reply(request.response_id)
            if (
                not self.connection.conversation.accepts_output(
                    request.response_id
                )
                or request.review_id in reply.review_ids
            ):
                return
            reply.review_ids.add(request.review_id)
            if len(self._review_tasks) >= 64:
                await self._report(
                    "review_capacity",
                    "Too many pending reviews; server timeout policy applies",
                    False,
                )
                return
            task = asyncio.create_task(self._review_chunk(request))
            self._review_tasks[request.review_id] = (request.response_id, task)
            task.add_done_callback(
                lambda _task: self._review_tasks.pop(request.review_id, None)
            )
            return
        if kind == "response.done":
            await self._cancel_reviews(event["response"]["id"])
        if kind == "input_audio_buffer.speech_started":
            self.leftover_bytes = b""
            await self._cancel_reviews()
            await self.send_server_interrupted(
                MLLMServerInterrupt(metadata=self._event_metadata(event))
            )
            for (
                response_id,
                reply,
            ) in self.connection.conversation.replies.items():
                if reply.transcript and not reply.caption_final:
                    reply.caption_final = True
                    await self.send_server_output_text(
                        MLLMServerOutputTranscript(
                            content=reply.transcript,
                            delta="",
                            final=True,
                            metadata={
                                **self._event_metadata(event),
                                "response_id": response_id,
                                "interrupted": True,
                            },
                        )
                    )
        elif kind in {
            "conversation.item.input_audio_transcription.delta",
            "conversation.item.input_audio_transcription.completed",
            "conversation.item.input_audio_transcription.failed",
        }:
            transcript = event.get("transcript", "")
            final = kind != "conversation.item.input_audio_transcription.delta"
            await self.send_server_input_transcript(
                MLLMServerInputTranscript(
                    content=transcript,
                    delta=event.get("delta", transcript),
                    final=final,
                    metadata=self._event_metadata(event),
                )
            )
            if kind.endswith("failed"):
                await self._report(
                    "transcription_failed", "Caller transcript failed", False
                )
        elif kind in {
            "response.text.delta",
            "response.text.done",
            "response.audio.delta",
        }:
            response_id = event["response_id"]
            if not self.connection.conversation.accepts_output(response_id):
                return
            reply = self.connection.conversation.reply(response_id)
            if kind == "response.audio.delta":
                audio = base64.b64decode(event["delta"], validate=True)
                if len(audio) % SAMPLE_WIDTH:
                    raise ValueError("partial output PCM16 sample")
                await self.send_server_output_audio_data(audio)
            else:
                final = kind == "response.text.done"
                reply.caption_final = final
                delta = event.get("delta", "")
                reply.transcript = (
                    event["text"] if final else reply.transcript + delta
                )
                await self.send_server_output_text(
                    MLLMServerOutputTranscript(
                        content=reply.transcript,
                        delta=delta,
                        final=final,
                        metadata=self._event_metadata(event),
                    )
                )
        elif kind == "response.function_call_arguments.done":
            await self.send_server_function_call(
                MLLMServerFunctionCall(
                    call_id=event["call_id"],
                    name=event["name"],
                    arguments=event["arguments"],
                    metadata=self._event_metadata(event),
                )
            )
        elif kind == "error":
            error = event["error"]
            await self._report(
                error.get("code", "vendor_error"),
                (
                    "Prism reported a session error"
                    if error.get("scope") == "session"
                    else "Prism refused a request"
                ),
                error.get("scope") == "session"
                or self.connection.terminal_error is not None,
            )

    async def _report(self, code, message, fatal):
        if fatal:
            if self.stopped:
                return
            self.stopped = True
        logger = self.ten_env.log_error if fatal else self.ten_env.log_warn
        logger(f"Rime {code}: {message}")
        try:
            await self.send_mllm_error(
                ModuleError(
                    code=int(
                        ModuleErrorCode.FATAL_ERROR.value
                        if fatal
                        else ModuleErrorCode.NON_FATAL_ERROR.value
                    ),
                    message=message,
                ),
                ModuleErrorVendorInfo(
                    vendor=self.vendor(), code=code, message=message
                ),
            )
        finally:
            if fatal:
                await self.stop_connection()
