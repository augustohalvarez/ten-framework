# Prism integration: end-to-end checks and design review

Reviewed November 15, 2026, against
`5c787998677db2b8f8b1afbe1f6eedcf6b38df26`. Provider runtime code is unchanged.
Future API additions are tracked in [the feature backlog](prism-feature-backlog.md).

Follow-up: R1–R3 are fixed; see the
[implementation verification](prism-follow-up-validation-2026-11-16.md). R4 remains
open and was excluded from the follow-up implementation. The findings and
results below describe the original reviewed commit.

## Environment and scope

The separate `ten-prism-demo` project was rebuilt from the reviewed commit.
It runs the fork's provider, controller, stream adapter, message collector, and
Prism graph in native TEN on Linux AMD64, with Python 3.10.12, TEN runtime
0.11.75, base 0.7.65, RTC 0.27.0-rc2, and RTC SDK 4.6.31-131. The image is
`ten-prism-demo:5c787998677d-rtc-0.27.0-rc2`, built as `9fd52e158175`.
Linux AMD64 runs under the local Colima VM on an ARM64 Mac. Timing observations
below describe these functional checks, not production latency benchmarks.

Chromium publishes synthetic caller speech through real RTC and receives Prism
replies through RTC. The demo substitutes a deterministic three-second status
tool for the example's weather tool and adds a diagnostics extension. Its
diagnostics receive provider messages directly; visible captions arrive through
the normal controller, message collector, and RTC path. The full Go API server,
upstream playground, hosted Engine, and Agents SDK are outside this test.

## Findings

### R1 — P1: fatal provider errors do not reach call control

The Prism graph routes transcripts, session readiness, interruption, and function
calls from the provider to the controller, but omits `error`.
[Graph routes](../../ai_agents/agents/examples/voice-assistant-realtime/tenapp/property.json#L266).
The controller's `Agent.on_data` also has no error handler: explicitly delivering
the error logs `Unhandled data: error`.
[Controller dispatch](../../ai_agents/agents/examples/voice-assistant-realtime/tenapp/ten_packages/extension/main_python/agent/agent.py#L128).

When the WebSocket fails, the provider stops its own connection and audio sender,
but does not terminate the hosting call. Its base error helper emits an ordinary
`Data("error")`, which does not stop the application by itself.
[Provider failure handling](../../ai_agents/agents/ten_packages/extension/rime_mllm_python/extension.py#L296).
The example can therefore remain connected to RTC while Prism is unavailable,
with no application error or call-ending action. This also affects startup errors.

Two targeted tests reproduce the missing route and the ignored fatal disconnect.
The demo masks this issue: its extra probe receives the error and its backend
stops the container. Passing the demo's worker-failure check does not validate
the example's provider-failure path.

**Fix direction:** route provider errors to a typed controller event, distinguish
recoverable and fatal errors, surface failure to the application, and end the
owned call on fatal errors. Cover startup rejection and established disconnect
using the actual example graph without relying on the diagnostics extension.

### R2 — P2: an interrupted partial caption is never finalized

`speech_started` invalidates the old reply's generation. The provider then drops
all old output, including `response.text.done`.
[Output filtering](../../ai_agents/agents/ten_packages/extension/rime_mllm_python/extension.py#L251).
It emits an interrupt event but no final caption for text already forwarded.

A controlled WebSocket test sends partial story text, an interruption, the old
reply's final text, and cancellation. No final assistant transcript is emitted.
The separate browser app holds one draft per role, so the next assistant reply
can overwrite that unfinished story instead of becoming a new conversation row.
Interruption after a reply's final text has arrived does not exhibit this case.

**Fix direction:** close or explicitly cancel the displayed caption at interruption,
preserve reply identity through the transcript path, and continue rejecting stale
audio and text additions. Do not present generated but unheard text as measured
caller history.

### R3 — P2: queued captions survive an audio flush

The message collector keeps an unbounded queue, breaks every cumulative caption
into packets, and sends one packet every 40 ms.
[Collector queue and sender](../../ai_agents/agents/ten_packages/extension/message_collector2/extension.py#L73).
Long, fast-generated replies can enqueue packets faster than this sender drains.
The controller's interruption handler flushes RTC audio but does not flush the
collector's caption queue, although the collector already accepts a `flush` data
message.
[Interruption handler](../../ai_agents/agents/examples/voice-assistant-realtime/tenapp/ten_packages/extension/main_python/extension.py#L274).

In a real story/interruption/math call, the browser displayed the new answer
about 3.8 seconds after the provider's final-text diagnostic. Old story captions
continued arriving while the next answer's audio had already been published.
This is separate from R2: that call had received the story's final text before
interruption and retained three distinct assistant rows.

A second call interrupted earlier, with synthetic caller speech starting at
18 seconds. Provider interruption, track republication, and the new final answer
all arrived, satisfying the test's provider/audio conditions. The visible RTC
caption still contained the old story when its subsequent 30-second wait expired.
That end-to-end check failed, independently of the controlled caption-closure test.

**Fix direction:** coalesce superseded partial captions, bound the pending queue,
and clear stale caption packets at interruption while preserving final messages
and complete packet framing. Keep RTC transport limits intact. Assert visible
RTC captions in end-to-end tests, rather than relying on diagnostic events alone.

### R4 — P2: acknowledgment deadlines exclude a blocked write

`acknowledged_request` awaits `send` before starting `asyncio.wait_for` on the
acknowledgment future. `send` first acquires a shared write lock and then awaits
the WebSocket write, neither under this deadline.
[Request deadline](../../ai_agents/agents/ten_packages/extension/rime_mllm_python/connection.py#L132).
Under write contention or network backpressure, a tool result or settings update
can remain pending beyond its configured acknowledgment timeout.

A controlled test holds the writer lock, configures a 20 ms acknowledgment
deadline, and observes that only an outer 100 ms test deadline cancels the
request. It never produces the adapter's acknowledgment-timeout error. This is a
reproduced synchronization case, not an observed production network failure.

**Fix direction:** apply a single deadline to writing and receiving the
acknowledgment, clean up the waiter on every exit, and preserve terminal handling
for requests whose delivery outcome is unknown.

## Verification results

- Existing Python suite: **31 passed** on Python 3.10 with real TEN base models
  and a native-runtime double. Protocol tests use real local WebSockets.
- Four added desired-behavior probes: **4 failed**, reproducing R1 twice, R2,
  and R4. These are recorded defects; the combined suite is not green.
- Demo unit suite: **7 passed**. Browser setup/desktop/mobile check passed.
- Native provider formatting: **10 files passed** Black's check.
- Real RTC conversation, captions, complete tool continuation, and end call:
  passed. Tool execution measured **3,001 ms** between backend receipt events.
- Story interruption, unpublication, republication, and the answer "Four":
  passed. A follow-up check of the actual displayed RTC caption passed with
  **3,819 ms** delay from provider-final receipt, documenting R3.
- Earlier story interruption: the provider/audio conditions passed, but the
  visible RTC-caption assertion **failed after 30 seconds** on stale story text.
  End-to-end caption correctness is not green.
- Speech during the three-second tool: passed. The browser received unpublication
  **32 ms** after the interruption event, before the tool finished. Late tool
  completion did not close the call or replace the requested new answer.
- Fifteen seconds of microphone mute, then unmute and a spoken tool request:
  passed. The browser received nonzero audio energy and 2,494 inbound audio
  packets in the recorded call.
- Explicit end-call cleanup, a fresh call, tab-close cleanup, and forced failure
  of the test call's worker: passed. Owned call containers were removed.

The fault probes and browser checks are in the separate demo project's
`tests/review/` and `tests/browser/`. Synthetic evidence is in its ignored
`.cache/design-review/` directory: `combined-tests.log`,
`current-tool-timing.json`, `current-interruption-with-captions.json`,
`tool-interruption.json`, `lifecycle.json`, `early-interruption-test.log`, and
`early-interruption-browser-failure.md`. Credentials, RTC tokens, and raw
native SDK logs are excluded.

Browser unpublication timing, decoded audio energy, and provider text are not a
measurement of the listener's played position or audible post-flush tail.
Actual speaker timing, a live upstream playground call, a live OpenAI regression
call, and hosted-product behavior have not been established by this run.
