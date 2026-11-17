# Prism integration review guide

This change adds Rime Prism as a speech-to-speech provider in TEN's realtime
voice-assistant example. Prism handles speech recognition, turn detection,
reasoning, and spoken replies. TEN transports audio through Agora RTC, runs
application tools, and delivers captions.

Start with the example graph to understand the wiring, then read the new
provider, the controller changes, and the shared caption collector. The sections
below follow that order and identify the tests for each area.

## 1. Start with the realtime example

The example lives in
[`ai_agents/agents/examples/voice-assistant-realtime/`](../ai_agents/agents/examples/voice-assistant-realtime/).

| Modified files                                                                                                                                                                                                   | Responsibility and review focus                                                                                                                                                                                       |
| ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`tenapp/property.json`](../ai_agents/agents/examples/voice-assistant-realtime/tenapp/property.json)                                                                                                             | Adds the `voice_assistant_prism` graph. Follow microphone audio into the provider, reply audio back to RTC, and captions, errors, and tool requests into the controller. The existing OpenAI graph remains available. |
| [`tenapp/manifest.json`](../ai_agents/agents/examples/voice-assistant-realtime/tenapp/manifest.json) and [`manifest-lock.json`](../ai_agents/agents/examples/voice-assistant-realtime/tenapp/manifest-lock.json) | Include the new provider and update the example's RTC pin from `0.23.9-t1` to `0.27.0-rc2`. This dependency change affects the example's graphs.                                                                      |
| [`README.md`](../ai_agents/agents/examples/voice-assistant-realtime/README.md) and [`ai_agents/.env.example`](../ai_agents/.env.example)                                                                         | Document selecting the Prism graph and supplying backend credentials through environment variables.                                                                                                                   |

Check the audio configuration: Prism receives 16 kHz mono PCM16 and returns
24 kHz mono PCM16. The Prism graph enables `flush_by_unpub` so RTC unpublishes
outgoing audio during a flush; receiving clients must handle its next publication.

## 2. Read the added Prism provider

The entire
[`ai_agents/agents/ten_packages/extension/rime_mllm_python/`](../ai_agents/agents/ten_packages/extension/rime_mllm_python/)
directory is new. It implements TEN's existing multimodal model interface
(MLLM) over Prism's WebSocket API. It uses `aiohttp` to support TEN's Python 3.10
runtime; the Rime Python SDK requires Python 3.11 or later.

| Added files                                                                                                                                                                                   | Responsibility                                                                                                                                                                |
| --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`extension.py`](../ai_agents/agents/ten_packages/extension/rime_mllm_python/extension.py)                                                                                                    | TEN entry point and lifecycle. Translates audio, transcripts, tool requests, application commands, and provider errors. Owns asynchronous review callbacks and their cleanup. |
| [`connection.py`](../ai_agents/agents/ten_packages/extension/rime_mllm_python/connection.py)                                                                                                  | WebSocket lifecycle, startup retries, request acknowledgments, serialized session updates, event dispatch, and scheduling typed turns and tool continuations.                 |
| [`conversation.py`](../ai_agents/agents/ten_packages/extension/rime_mllm_python/conversation.py)                                                                                              | Tracks reply and tool identities. Rejects output from interrupted replies and prevents duplicate or stale tool continuations.                                                 |
| [`audio.py`](../ai_agents/agents/ten_packages/extension/rime_mllm_python/audio.py)                                                                                                            | Bounded microphone buffering and fixed-size frames padded with silence. The sender keeps audio flowing while Prism speaks.                                                    |
| [`config.py`](../ai_agents/agents/ten_packages/extension/rime_mllm_python/config.py)                                                                                                          | Validated configuration and conversion to Prism session settings, including the distinction between initial-only and mutable settings.                                        |
| [`controls.py`](../ai_agents/agents/ten_packages/extension/rime_mllm_python/controls.py)                                                                                                      | Command names and typed review requests and decisions.                                                                                                                        |
| [`manifest.json`](../ai_agents/agents/ten_packages/extension/rime_mllm_python/manifest.json) and [`property.json`](../ai_agents/agents/ten_packages/extension/rime_mllm_python/property.json) | TEN package dependencies, configuration, command/event schemas, and defaults. Compare these declarations with the implemented interface.                                      |
| `addon.py`, `__init__.py`, `requirements.txt`, and `pyproject.toml`                                                                                                                           | Extension registration and Python packaging.                                                                                                                                  |

The provider's
[`README.md`](../ai_agents/agents/ten_packages/extension/rime_mllm_python/README.md)
is the configuration and command reference. It covers voice and speed updates,
interruption rules, turn timing, recording requests, reply review, manual turns,
typed input, and backend diagnostics.

In the implementation, focus on ordering and lifecycle: settings change locally
after server acknowledgment; newer caller speech supersedes queued requests;
tool continuations wait for the parent reply and result acknowledgments; and
review work cannot block microphone delivery or the WebSocket reader. Review
callbacks belong to an application-provided extension connected in the graph.

## 3. Review the modified call controller

These changes are in the realtime example's own
[`tenapp/ten_packages/extension/main_python/`](../ai_agents/agents/examples/voice-assistant-realtime/tenapp/ten_packages/extension/main_python/).

- [`extension.py`](../ai_agents/agents/examples/voice-assistant-realtime/tenapp/ten_packages/extension/main_python/extension.py)
  runs tools in separate asynchronous tasks, handles fatal provider errors,
  flushes RTC and pending partial captions, and preserves transcript metadata.
- [`agent/agent.py`](../ai_agents/agents/examples/voice-assistant-realtime/tenapp/ten_packages/extension/main_python/agent/agent.py)
  routes provider errors and validates tool calls and results. Tool failures
  return a structured result rather than leaving the model waiting.
- [`agent/events.py`](../ai_agents/agents/examples/voice-assistant-realtime/tenapp/ten_packages/extension/main_python/agent/events.py)
  adds the typed provider-error event consumed by the controller.

Check that a slow tool leaves interruption handling responsive, shutdown owns
and cancels its tasks, fatal errors close the native call, and recoverable errors
leave it usable. This controller also serves the example's OpenAI graph, making
provider compatibility a review concern.

## 4. Review the shared caption collector

In
[`message_collector2/`](../ai_agents/agents/ten_packages/extension/message_collector2/),
`extension.py` and `manifest.json` are modified and `README.md` is added.
The collector bounds queued messages, preserves complete packet groups, supports
clearing partial captions while retaining finals, and owns its sender task.

The Prism graph opts into replacing older queued partial transcripts with the
latest text for the same turn. Other graphs retain FIFO partial updates by
default. Review final-message preservation and queue/shutdown behavior with that
shared scope in mind.

## 5. Use the added tests to check the behavior

All new tests live under the provider's
[`tests/`](../ai_agents/agents/ten_packages/extension/rime_mllm_python/tests/)
directory, including regression coverage for the controller and shared collector.

| Test files                                     | Coverage to review                                                                                                            |
| ---------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| `test_connection.py`                           | Startup, authentication/refusals, acknowledgments, disconnects, tool ordering, queued turns, and audio buffering.             |
| `test_extension.py` and `test_call_control.py` | TEN event translation, tools that do not block flush, fatal call cleanup, interrupted captions, and rejection of late output. |
| `test_caption_queue.py`                        | Coalescing, final captions, packet framing, capacity, flush, and default behavior for other graphs.                           |
| `test_session_controls.py`                     | Accepted/refused updates, native command schemas, transcripts/diagnostics, manual turns, and long text.                       |
| `test_review.py`                               | Concurrent allow/replace/stop decisions, timeouts, and cancellation of pending reviews.                                       |
| `conftest.py` and `bin/start`                  | Local WebSocket/test-runtime fixtures and the standalone test entry point discovered by the agent test command.               |

From `ai_agents`, run the provider suite with:

```sh
task test-extension EXTENSION=agents/ten_packages/extension/rime_mllm_python
```

The results below record the November 16 validation of source revision
`a1a4a469`, before subsequent upstream merges.

The 88 provider/regression tests passed on Python 3.10 on macOS and Linux. They
use local WebSockets and TEN base models with a native runtime test double.
Separate validation with the real native worker, Prism, and RTC passed 17 browser
scenarios, including 16 live calls, plus a native startup-failure check. See the
[end-to-end validation report](prism-e2e-validation-2026-11-16.md).

The broader agent-suite run recorded 1,450 passed, eight failed, and nine skipped.
The eight failures reproduced before the Prism implementation in
NVIDIA Riva, Typesafe, and Whisper tests; Text Webhook also has an existing runner
coverage gap.

## Known limits to keep in view

- RTC provides no measured receiver playback position. The adapter uses Prism's
  estimate and sends no inferred playback-complete or truncation reports.
  Generation completion and a successful flush do not establish what the caller
  heard. Browser validation checks decoded audio, not speaker playback position.
- The acknowledgment timeout starts after the WebSocket write. Waiting for the
  writer lock or a blocked write can exceed that timeout; this remains open.
- A disconnected established session ends the call. Requests and tools are not
  automatically replayed into a replacement session.
- Manual-turn controls and review callbacks need application wiring. Hosted
  Engine and Agents SDK releases, and live OpenAI regression checks, remain
  separate validation work.

The browser app and raw test evidence remain outside this repository. The
`.agents/` directory is removed from tracking and ignored; this guide and the
end-to-end report are maintained under `docs/`.
