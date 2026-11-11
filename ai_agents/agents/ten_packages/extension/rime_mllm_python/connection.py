"""Prism WebSocket session, readiness, and acknowledged tool continuations."""

import asyncio
import base64
import json
import uuid
from collections import deque
from typing import Awaitable, Callable

import aiohttp

from .config import RimeRealtimeConfig
from .conversation import Conversation


class PrismError(Exception):
    """A sanitized integration failure with a stable machine-readable code."""

    def __init__(self, code: str, message: str, fatal: bool = False):
        super().__init__(message)
        self.code = code
        self.fatal = fatal


class PrismConnection:
    """One call; only failed startup attempts may reconnect automatically."""

    def __init__(
        self,
        config: RimeRealtimeConfig,
        on_event: Callable[[dict], Awaitable[None]],
    ):
        self.config = config
        self.on_event = on_event
        self.conversation = Conversation()
        self.tools: list[dict] = []
        self.ready = False
        self.stopping = False
        self._session = None
        self._socket = None
        self._receiver = None
        self._initialized = None
        self._setup_id = None
        self._write_lock = asyncio.Lock()
        self._settings_lock = asyncio.Lock()
        self._waiters: dict[str, asyncio.Future] = {}
        self._typed_ready = False
        self._triggers: deque[dict] = deque()
        self._trigger_inflight = None

    @staticmethod
    def event_id() -> str:
        """Identify a request for correlation, never for implicit replay."""
        return "evt_" + uuid.uuid4().hex

    async def start(self) -> None:
        """Initialize within a finite budget, retrying only transient startup."""
        if not self.config.api_key.get_secret_value():
            raise PrismError("missing_api_key", "Rime API key is required")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.config.startup_timeout
        for attempt in range(self.config.startup_retries + 1):
            self._initialized = loop.create_future()
            try:
                remaining = deadline - loop.time()
                if remaining <= 0 or self.stopping:
                    raise asyncio.TimeoutError()
                self._session = aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=None, sock_connect=5)
                )
                self._socket = await asyncio.wait_for(
                    self._session.ws_connect(
                        self.config.endpoint,
                        headers={
                            "Authorization": "Bearer "
                            + self.config.api_key.get_secret_value()
                        },
                        heartbeat=15,
                        max_msg_size=1024 * 1024,
                    ),
                    timeout=remaining,
                )
                self._receiver = asyncio.create_task(self._receive())
                await asyncio.wait_for(
                    asyncio.shield(self._initialized),
                    timeout=max(0, deadline - loop.time()),
                )
                return
            except aiohttp.ClientResponseError as error:
                retry = error.status in {408, 429, 500, 502, 503, 504}
                failure = PrismError(
                    str(error.status),
                    f"Prism refused the connection (HTTP {error.status})",
                )
            except (aiohttp.ClientConnectionError, asyncio.TimeoutError):
                retry = True
                failure = PrismError("startup_timeout", "Prism startup failed")
            except PrismError as error:
                retry = error.code == "connection_closed"
                failure = error
            except BaseException:
                await self._close_transport()
                raise
            await self._close_transport()
            delay = min(0.25 * (2**attempt), 1)
            if (
                not retry
                or attempt == self.config.startup_retries
                or loop.time() + delay >= deadline
                or self.stopping
            ):
                raise failure
            await asyncio.sleep(delay)

    async def send(self, payload: dict) -> str:
        """Serialize writes and return the event identity used on the wire."""
        request = dict(payload)
        request.setdefault("event_id", self.event_id())
        async with self._write_lock:
            if self._socket is None or self._socket.closed or self.stopping:
                raise PrismError(
                    "connection_closed", "Prism is disconnected", fatal=True
                )
            try:
                await self._socket.send_json(request)
            except (aiohttp.ClientError, OSError) as error:
                raise PrismError(
                    "connection_closed", "Prism is disconnected", fatal=True
                ) from error
        return request["event_id"]

    async def acknowledged_request(self, payload: dict) -> dict:
        """Wait for a matching acknowledgment without blocking the reader."""
        event_id = payload.get("event_id", self.event_id())
        future = asyncio.get_running_loop().create_future()
        self._waiters[event_id] = future
        try:
            await self.send({**payload, "event_id": event_id})
            return await asyncio.wait_for(future, self.config.startup_timeout)
        except asyncio.TimeoutError as error:
            raise PrismError(
                "acknowledgment_timeout",
                "Prism request acknowledgment did not arrive; start a new call",
                fatal=True,
            ) from error
        finally:
            self._waiters.pop(event_id, None)

    async def update_tools(self, tools: list[dict]) -> None:
        """Apply complete tool lists in order, including registration at startup."""
        async with self._settings_lock:
            self.tools = tools
            if self.ready:
                await self.acknowledged_request(
                    {
                        "type": "session.update",
                        "session": self.config.session_settings(self.tools),
                    }
                )

    async def send_audio(self, audio: bytes) -> None:
        """Append headerless 16 kHz mono PCM16, including silent frames."""
        if not self.ready:
            raise PrismError("not_ready", "Prism session is not initialized")
        await self.send(
            {
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(audio).decode("ascii"),
            }
        )

    async def trigger(self, text: str | None) -> None:
        """Queue one typed turn or proactive reply until Prism becomes idle."""
        if len(self._triggers) >= 8:
            raise PrismError("trigger_queue_full", "Too many pending replies")
        request = {"metadata": {"prism_cause": "proactive"}}
        if text is not None:
            request = {
                "metadata": {"prism_cause": "user_text"},
                "prism_input_text": text,
            }
        self._triggers.append(request)
        await self._send_trigger()

    async def _send_trigger(self) -> None:
        if (
            self.ready
            and self._typed_ready
            and self._triggers
            and self._trigger_inflight is None
        ):
            request = self._triggers.popleft()
            event_id = self.event_id()
            self._typed_ready = False
            self._trigger_inflight = (event_id, request)
            await self.send(
                {
                    "type": "response.create",
                    "event_id": event_id,
                    "response": request,
                }
            )

    async def tool_result(self, call_id: str, output: str) -> None:
        """Submit once; continuation waits for its own result acknowledgment."""
        event_id = self.event_id()
        self.conversation.result_sent(event_id, call_id)
        await self.acknowledged_request(
            {
                "type": "conversation.item.create",
                "event_id": event_id,
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": output,
                },
            }
        )

    async def _dispatch(self, event: dict) -> None:
        kind = event["type"]
        request_id = event.get("prism_request_event_id")
        if kind == "error":
            error = event["error"]
            request_id = error.get("event_id")
            failure = PrismError(
                error.get("code", "vendor_error"), "Prism refused a request"
            )
            if error.get("scope") == "session" or (
                request_id == self._setup_id and not self.ready
            ):
                raise failure
            result_refused = self.conversation.result_refused(request_id)
            failure.fatal = result_refused
            waiter = self._waiters.get(request_id)
            if waiter is not None and not waiter.done():
                waiter.set_exception(failure)
            if self._trigger_inflight and (
                request_id == self._trigger_inflight[0]
                or error.get("code") == "typed_turn_superseded"
            ):
                _, request = self._trigger_inflight
                self._trigger_inflight = None
                if error.get("code") == "typed_turn_busy":
                    self._triggers.appendleft(request)
                    self._typed_ready = False
            if error.get("scope") == "tool_roundtrip":
                self.conversation.close_round(error["owner"]["response_id"])
            if result_refused:
                raise failure
            await self.on_event(event)
            return
        waiter = self._waiters.get(request_id)
        if waiter is not None and not waiter.done():
            waiter.set_result(event)
        if kind == "session.created":
            if self._setup_id is not None:
                raise PrismError("protocol_error", "Duplicate session.created")
            self._setup_id = self.event_id()
            await self.send(
                {
                    "type": "session.update",
                    "event_id": self._setup_id,
                    "session": self.config.session_settings(self.tools),
                }
            )
        elif kind == "session.updated" and not self.ready:
            if request_id != self._setup_id:
                raise PrismError("protocol_error", "Unmatched session update")
            self.ready = True
            self._initialized.set_result(None)
        elif kind == "prism.typed_input.ready":
            self._typed_ready = True
        elif kind == "response.created":
            self.conversation.created(event["response"]["id"])
            self._typed_ready = False
            if (
                self._trigger_inflight
                and request_id == self._trigger_inflight[0]
            ):
                self._trigger_inflight = None
        elif kind == "input_audio_buffer.speech_started":
            self.conversation.interrupt()
            self._typed_ready = False
        elif kind == "response.function_call_arguments.done":
            if not self.conversation.function_call(
                event["response_id"], event["call_id"]
            ):
                return
        elif kind == "conversation.item.created":
            self.conversation.acknowledged(request_id)
        elif kind == "response.done":
            response = event["response"]
            self.conversation.done(response["id"], response["status"])
        await self.on_event(event)
        for parent in self.conversation.continuations():
            await self.send(
                {
                    "type": "response.create",
                    "response": {
                        "metadata": {
                            "prism_cause": "tool_continuation",
                            "prism_parent_response_id": parent,
                        }
                    },
                }
            )
        await self._send_trigger()

    async def _receive(self) -> None:
        try:
            async for message in self._socket:
                if message.type != aiohttp.WSMsgType.TEXT:
                    raise PrismError("protocol_error", "Expected a JSON event")
                event = json.loads(message.data)
                if not isinstance(event, dict) or not isinstance(
                    event.get("type"), str
                ):
                    raise ValueError("event needs a string type")
                await self._dispatch(event)
            raise PrismError(
                "connection_closed", "Prism ended the conversation"
            )
        except Exception as error:
            if isinstance(error, PrismError):
                failure = error
            elif isinstance(error, (aiohttp.ClientError, OSError)):
                failure = PrismError(
                    "connection_closed", "Prism is disconnected"
                )
            else:
                failure = PrismError(
                    "protocol_error", "Prism event processing failed"
                )
            if not self._initialized.done():
                self._initialized.set_exception(failure)
            elif not self.stopping:
                await self.on_event(
                    {
                        "type": "error",
                        "error": {
                            "code": failure.code,
                            "message": str(failure),
                            "scope": "session",
                        },
                    }
                )
        finally:
            self.ready = False
            for future in self._waiters.values():
                if not future.done():
                    future.set_exception(
                        PrismError(
                            "connection_closed",
                            "Prism is disconnected",
                            fatal=True,
                        )
                    )
            if self._socket is not None:
                await self._socket.close()
            if self._session is not None:
                await self._session.close()

    async def _close_transport(self) -> None:
        if self._receiver is not None:
            if self._receiver is not asyncio.current_task():
                self._receiver.cancel()
                await asyncio.gather(self._receiver, return_exceptions=True)
            self._receiver = None
        if self._socket is not None:
            await self._socket.close()
        if self._session is not None:
            await self._session.close()
        if self._initialized is not None and not self._initialized.done():
            self._initialized.cancel()
        self._setup_id = None
        self.ready = False
        self._typed_ready = False

    async def close(self) -> None:
        """End the call and join its receive task without replaying requests."""
        self.stopping = True
        await self._close_transport()
