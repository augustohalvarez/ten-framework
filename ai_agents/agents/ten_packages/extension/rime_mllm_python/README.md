# Rime Prism realtime speech

`rime_mllm_python` connects TEN's MLLM interface to Prism's speech-to-speech
WebSocket API. Prism handles recognition, turn detection, tool selection, and
spoken replies. TEN transports audio, runs tools, and presents captions.

## Run the example

From `ai_agents`, set `AGORA_APP_ID` and `RIME_API_KEY` in your ignored `.env`.
Use `AGORA_APP_CERTIFICATE` when your Agora project requires it. Never put
credential values in a graph or client application.

```sh
cd agents/examples/voice-assistant-realtime
task install
task run
```

In the playground at `http://localhost:3000`, select graph
`voice_assistant_prism`, connect, and speak. The OpenAI graph remains available.
Only the selected provider needs credentials. `WEATHERAPI_API_KEY` enables the
example's weather tool; failed tools return an error result to the model.

## Configuration

| Property | Default | Meaning |
| --- | --- | --- |
| `api_key` | `${env:RIME_API_KEY\|}` | Backend Rime credential; required when this provider starts. |
| `endpoint` | `wss://prism.api.rime.ai/v1/realtime` | Prism WebSocket endpoint. TLS is required except for local test servers. |
| `voice` | `aurora` | Prism voice, validated by the server. |
| `prompt` | Empty | Agent instructions. |
| `interrupt_response` | `true` | Whether caller speech interrupts a reply. |
| `eagerness` | `auto` | `low`, `medium`, `high`, or `auto`, set at startup. |
| `startup_timeout` | `15` seconds | Total initialization/retry budget and acknowledgment timeout. |
| `startup_retries` | `2` | Additional transient connection attempts before initialization succeeds. |

The adapter runs on Python 3.10 or later using `aiohttp`. The Rime Python SDK
requires Python 3.11, so this adapter uses the public WebSocket contract to
preserve TEN's example runtime baseline.

## Audio and interruptions

Input is headerless PCM16 little-endian, 16 kHz, mono. Configure
`agora_rtc.subscribe_audio_sample_rate` as `16000` and
`subscribe_audio_num_of_channels` as `1`; declaring the MLLM input rate does not
resample frames. Replies are PCM16 little-endian, 24 kHz, mono. The input sender
keeps a continuous 20 ms stream, padding missing microphone samples with silence,
including while Prism speaks. Input buffering is limited to one second.
If input exceeds that limit, the adapter drops the stale buffer and ends the
session with a fatal error instead of replaying delayed speech.

When caller speech starts, the controller sends `flush` to Agora RTC and the
adapter rejects later output from interrupted replies. Tools execute outside
the controller's event loop so a slow tool cannot hold up that flush.

Playback timing uses **Prism's server estimate**. The inspected RTC API does
not declare a reply-relative played-position or completion callback. The adapter
sends neither `conversation.item.truncate` nor `prism.playback.drained` based on
frame delivery or `response.done`. Generation completion is not playback
completion. Receiver buffering or discarded audio can make estimated history
and readiness differ from actual playback; test the audible tail after a flush
on your deployment.

## Tools, text, and recovery

Tools register through TEN's standard MLLM interface. The adapter returns each
result once and continues only after the parent reply finishes and every result
is acknowledged. Tool execution failures produce a structured error result.
Interrupted or expired tool rounds accept late results without starting a
continuation. Missing acknowledgments end the session with a fatal error;
requests with unknown outcomes are not replayed.
Typed text and the example's greeting wait for Prism's idle/readiness event.
Completed history may be seeded before connection; live context replacement is
refused explicitly. This provider accepts audio and text, not video input.

Only transient startup failures may retry. An established conversation that
disconnects ends with a fatal MLLM error. Start a new call; no requests or tools
are replayed automatically and provider history is not claimed to survive.

## Tests

From `ai_agents`, the normal standalone command installs TEN dependencies and
runs a local WebSocket server plus TEN base-model tests without vendor keys:

```sh
task test-extension EXTENSION=agents/ten_packages/extension/rime_mllm_python
```

`task test` discovers this suite. The native runtime is doubled in adapter tests;
the installed `ten_ai_base` models and output helpers are used. For a host test
run, install `pytest`, `pytest-asyncio`, and this package's requirements, and set
`TEN_AI_BASE_INTERFACE_PATH` to an installed TEN base `interface` directory.

Before shipping, also run a real Linux RTC call through the example: greeting,
spoken turn, tool result, interruption during generation and after generation,
a later turn, mute/unmute, silence, and disconnect. Offline protocol tests do not
establish receiver playback timing.

Protocol: [Prism WebSocket reference](https://docs.rime.ai/api-reference/prism/websocket).
