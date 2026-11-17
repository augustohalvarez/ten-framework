# Prism integration with TEN

This change adds Rime Prism as a speech-to-speech provider in TEN's realtime
voice-assistant example. A caller can speak to Prism, receive spoken replies
and captions, and use tools implemented as TEN extensions. Prism handles speech
recognition, turn detection, reasoning, and reply generation. TEN handles the
call's audio transport, application orchestration, tool execution, and caption
delivery.

Applications can also configure interruption rules, review replies before they
are spoken, and control turns explicitly for experiences such as push-to-talk.

## How a call works

TEN assembles an agent from a **graph**: a set of extensions and the connections
between them. The new `voice_assistant_prism` graph connects the existing RTC
transport and call controller to a new provider, `rime_mllm_python`.

1. The caller joins an Agora RTC channel. RTC carries microphone audio into TEN.
2. The Prism provider sends that audio over an authenticated WebSocket to Rime's
   Prism API. It uses TEN's existing multimodal model interface, called MLLM.
3. Prism returns reply audio, caller and assistant transcripts, and tool requests.
4. TEN sends reply audio back through RTC, forwards captions to the client, and
   routes tool requests to the registered TEN extensions.
5. Tool results return to Prism, which can continue the conversation using them.

The adapter runs on **Python 3.10** using `aiohttp`. This preserves TEN's runtime
requirement while connecting directly to Prism's public WebSocket API; the Rime
Python SDK requires Python 3.11 or later.

Microphone audio enters at 16 kHz and reply audio leaves at 24 kHz, both as mono
16-bit PCM. The sender maintains a continuous stream, padding gaps with silence.
Input buffering is bounded to prevent delayed microphone audio accumulating.

## Conversation behavior

### Interruptions clear pending output

When Prism confirms that caller speech interrupts a reply, the provider rejects
later audio and text from that interrupted reply. The controller also flushes
RTC's pending output. The Prism graph enables track unpublishing during flush,
and the client must subscribe to the next audio publication to resume playback.

The interrupted caption closes with its last forwarded text. Queued partial
captions are cleared so the display can move on to the new answer. New confirmed
caller speech also supersedes waiting greetings or typed requests, preventing an
old greeting from starting during the caller's newer conversation.

Interruption settings apply to later replies. The adapter still follows a
confirmed interruption for the current reply when a runtime setting has just
changed.

### Slow tools leave the call responsive

The realtime controller executes tools in separate asynchronous tasks, allowing
it to process interruptions while a tool is running. Tool failures return a
structured error result to the model.

The provider matches each tool result to its call. It requests a follow-up reply
only after the parent reply has finished and all results have been acknowledged.
A late result from an interrupted tool round does not start a stale answer.
Prism can speak before or during a tool operation; the answer that uses the
tool result waits for that result.

### Captions remain current and failed calls close

Caller partial transcripts use Prism's cumulative text, allowing corrections to
replace earlier words. Captions preserve turn and reply identities. The shared
message collector bounds its queue and preserves complete message framing. The
Prism graph also replaces older queued partial updates with the latest text for
the same turn, while preserving final messages.

Fatal provider errors now reach the realtime controller and close the native
call. Recoverable request errors leave the call open. Transient startup failures
can retry; an established session that disconnects requires a fresh call, without
automatically replaying tools or conversation requests.

## Controls available to applications

| Capability                      | Application behavior                                                                                                                                                                                                                               |
| ------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Voice, prompt, and speech speed | Configure the agent initially or update it during a call. Local settings change only after Prism acknowledges the update.                                                                                                                          |
| Interruption rules              | Set a minimum word count, immediate stop phrases, and acknowledgments such as “okay” that should not interrupt. These support acknowledged runtime updates in automatic mode.                                                                      |
| Turn timing                     | Configure minimum silence and an end-of-turn timeout when the session starts.                                                                                                                                                                      |
| Recording retention             | Request which text, tool, or audio data may be retained, attach an external identifier, and inspect the effective server policy. An empty retention list explicitly requests no retained data; account and deployment policy remain authoritative. |
| Reply review                    | Route proposed reply chunks to an application-provided reviewer that can allow, replace, or stop them. Slow or failed review follows the configured server timeout policy: stop, speak the original chunk, or say a fallback line.                 |
| Manual turns                    | Build push-to-talk with explicit start, commit, and create-response commands. Committing audio alone does not request a manual-mode reply. Buffer clear and response cancel commands are also exposed.                                             |
| Typed input and history         | Send typed turns and seed completed history before connecting. Text uses the server's context limit, without an adapter-imposed 4,000-character cap.                                                                                               |
| Diagnostics                     | Inspect session build/request information, effective settings, server timestamps, reply endings, and optional backend protocol events.                                                                                                             |

Applications supply a reviewer extension and connect it in their graph. Review
callbacks run asynchronously so a slow reviewer does not hold up audio or other
server events. Callbacks are cancelled when their reply ends or is interrupted,
and when the session closes.

Manual-turn mode, turn timing, recording, and review policy are fixed when the
session starts. Invalid updates are refused explicitly. Manual start and commit
commands confirm dispatch; server events report later acceptance or refusal.
Applications cancelling a reply must also handle flushing queued RTC audio.

Protocol diagnostics omit binary audio but may contain proposed review text;
applications should route them to backend consumers rather than caller captions.

## Where the repository changes are

| Area                             | Scope                                                                                                                                                                                                                                     |
| -------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| New `rime_mllm_python` extension | WebSocket connection, audio pacing, conversation/tool state, configuration, application commands, review callbacks, manifests, documentation, and regression tests.                                                                       |
| Realtime example                 | Adds the Prism graph and dependency, environment-variable documentation, and changes to this example's `main_python` controller for concurrent tools, captions, flush, and fatal errors. The example offers both OpenAI and Prism graphs. |
| Shared `message_collector2`      | Bounded message queue, packet framing, shutdown cleanup, and optional transcript coalescing. Other graphs retain FIFO partial updates by default.                                                                                         |
| RTC dependency                   | Updates the realtime example's pin from `0.23.9-t1` to `0.27.0-rc2` and its dependency lock. The version change applies to the example; track unpublishing during flush is enabled in the Prism graph.                                    |

The browser validation app and its test wiring remain in a separate project,
outside the TEN fork. Hosted Conversational AI Engine and Agents SDK releases
still need their own configuration surfaces, graph wiring, and reviewer or
manual-turn application controls.

## Validation

**88 provider and regression tests pass** on macOS and Linux with Python 3.10.
They exercise local WebSockets and TEN base models with a native runtime test
double, covering protocol ordering, refusal paths, tools, interruptions,
captions, review, manual controls, and session updates.

Separate browser validation exercised the actual native TEN worker, live Prism,
and Agora RTC. **All 17 browser scenarios passed**, including 16 live-call
scenarios. Coverage includes spoken tool use, early and late interruptions,
interruption during a tool, manual turns, all six review cases, session updates,
long typed input, interruption rules, reconnects, and call cleanup. The tool
operation measured **3.001 seconds**. A separate native startup-failure check
also passed without the demo's error handler masking controller behavior.

The broader agent-suite investigation recorded **1,450 passed, 8 failed, and
9 skipped**. All eight failures reproduced on the pre-Prism baseline in NVIDIA
Riva, Typesafe, and Whisper tests. The overall suite remains failing; the Prism
suite passes. The existing Text Webhook runner also has a coverage gap.

## Remaining limitations

**Actual playback position is unavailable.** RTC exposes no callback telling the
provider how much audio the receiving speaker has played. The integration uses
Prism's server playback estimate. Generation completion, sending audio, and a
successful flush do not prove that the caller heard the whole reply. The adapter
therefore sends no fabricated playback-complete or conversation-truncation
reports. Browser tests verify decoded receive audio; audible tails after flush
still need receiver measurements or listening on the target deployment.

**A socket write can outlast the acknowledgment timeout.** The timeout currently
starts after the WebSocket write. Waiting for the writer lock or a stalled write
is outside that deadline; this known review finding remains open.

The provider accepts audio and text. Live context replacement and video input
are unsupported. Validation covers the open-source provider and separate browser
app, with hosted-product and live OpenAI regression testing still outstanding.

## Reviewer references

- [Provider README and configuration reference](https://github.com/augustohalvarez/ten-framework/blob/a1a4a469ebef255508f6ee4a77fd350f16597f63/ai_agents/agents/ten_packages/extension/rime_mllm_python/README.md)
- [Prism graph and RTC configuration](https://github.com/augustohalvarez/ten-framework/blob/a1a4a469ebef255508f6ee4a77fd350f16597f63/ai_agents/agents/examples/voice-assistant-realtime/tenapp/property.json)
- [Controller behavior](https://github.com/augustohalvarez/ten-framework/blob/a1a4a469ebef255508f6ee4a77fd350f16597f63/ai_agents/agents/examples/voice-assistant-realtime/tenapp/ten_packages/extension/main_python/extension.py)
- [Shared caption collector](https://github.com/augustohalvarez/ten-framework/blob/a1a4a469ebef255508f6ee4a77fd350f16597f63/ai_agents/agents/ten_packages/extension/message_collector2/extension.py)
- [Full agent-suite results and baseline comparison](https://github.com/augustohalvarez/ten-framework/blob/a1a4a469ebef255508f6ee4a77fd350f16597f63/.agents/plans/prism-full-suite-validation-2026-11-16.md)
- [Browser end-to-end validation report](prism-e2e-validation-2026-11-16.md)

Source snapshot: `a1a4a469ebef255508f6ee4a77fd350f16597f63`.
