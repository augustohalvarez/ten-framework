# Prism realtime extension implementation plan

Date: 2026-11-09. Status: source investigation complete; implementation pending.

Add Rime Prism as a speech-to-speech provider through TEN's existing MLLM
interface. Developers should be able to select Prism in the realtime voice
assistant and keep Agora RTC, the main controller, captions, and tool extensions.

## Baseline and evidence

- TEN baseline: `dd78994529c927e6e83065f0904b33b7c5a3d428`.
- Fork: <https://github.com/augustohalvarez/ten-framework>.
- Working branch: `feat/prism-realtime-extension`.
- The realtime example pins `agora_rtc` to `0.23.9-t1`. Its checked-in lock
  resolves `ten_ai_base` to `0.7.22` and the TEN runtimes to `0.11.27`.
- The RTC package was fetched with a read-only GET from TEN's public registry.
  Its manifest declares the API; the archive contains `manifest.json`,
  `property.json`, and two native libraries, with no implementation source.
- Registry package hash:
  `6f08c87c362d30bbfd831899435fec06982a2924bd9cc0f5cb7e936974bf5731`.
- Downloaded archive SHA-256:
  `83b46c93ec15b422dabd6f70fca0475a310d8ad08e5307d47ee5521b02374042`.
  This is a byte checksum, distinct from the registry's package hash.
- A second manifest, `agora_rtc` `0.27.0-rc2`, declares the same command/data
  names relevant to playback. This is an additional sample, not a recommendation
  to change the example's pin or a claim about every published version.
- [RTC API evidence](prism-rtc-api-evidence.json) records the inspected package
  contents, declared messages, and the limits of the conclusion.

Public source anchors at the baseline:

- [Realtime graph and RTC configuration](https://github.com/TEN-framework/ten-framework/blob/dd78994529c927e6e83065f0904b33b7c5a3d428/ai_agents/agents/examples/voice-assistant-realtime/tenapp/property.json#L9).
- [Pinned RTC dependency](https://github.com/TEN-framework/ten-framework/blob/dd78994529c927e6e83065f0904b33b7c5a3d428/ai_agents/agents/examples/voice-assistant-realtime/tenapp/manifest.json#L12).
- [MLLM base interface and audio helpers](https://github.com/TEN-framework/ten_ai_base/blob/0.7.22/interface/ten_ai_base/mllm.py).
- [OpenAI connection and authentication](https://github.com/TEN-framework/ten-framework/blob/dd78994529c927e6e83065f0904b33b7c5a3d428/ai_agents/agents/ten_packages/extension/openai_mllm_python/realtime/connection.py#L68).
- [OpenAI interruption handling](https://github.com/TEN-framework/ten-framework/blob/dd78994529c927e6e83065f0904b33b7c5a3d428/ai_agents/agents/ten_packages/extension/openai_mllm_python/extension.py#L364).
- [Main event loop and tool dispatch](https://github.com/TEN-framework/ten-framework/blob/dd78994529c927e6e83065f0904b33b7c5a3d428/ai_agents/agents/examples/voice-assistant-realtime/tenapp/ten_packages/extension/main_python/extension.py#L90).
- [Tool result delivery](https://github.com/TEN-framework/ten-framework/blob/dd78994529c927e6e83065f0904b33b7c5a3d428/ai_agents/agents/examples/voice-assistant-realtime/tenapp/ten_packages/extension/main_python/agent/agent.py#L149).
- [Browser RTC playback](https://github.com/TEN-framework/ten-framework/blob/dd78994529c927e6e83065f0904b33b7c5a3d428/ai_agents/playground/src/manager/rtc/rtc.ts#L309).
- [Prism migration contract](https://docs.rime.ai/docs/prism/migrate-from-openai),
  [WebSocket reference](https://docs.rime.ai/api-reference/prism/websocket), and
  [playback behavior](https://docs.rime.ai/docs/prism/live-conversations).

## What the RTC extension exposes

| Surface | Verified meaning and limitation |
| --- | --- |
| `flush` command | Declared command input, with no declared result payload. The example sends it to RTC when the model reports an interruption. There is no declared played offset, response ID, or drain receipt. |
| `on_remote_audio_track_statistics` | Fields are `stream_id`, `network_transport_delay`, `jitter_buffer_delay`, and `e2e_delay_ms`. These describe a remote track received by the agent, not how much of the agent's reply a caller played. |
| `on_first_remote_audio_frame` / `on_first_remote_audio_decoded` | Fields are `remote_user_id` and `elapsed`. They do not identify a generated reply or its played sample count. |
| `pcm_frame` output | Incoming remote media, with channel/user/stream/session metadata. Media timestamps are not a reply-relative remote playback acknowledgment. |
| `audio_frame_send_delay_ms` | Declared integer configuration, not a played-position result. |
| `use_frame_counted_audio_timestamp` | Declared boolean configuration, not a per-reply playback API. |
| Browser player | The checked-in playground invokes `audioTrack.play()`; no per-response playback reporting is wired back to TEN. |

Binary string inspection additionally finds local sender-buffer and queue flush
messages. This supports investigating local queue clearing; it does not prove
remote receiver buffers are cleared or reveal a supported command-result schema.
No native code was executed. The binary inspection is corroboration only.

Conclusion: the inspected, declared RTC API has no measured per-response
playback cursor or completion receipt. The native implementation is not present
in the open-source checkout or inspected package. Undocumented flush-result
properties and private/newer Engine APIs remain unverified.

## Playback decision

Ship the first integration using Prism's documented server playback estimate:

1. Keep input audio flowing while Prism speaks and while the caller is silent.
2. On `input_audio_buffer.speech_started`, mark the interrupted reply locally,
   discard its queued output, and emit TEN's `mllm_server_interrupted` event.
3. Main control sends `flush` to RTC immediately, including during slow tools.
   Check the command result for failure rather than assuming success.
4. Do not send `conversation.item.truncate` without an observed played position.
   Do not send `prism.playback.drained` at `response.done`, at the last decoded
   audio chunk, or when TEN accepts a frame. None proves remote playback ended.
5. Let Prism estimate playback/history when no report is available. Document
   the resulting approximation and possible delay before typed/proactive input
   is admitted. Generation completion and playback completion are separate.

This makes measured playback a later improvement, not a prerequisite for voice
conversations. Device mute, network buffering, and receiver queueing remain
limits of this first mode. Test their effects instead of advertising exact
heard-word history or immediate clearing of every remote buffer.

To add measured playback, obtain a supported RTC callback/result that states
the stream and reply/item identity, played samples or milliseconds, clock
origin, meaning of queue/drain/flush completion, and whether the measurement is
server-side publication or remote rendering. Generation IDs are needed to reject
stale reports. A local publisher counter can support an estimate but cannot be
reported as measured remote rendering. Implement no speculative RTC message
names or SDK receipt support before that contract is confirmed.

## Implementation shape

Create `ai_agents/agents/ten_packages/extension/rime_mllm_python/` with addon
name `rime_mllm_python`, `RimeRealtimeExtension`, and vendor `rime`.
Use the established TEN MLLM interface and output helpers. Keep the transport
client and Prism response/tool state separate from TEN event conversion.

The first transport should be a small Python 3.10-compatible `aiohttp`
WebSocket client. Reuse TEN's existing dependency and connection patterns, but
send Bearer authentication and only Prism's supported messages. Do not copy
OpenAI's full protocol model or build another generic realtime framework.

Reason for the direct client: TEN's example declares Python `>=3.10` and its
release image uses Ubuntu 22.04's `python3`; the currently published Rime SDK
`0.1.0a29` requires Python `>=3.11` ([package metadata](https://pypi.org/pypi/rimelabs-sdk/0.1.0a29/json)).
Using that SDK requires a separate verified Python/runtime-image upgrade.
This plan preserves the existing example's runtime baseline. State this reason
in the eventual extension PR and recheck versions before implementation.

### Files and responsibilities

| Path | Planned change |
| --- | --- |
| `.../rime_mllm_python/addon.py`, `__init__.py` | Standard TEN registration and package entry. |
| `.../rime_mllm_python/config.py` | Typed settings, secret redaction, endpoint and session configuration. Keep each configuration value authoritative in one place. |
| `.../rime_mllm_python/connection.py` | WebSocket authentication, serialized writes, initialization, receive loop, bounded startup retry, error classification, and cleanup. |
| `.../rime_mllm_python/conversation.py` | Reply/item/call identities, typed-input readiness, acknowledgments, tool-round completion, and interrupted-response filtering. |
| `.../rime_mllm_python/extension.py` | Adapt TEN audio/data/lifecycle to the transport and emit standard MLLM events. Reuse base output helpers. |
| `.../rime_mllm_python/manifest.json`, `property.json`, `pyproject.toml`, `requirements.txt` | Matching addon name, MLLM interface import, supported config and dependencies. Use environment substitution for credentials. |
| `.../rime_mllm_python/tests/`, `tests/bin/start` | Offline fake-WebSocket and TEN-harness tests reachable from `task test`. |
| `.../rime_mllm_python/README.md` | Configuration, quickstart, audio contract, estimate-based playback, tool behavior, and recovery limits. |
| `ai_agents/agents/examples/voice-assistant-realtime/tenapp/property.json` | Add a separately selectable Prism graph while retaining the existing OpenAI graph. Preserve the `v2v` node name expected by main control. |
| `.../voice-assistant-realtime/tenapp/manifest.json` | Add the local Prism extension dependency. Let the normal installer resolve generated files. |
| `.../voice-assistant-realtime/tenapp/ten_packages/extension/main_python/extension.py` | Dispatch tool calls as tracked concurrent tasks so an in-flight tool cannot block interruption handling. Cancel/join owned tasks on shutdown. |
| `.../voice-assistant-realtime/tenapp/ten_packages/extension/main_python/agent/agent.py` | Return structured tool failures instead of leaving Prism waiting after exceptions, missing tools, or invalid results. |
| `ai_agents/.env.example`, realtime example README | Document the optional Rime credential name, graph selection, startup, and smoke checks. |

The `.../rime_mllm_python/` prefix above is
`ai_agents/agents/ten_packages/extension/rime_mllm_python/`.

### Session and audio behavior

- Connect to `wss://prism.api.rime.ai/v1/realtime` with the key loaded from
  `RIME_API_KEY` through TEN environment substitution. Keep it on the backend.
- After `session.created`, send a correlated `session.update` and wait for
  `session.updated` before exposing initialized audio/session readiness.
- Send instructions, tools, voice, and supported turn-detection fields. Do not
  forward OpenAI model/transcription/temperature/VAD-type defaults. The server
  remains the authority on available voices and accepted settings.
- Set the Prism graph's RTC input explicitly to 16,000 Hz, mono. Verify the
  actual frames' rate, channels, width, and sample alignment; the MLLM base's
  input-rate method does not itself resample frames.
- Send headerless PCM16 little-endian input. Emit reply frames as 24,000 Hz,
  mono PCM16 with correct TEN frame metadata. Handle input/output rates
  independently, as TEN's Gemini extension already does.
- Keep live input continuous, including during output. Add tested, clocked
  silence filling only where RTC mute/gaps stop callbacks. Bound queued audio,
  avoid duplicating received samples, and discard stale buffered input rather
  than replaying it as fresh speech. Validate timing in a real RTC call.
- Own all receive, audio-sender, initialization-wait, and tool tasks. Cancel and
  await them on stop. Audit the base class's task ownership before using its
  default lifecycle; output helpers can be reused without copying its detached
  task pattern. Never reconnect an established call as if its history survived.

### Mapping TEN behavior to Prism

| TEN operation or Prism event | Adapter behavior |
| --- | --- |
| `mllm_client_request_message_item` followed by create-response | Buffer a user text item until its trigger, then issue one Prism `user_text` request. This covers the existing main controller's greeting sequence without creating the user turn twice. Assistant/history messages use `conversation.item.create` with acknowledgments. |
| Create-response with no pending user text | Issue a `proactive` request only when Prism allows it. Wait for `prism.typed_input.ready`; bound pending triggers and never retry an accepted request merely because its reply is slow. |
| Context initialization | Seed completed history in order before the greeting. Refuse unsupported live history replacement explicitly rather than pretending a local context assignment changed provider memory. |
| Tool registration | Build Prism function schemas from TEN tool metadata and send an acknowledged session update. Avoid racing overlapping updates. |
| `response.function_call_arguments.done` | Record `call_id -> parent response`, then emit `mllm_server_function_call`. Tools run outside the receive loop. |
| `mllm_client_function_call_output` | Submit the result with a unique event ID. After the parent's `response.done` and all result acknowledgments, send exactly one `tool_continuation` with `prism_parent_response_id`. Handle out-of-order results, stale rounds, and failed tools. |
| Caller transcript completion/failure | Emit completed transcripts from the actual event text, even when they arrive after the reply. On transcription failure, report the failure and close that transcript with an empty final result. Neither event triggers a second response. |
| `response.text.delta` / `.done` | Emit assistant captions, preserving accumulated and final text. |
| `response.audio.delta` | Decode PCM and emit output only while the reply is current; ignore later chunks from interrupted replies. |
| `input_audio_buffer.speech_started` | Emit the interrupt event promptly and clear local interrupted output. Main flushes RTC without waiting for tool execution. |
| `response.done` | Complete generation/tool-round bookkeeping; do not infer playback completion. |
| Error or disconnect | Emit TEN's classified MLLM error with sanitized vendor information. Retry eligible startup failures within a finite budget. After an established connection fails, surface session loss and require an explicit new call. |

The adapter must not unconditionally attach OpenAI dataclasses to Prism server
events. For example, TEN's OpenAI `Session` expects `expires_at`, which is not
part of Prism's documented session view. Parse the Prism subset and tolerate
unrelated future server fields/events without losing known events or errors.

## Delivery order and acceptance checks

1. **Transport and state tests:** implement the direct client, typed config,
   and response/tool state against a local fake Prism WebSocket. Acceptance:
   initialization gates audio; errors are correlated; authentication is
   redacted; cancellation leaves no owned tasks or client sessions behind.
2. **TEN extension:** implement the MLLM methods and standard event conversion.
   Acceptance: 16 kHz input and 24 kHz output are verified, captions and delayed
   transcripts work, and a full turn completes without real API credentials.
3. **Tool and interruption support:** add parent-response continuation and the
   main controller's concurrent tool dispatch. Acceptance: a deliberately slow
   tool cannot delay RTC flush, multiple calls continue once, and failures
   produce results/errors instead of indefinite silence. Exercise the existing
   OpenAI graph to check shared-controller compatibility.
4. **Prism graph and documentation:** add a separate graph and optional Rime
   configuration. Both providers remain selectable without requiring keys for
   unselected graphs. Run the installed example to check the server's actual
   graph filtering and environment substitution.
5. **Linux RTC smoke test:** in the supported TEN container, verify greeting,
   caller turn, tool result, interruption during generation, interruption after
   generation but before playback finishes, a later turn, mute/unmute, long
   silence, disconnect, and shutdown. Record audible post-flush tail and any
   timing error from estimate-based playback. Do not claim these checks passed
   from offline tests or a macOS-only run.

Offline tests must also refuse wrong input rate/format, malformed events,
invalid startup settings, duplicate/stale continuations, output from interrupted
responses, failed flush commands, and fabricated playback reports. Include
tool results before/after `response.done`, tool failure, and a transcript that
arrives after the reply. Use controlled clocks and synchronization, not sleeps
that guess when another task has run.

Run from `ai_agents` after implementation:

```sh
task check
task lint
task test-extension EXTENSION=agents/ten_packages/extension/rime_mllm_python
task test
```

The existing `task test` discovers extension directories containing `tests`,
and the AI Agents workflow calls it for the realtime example. Keep mock tests
credential-free and put live-vendor checks behind explicit opt-in. ASR/TTS
guarders do not validate a speech-to-speech provider.

Use two reviewable changes: the generic realtime-controller tool/flush fix,
then the Prism extension, graph, and docs. Include a regression test for the
controller fix. Request the supported playback contract from Agora separately;
its answer can improve fidelity without preventing the estimate-based work.

## Questions that need Agora implementation knowledge

1. Does `flush` return any supported fields beyond the declared status? Which
   sender, SDK, network, and receiver queues does its completion cover?
2. Is there a supported reply-correlated published/played-sample cursor or a
   drain callback in another RTC package or in Conversational AI Engine?
3. Can the receiving app acknowledge rendered audio with a reply/item identity?
   If so, what clock, buffering, mute, and multi-listener semantics apply?
4. Which TEN runtime and RTC package versions will the hosted product release
   use, and does its Python baseline allow the Rime SDK's Python 3.11 minimum?

## Verification completed for this plan

- Fork and local branch created from the recorded baseline.
- Public RTC package downloaded and its declared API inspected, including an
  additional release candidate. No native package code executed.
- Realtime graph, controller, OpenAI/Gemini adapters, browser player, MLLM base,
  test discovery, and CI entry points inspected.
- Prism public contract and the local Rime server's audio/playback behavior
  cross-checked. This document contains only public source/API facts.
- This change is an implementation plan. It does not implement the extension
  or establish runtime/RTC test results.
