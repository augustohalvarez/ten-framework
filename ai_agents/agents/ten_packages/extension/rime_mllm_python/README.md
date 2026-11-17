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
| `speed` | Server default | Speech speed, `0.85` through `1.3`. |
| `interrupt_min_words` | Server default | Words needed to interrupt, `1` through `10`. |
| `interrupt_phrases` | Server default | Up to 100 phrases that interrupt immediately. |
| `acknowledgment_phrases` | Server default | Up to 100 acknowledgments excluded from the interruption count. |
| `end_of_turn_min_silence_ms` | Server default | Initial minimum silence before ending a turn, `0` through `5000` ms. |
| `prism_end_of_turn_timeout_ms` | Server default | Initial maximum silence, `1000` through `10000` ms; greater than the minimum. |
| `manual_turns` | `false` | Initialize `turn_detection: null`; the application controls turns. |
| `prism_recording` | Omitted | Initial retention request with optional `keep` and opaque `external_id`. |
| `prism_review` | Omitted | Initial review policy: `on_timeout`, `timeout_ms`, and optional `timeout_line`. |
| `review_extension` | Omitted | TEN extension handling `prism_review` commands; required with review enabled. |
| `emit_server_events` | `false` | Publish backend protocol diagnostics on `prism_server_event`. |
| `startup_timeout` | `15` seconds | Initialization/retry budget and acknowledgment wait after a request is written. |
| `startup_retries` | `2` | Additional transient connection attempts before initialization succeeds. |

The adapter runs on Python 3.10 or later using `aiohttp`. The Rime Python SDK
requires Python 3.11, so this adapter uses the public WebSocket contract to
preserve TEN's example runtime baseline.

New optional settings are omitted unless configured, so the server remains the
authority on defaults. `prism_recording.keep` accepts `text`, `tools`,
`caller_audio`, and `agent_audio`; `[]` explicitly requests no retained data.
The account and deployment decide what is allowed. The first update fixes the
recording request, turn-detection mode/timing, eagerness, and review policy.

For example, these additional provider properties allow short acknowledgments
while keeping a one-word stop command:

```json
{
  "speed": 0.95,
  "interrupt_min_words": 2,
  "interrupt_phrases": ["stop", "wait"],
  "acknowledgment_phrases": ["uh-huh", "okay"],
  "end_of_turn_min_silence_ms": 800,
  "prism_end_of_turn_timeout_ms": 3000,
  "prism_recording": {"keep": []}
}
```

## Application commands and diagnostics

Send commands to `Loc("", "", "v2v")`, using the graph's provider node name.
A failed command has status `ERROR` and an `error` object with `code` and a
sanitized `message`. Initial-only settings cannot be changed through this API.

| Command | Properties | Successful result |
| --- | --- | --- |
| `prism_update_session` | `settings`: mutable fields below | `session`: server-acknowledged session view |
| `prism_input_audio_start` | None | Local dispatch and `event_id`; manual mode only |
| `prism_input_audio_commit` | None | Local dispatch and `event_id`; also usable with automatic detection |
| `prism_create_response` | None | `accepted: true`: local dispatch or queued typed/proactive request |
| `prism_input_audio_clear` | None | Server acknowledged the clear |
| `prism_cancel_response` | None | Server acknowledged cancellation; queued audio still needs application flush handling |

Mutable settings are `voice`, `prompt`, `speed`, `interrupt_response`,
`interrupt_min_words`, `interrupt_phrases`, and `acknowledgment_phrases`.
An accepted update changes local settings only after `session.updated` arrives;
a refusal leaves them unchanged. Settings updates and tool registration are
serialized. Omitted fields keep their values; null does not reset them.
Interruption changes apply to later replies. The adapter follows the server's
confirmed `speech_started` event when clearing current playback, even if a
new setting disables interruption for the next reply. Automatic interruption
updates are refused in manual mode, where start/commit commands own the turn.

```python
import json
from ten_runtime import Cmd, Loc

command = Cmd.create("prism_update_session")
command.set_dests([Loc("", "", "v2v")])
command.set_property_from_json("settings", json.dumps({"speed": 0.9}))
result, error = await ten_env.send_cmd(command)
```

With `emit_server_events: true`, connect `prism_server_event` from the provider
to a backend consumer. Its payload is `{"event": <provider event>}`. It preserves
server event IDs, `server_time_ms`, session updates, review error scope/owner,
and `response.done.status_details`. Audio-delta events omit their base64 `delta`
payload. Diagnostics may contain review text and belong on the backend, not in
the caller's unreviewed transcript.

Standard transcript metadata also retains `item_id`, `response_id`, `event_id`,
`prism_request_event_id`, and `server_time_ms` when supplied. Caller partial
captions use the cumulative `transcript`, so corrections replace previous words.
Session-ready metadata includes server `version`, `request_id`, and the applied
`session`, including `prism_effective_recording`. Server send times do not measure
receiver playback. End reasons such as `audio_cut`, `max_output_tokens`, and
review endings do not by themselves mean playback finished.

## Reply review

Set `prism_review` and `review_extension` in the initial properties. For example:

```json
{
  "prism_review": {"on_timeout": "stop", "timeout_ms": 1000},
  "review_extension": "reviewer"
}
```

Add that reviewer extension to the graph. The provider sends it one
`prism_review` command per chunk, with `review_id`, `response_id`, `item_id`,
`index`, `text`, and optional server event ID/time. Return an `OK` command result
whose `decision` property is one of:

```json
{"action": "allow"}
{"action": "replace", "text": "Our staff can help with refunds."}
{"action": "stop"}
```

The callback can use an async service or application policy. A minimal handler
inside a registered TEN extension is:

```python
from ten_runtime import CmdResult, StatusCode

async def on_cmd(self, environment, command):
    result = CmdResult.create(StatusCode.OK, command)
    result.set_property_from_json("decision", '{"action":"allow"}')
    await environment.return_result(result)
```

Declare `prism_review` as a command input on the reviewer with those request
fields and an object-valued `decision` result. The provider manifest defines
the corresponding command output schema. Each callback runs on its own owned
task, so one slow chunk does not stop microphone delivery, other reviews, or
the WebSocket reader. Interruption, reply completion, and shutdown cancel
pending callbacks. Duplicate review IDs are ignored.

`timeout_ms` is `100` through `5000` (default `1000`). `on_timeout` is required:
`stop` ends the reply, `speak` speaks the original chunk, and `say` speaks the
required nonblank `timeout_line` (at most 500 characters). Only `say` accepts a
timeout line. Callback failures or missed deadlines emit a recoverable error
and leave the decision to that server policy; the adapter never invents an
allow decision. Replacement text has no 4,000-character cap: the server's
context-based text limit applies.

## Client-controlled turns

Set `manual_turns: true` before connecting. Keep audio flowing, including
silence. For push-to-talk, the application sends `prism_input_audio_start` at
the start, `prism_input_audio_commit` at release, then `prism_create_response`
when it wants a reply. A commit alone does not request a reply in manual mode.
Response creation in this mode sends a plain `response.create`, without the
typed/proactive cause or the idle gate used by automatic-mode nudges.

Start/commit command results confirm local dispatch only: these protocol events
have no correlated acknowledgment. Observe the server events for accepted
turns and errors, including empty commits. They do not return playback receipts.
Manual start cancels the prior reply through the normal confirmed-interruption
path. Microphone gating, buttons, and ordering of application requests belong
in the receiving app. The standard MLLM text/message APIs remain available.

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
The interrupted caption is closed once with its last forwarded text, marked
`interrupted` in metadata. This closes a displayed draft, not a measured record
of words heard. The controller clears stale caption updates and the collector
coalesces cumulative partial text while preserving final messages and framing.

The realtime example pins `agora_rtc` to `0.27.0-rc2` and sets the Prism graph's
`flush_by_unpub` to the string `"true"`. Flush clears the local pending queues
and RTC sender buffer, then unpublishes the outgoing audio track. Receiving
clients must handle the next audio publication to resume playback. A successful
flush result confirms local operations; it does not report remote playback.

Playback timing uses **Prism's server estimate**. The inspected RTC API does
not declare a reply-relative played-position or completion callback. The adapter
sends neither `conversation.item.truncate` nor `prism.playback.drained` based on
frame delivery or `response.done`. Generation completion is not playback
completion. Receiver buffering or discarded audio can make estimated history
and readiness differ from actual playback; test the audible tail after a flush
on your deployment.

## Tools, text, and recovery

The example routes tool registrations and function calls through
`rime_tool_dispatcher_python`. It validates and executes tools on owned
asynchronous tasks, then forwards standard MLLM results to this provider. The
shared controller's tool path remains available to the other provider graphs.
Applications can also supply their own tool executor through the MLLM interface.

The adapter returns each
result once and continues only after the parent reply finishes and every result
is acknowledged. Tool execution failures produce a structured error result.
Interrupted or expired tool rounds accept late results without starting a
continuation. Missing acknowledgments end the session with a fatal error;
requests with unknown outcomes are not replayed.
Typed text and the example's greeting wait for Prism's idle/readiness event.
New confirmed caller speech supersedes queued typed/proactive requests, so an
old greeting cannot start after the caller's newer turn.
Completed history may be seeded before connection; live context replacement is
refused explicitly. This provider accepts audio and text, not video input.

Only transient startup failures may retry. An established conversation that
disconnects ends with a fatal MLLM error. Start a new call; no requests or tools
are replayed automatically and provider history is not claimed to survive.
The example routes fatal provider errors to call control and closes the native
application; recoverable errors keep the call open.

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
