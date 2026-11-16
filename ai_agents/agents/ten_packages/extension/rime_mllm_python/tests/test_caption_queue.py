"""RTC caption queues stay bounded, current, and correctly framed."""

import asyncio
import base64
import json

import pytest


def caption(runtime, text, final=False, turn="reply"):
    return runtime.message(
        "message",
        {
            "data_type": "transcribe",
            "role": "assistant",
            "stream_id": 100,
            "turn_id": turn,
            "text": text,
            "is_final": final,
        },
    )


async def received(environment):
    parts = []
    identity = None
    while True:
        packet = await asyncio.wait_for(environment.data.get(), 2)
        name, index, total, data = packet.payload["data"].decode().split("|", 3)
        identity = identity or name
        assert identity == name
        assert int(index) == len(parts) + 1
        parts.append(data)
        if len(parts) == int(total):
            return json.loads(base64.b64decode("".join(parts)))


@pytest.mark.asyncio
async def test_coalescing_preserves_finals_and_complete_packet_groups(
    ten_modules,
):
    environment = ten_modules.environment({})
    collector = ten_modules.collector.MessageCollector2Extension("collector")
    for index in range(1000):
        await collector.on_data(
            environment, caption(ten_modules, f"draft {index}")
        )
    assert len(collector.pending) == 1
    long_text = "世界" * 600
    await collector.on_data(environment, caption(ten_modules, long_text, True))
    await collector.on_data(
        environment, caption(ten_modules, "Four.", True, "next")
    )
    await collector.on_init(environment)
    try:
        assert (await received(environment))["text"] == long_text
        assert (await received(environment))["text"] == "Four."
    finally:
        await collector.on_stop(environment)
    assert collector.sender is None


@pytest.mark.asyncio
async def test_interruption_discards_queued_partials_but_preserves_finals(
    ten_modules,
):
    environment = ten_modules.environment({})
    collector = ten_modules.collector.MessageCollector2Extension("collector")
    await collector.on_data(
        environment, caption(ten_modules, "Greeting", True, "greeting")
    )
    await collector.on_data(environment, caption(ten_modules, "Stale story"))
    await collector.on_data(
        environment, ten_modules.message("flush", {"preserve_final": True})
    )
    await collector.on_data(
        environment, caption(ten_modules, "Closed story", True)
    )
    await collector.on_data(
        environment, caption(ten_modules, "Four.", True, "next")
    )
    await collector.on_init(environment)
    try:
        assert [(await received(environment))["text"] for _ in range(3)] == [
            "Greeting",
            "Closed story",
            "Four.",
        ]
        assert environment.data.empty()
    finally:
        await collector.on_stop(environment)


@pytest.mark.asyncio
async def test_queue_capacity_and_flush_release_a_stale_blocked_partial(
    ten_modules,
):
    environment = ten_modules.environment({})
    collector = ten_modules.collector.MessageCollector2Extension("collector")
    for index in range(ten_modules.collector.MAX_PENDING_MESSAGES):
        await collector.on_data(
            environment, caption(ten_modules, str(index), True, str(index))
        )
    began = asyncio.Event()

    async def blocked():
        began.set()
        await collector.on_data(
            environment,
            caption(ten_modules, "Stale blocked partial", turn="blocked"),
        )

    pending = asyncio.create_task(blocked())
    await began.wait()
    assert not pending.done()
    assert len(collector.pending) == ten_modules.collector.MAX_PENDING_MESSAGES
    await collector.on_data(environment, ten_modules.message("flush", {}))
    await asyncio.wait_for(pending, 2)
    assert not collector.pending
