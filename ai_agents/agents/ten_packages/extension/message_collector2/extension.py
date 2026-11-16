#
#
# Agora Real Time Engagement
# Created by Wei Hu in 2024-08.
# Copyright (c) 2024 Agora IO. All rights reserved.
#
#
import asyncio
import json
import uuid
from collections import deque
from dataclasses import dataclass

from .helper import _text_to_base64_chunks
from ten_runtime import AsyncExtension, Data
from ten_runtime.async_ten_env import AsyncTenEnv

DATA_IN_MESSAGE = "message"
DATA_IN_FLUSH = "flush"
DATA_OUT_MESSAGE = "data"
MAX_PENDING_MESSAGES = 64


@dataclass
class PendingMessage:
    """One complete JSON message, before RTC packet framing."""

    content: str
    key: tuple | None
    final: bool


class MessageCollector2Extension(AsyncExtension):
    def __init__(self, name: str):
        super().__init__(name)
        self.pending: deque[PendingMessage] = deque()
        self.available = asyncio.Condition()
        self.stopped = False
        self.flush_generation = 0
        self.sender = None
        self.coalesce_transcripts = False

    async def on_init(self, ten_env: AsyncTenEnv) -> None:
        ten_env.log_info("on_init")
        await super().on_init(ten_env)

        properties, _ = await ten_env.get_property_to_json(None)
        self.coalesce_transcripts = json.loads(properties or "{}").get(
            "coalesce_transcripts", False
        )

        self.sender = asyncio.create_task(self._process_queue(ten_env))

    async def on_start(self, async_ten_env: AsyncTenEnv) -> None:
        async_ten_env.log_info("on_start")
        await super().on_start(async_ten_env)

    async def on_stop(self, async_ten_env: AsyncTenEnv) -> None:
        async_ten_env.log_info("on_stop")
        await super().on_stop(async_ten_env)
        self.stopped = True
        async with self.available:
            self.pending.clear()
            self.available.notify_all()
        if self.sender is not None:
            self.sender.cancel()
            await asyncio.gather(self.sender, return_exceptions=True)
            self.sender = None

    async def on_deinit(self, async_ten_env: AsyncTenEnv) -> None:
        async_ten_env.log_info("on_deinit")
        await super().on_deinit(async_ten_env)

    async def on_data(self, ten_env: AsyncTenEnv, data: Data) -> None:
        name = data.get_name()

        if name == DATA_IN_MESSAGE:
            message, _ = data.get_property_to_json(None)
            try:
                payload = json.loads(message)
                transcribe = payload.get("data_type") == "transcribe"
                key = (
                    (
                        payload.get("role"),
                        payload.get("stream_id"),
                        payload.get("turn_id"),
                    )
                    if transcribe
                    else None
                )
                await self._queue_message(
                    PendingMessage(
                        message, key, bool(payload.get("is_final", False))
                    )
                )

            except Exception as e:
                ten_env.log_warn(f"on_data new_data error: {e}")
        elif name == DATA_IN_FLUSH:
            ten_env.log_info("Received flush command")
            payload, _ = data.get_property_to_json(None)
            preserve_final = bool(
                json.loads(payload or "{}").get("preserve_final")
            )
            async with self.available:
                self.flush_generation += 1
                self.pending = deque(
                    message
                    for message in self.pending
                    if preserve_final and message.final
                )
                self.available.notify_all()
        else:
            ten_env.log_warn(f"Unknown data name: {name}")

    async def _queue_message(self, message: PendingMessage):
        generation = self.flush_generation
        async with self.available:
            if self.coalesce_transcripts and message.key is not None:
                for pending in reversed(self.pending):
                    if pending.key != message.key:
                        continue
                    if pending.final:
                        break
                    if not message.final:
                        pending.content = message.content
                        return
                    self.pending.remove(pending)
                    break
            await self.available.wait_for(
                lambda: self.stopped or len(self.pending) < MAX_PENDING_MESSAGES
            )
            if self.stopped or (
                not message.final and generation != self.flush_generation
            ):
                return
            self.pending.append(message)
            self.available.notify_all()

    async def _process_queue(self, ten_env: AsyncTenEnv):
        while not self.stopped:
            async with self.available:
                await self.available.wait_for(
                    lambda: self.stopped or self.pending
                )
                if self.stopped:
                    return
                message = self.pending.popleft()
                self.available.notify_all()
            chunks = _text_to_base64_chunks(
                ten_env, message.content, str(uuid.uuid4())[:8]
            )
            # Finish a message's packet group before sending another message.
            for chunk in chunks:
                ten_data = Data.create(DATA_OUT_MESSAGE)
                ten_data.set_property_buf("data", chunk.encode())
                await ten_env.send_data(ten_data)
                await asyncio.sleep(0.04)
