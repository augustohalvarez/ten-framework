# Prism follow-up implementation and verification

The first three findings in the November 15 review are fixed, and every provider
interface in the feature backlog is implemented. R4's WebSocket write-deadline
finding remains open and was excluded from this work.

## Fixes

- Fatal provider errors reach typed call control and close the native application.
  The native failure test disables the demo's error probe and backend automatic
  error cleanup, so those cannot mask a missing controller shutdown.
- An interrupted partial caption closes once with its last forwarded text and
  reply identity. Later old text/audio are rejected. This describes the displayed
  draft, not measured words heard by the caller.
- The caption collector bounds pending complete messages, preserves packet-group
  framing and finals, clears pending partials on interruption, and coalesces
  cumulative text in the Prism graph. Other graphs keep FIFO partial updates by
  default. Sender-task shutdown is owned and awaited.

## Available features

The provider README and manifest define the configuration, six application
commands, reviewer callback, and opt-in backend event interface.

- Initial and acknowledged mutable speech speed and interruption controls.
- Initial turn timing, recording retention request, and opaque recording ID.
- Cumulative caller transcript updates, turn IDs, server timestamps, server build
  and request information, effective recording policy, and reply end details.
- Long typed text/history without an adapter-imposed 4,000-character limit.
- Asynchronous per-chunk review with allow/replace/stop, all server timeout
  policies, duplicate suppression, concurrent chunks, and cancellation on
  interruption, reply completion, and shutdown.
- Manual audio start/commit, plain manual reply creation, acknowledged clear and
  cancel commands, and asynchronous server refusals of empty commits.

Native checks also found and fixed command schemas that lacked required object
properties and review timestamps incorrectly declared as 32-bit integers. Review
commands use 64-bit epoch milliseconds. Policy updates apply to future replies;
confirmed speech still flushes the current reply. New caller speech supersedes
queued text/proactive turns, including busy requests refused after that speech,
so an old greeting cannot disrupt a newer audio/tool turn. Manual-mode session
updates explicitly refuse automatic interruption controls instead of silently
ignoring those fields.

## Verification

The separate test project remains outside both repositories at
`/Users/augustoalvarez/test/ten-prism-demo`. It snapshots immutable fork commits;
the final provider implementation is `d0f8bd798` (documentation-only changes
follow). The app is rebuilt from the final branch head.
The native environment is Linux AMD64 under Colima on an ARM64 Mac, Python 3.10,
TEN runtime 0.11.75, base 0.7.65, RTC 0.27.0-rc2, and RTC SDK 4.6.31-131.
Timing observations below are functional evidence, not production benchmarks.

- **88 targeted tests passed** on macOS Python 3.10.21 and Linux Python 3.10.12.
  They use real local WebSockets and installed TEN base models with a native
  runtime double. They cover all three fixes, settings acceptance/refusal,
  transcript corrections, diagnostics/redaction, concurrent review decisions,
  review lifecycle/timeouts, manual controls, correlated clear/cancel outcomes,
  empty-commit refusals, and long text/history with server-owned limits.
- Native fatal startup closed the application without the demo error probe or
  backend error cleanup. The verified pre-feature fix completed in 3,018 ms;
  the native feature build's follow-up completed in 3,068 ms.
- Native TEN/live Prism accepted initial controls, acknowledged runtime speed
  and interruption changes, retained explicit `keep: []`, refused changing an
  initial-only setting, exposed build/request information and timestamps, and
  completed a typed turn longer than 4,000 characters.
- Native TEN/live Prism passed allow, replace, stop, timeout-stop, timeout-speak,
  and timeout-say review cases. The real server provided the timeout end reasons;
  the adapter did not manufacture review decisions.
- Browser push-to-talk over real RTC passed start/commit/reply, the complete
  three-second tool round, buffer clear, and a second committed turn/reply.
  A continuously padded microphone buffer can contain silence after a clear;
  the browser test does not mistake that for an actually empty server buffer.
- The final early-interruption browser check passed unpublication, republication,
  the answer "Four", and three separate visible assistant rows. The actual RTC
  caption arrived **63 ms** after the provider-final diagnostic.
- The final standard browser conversation/tool check passed actual RTC audio,
  visible captions, continuation after the tool result, and call cleanup. Tool
  duration measured **3,000 ms**.
- The example lock was regenerated with its existing release pins and matched
  the tracked lock. Fresh resolution and a `--locked` install both passed with
  the final manifests. Browser setup/desktop/mobile checks also passed.
- Whole-extension Black check passed for **1,051 files**; implementation Pylint
  passed at **10.00/10**. The separate app's seven unit tests and Vite build pass.
- The repository-wide `task test` was attempted in an isolated Linux environment.
  It stops before the provider suite in `assemblyai_asr_python` because that
  unrelated extension's test environment lacks `websockets`. The repository-wide
  suite is **not green/complete**. A combined host collection also encounters
  unrelated runtime doubles; the supported task runner isolates extension suites.

Evidence is in the separate project's ignored `.cache/follow-up/`: native unit
and full-suite logs, native fatal-startup evidence, feature-case event reports,
manual-turn evidence, final RTC tool/interruption reports, formatting and lint
logs. Credentials, RTC tokens, and raw SDK logs are excluded.

## Remaining limits and product work

- R4 remains: acknowledgment timing starts after the WebSocket write, so blocked
  writer-lock acquisition or writes are outside that deadline.
- RTC exposes no measured receiving-client playback position. Keep Prism's
  estimate; never infer playback completion from generation, frame delivery,
  unpublication, or a local flush result. Actual speaker timing and the audible
  tail after flush still need listening/receiver measurements.
- Interruption phrase/acknowledgment quality and sub-minimum speech pauses were
  not exhaustively evaluated with live callers. The adapter forwards and validates
  the controls; the Prism server owns those decisions. Restricted-account
  recording-permission refusals were not exercised against a live account.
- A hosted Engine or Agents SDK needs its own configuration/control surface and
  reviewer implementation. This change implements the open-source TEN provider;
  the separate browser app supplies test wiring and a deterministic reviewer.
  Hosted-product and live OpenAI regression checks were not run.
