# Prism / TEN end-to-end validation — November 16, 2026

All 17 browser scenarios passed: 16 used live Prism and RTC, and one checked the
setup UI without a call. The separate native startup-failure check also passed.
No new integration defect was found in these flows. The test app, probes, and
evidence remain in the separate test project. No integration source changes
were needed during this validation run.

## Tested environment

- Fork branch: `feat/prism-realtime-extension`.
- Fork revision: `a1a4a469ebef255508f6ee4a77fd350f16597f63`.
- Worker image: `ten-prism-demo:a1a4a469ebef-rtc-0.27.0-rc2`.
- Image digest: `sha256:4f6c31c0e0538ff4c54c3a9f639cc8537aff4eb6691e136a4fa0a55046a8d80d`.
- Native worker: Python 3.10, TEN runtime 0.11.75, base extension 0.7.65,
  RTC extension 0.27.0-rc2, native RTC SDK 4.6.31-131.
- RTC configuration: `flush_by_unpub: "true"`.
- Receiver: Chromium 158.0.8127.18 / Playwright 1.65.0,
  browser RTC SDK 4.24.9, synthetic microphone audio.
- Prism reported build: `20261112`.
- Infrastructure: local browser and Node server; Linux AMD64 worker under
  Colima emulation on macOS. Calls used the live hosted services.

## Results

| Scenarios                       |          Count | Result and assertions                                                                                                                                                                                                                                                                                                      |
| ------------------------------- | -------------: | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Setup UI                        |              1 | Desktop/mobile layout and missing-configuration handling.                                                                                                                                                                                                                                                                  |
| Spoken request with status tool |              1 | Caller audio reaches Prism; tool starts and finishes; continuation produces RTC captions and decoded receive audio. Tool elapsed time: **3,001 ms**.                                                                                                                                                                       |
| Early and late interruptions    |              2 | Both interruption timings complete; RTC unpublishes and republishes; the following answer has captions and decoded audio; interrupted captions finalize without accumulating stale rows.                                                                                                                                   |
| Interruption during a tool      |              1 | A new question is answered; late tool completion does not replace that answer or terminate the call.                                                                                                                                                                                                                       |
| Manual turns                    |              1 | Spoken tool turn with explicit commit/reply; acknowledged clear followed by a second commit/reply; decoded response audio. The second turn contains silence, not a second spoken utterance.                                                                                                                                |
| Lifecycle                       |              1 | Mute/resume, end-call cleanup, a fresh call, tab-close cleanup, and forced worker failure. Reconnect assertions exclude closed peer connections.                                                                                                                                                                           |
| Review policies                 |              6 | Allow, replace, stop, timeout/stop, timeout/speak, timeout/say. Actual Prism review requests and terminal statuses are checked. Positive cases have RTC captions and decoded audio; stop cases have neither captions nor speech-level received audio during the observation window.                                        |
| Session controls and long text  |              1 | Initial settings acknowledged; runtime speed changes from 0.95 to 1.1; the next reply uses settings revision 3 instead of 2. Recording retention is acknowledged as empty. A typed turn exceeding 4,000 characters returns captions/audio. An unsupported runtime mode change receives HTTP 400 without breaking the call. |
| Interruption rules              |              3 | Control: “Okay” interrupts. Configured acknowledgment: the same “Okay” does not interrupt. Stop phrase: the six-word stop/question interrupts despite an eight-word minimum and receives the correct answer.                                                                                                               |
| Native startup failure          | Separate check | The native controller closes the failed call in **3,026 ms**, with the demo error-handler probe disabled.                                                                                                                                                                                                                  |

The app's seven unit tests and browser production build also passed. The rebuilt
worker uses the tested fork revision.

The early and late interruption tests observed final caption rendering **42 ms**
and **39 ms** after the provider-final event, respectively. Each retained three
assistant rows: greeting, interrupted story, and the following answer. These are
local caption-rendering measurements, not end-to-end speech latency.

No uncaught browser errors were recorded in the 16 live receiver captures.

## Receiver validation and limits

The QA-only probe uses the public remote-audio PCM callback. It records RMS,
sample rate, and RTC publication identity, without saving audio samples or
adding client playback-stop behavior. All receive captures were 48 kHz.
Positive cases require more than ten frames with normalized RMS above 0.001.
The two review-stop cases have zero frames above that threshold; tiny
comfort-noise samples are allowed.

These checks establish that audio reaches the browser decoder. They do not
measure the physical speaker's playback position, prove every word was heard,
or quantify an audible tail after flush. Review-positive cases stop after
captions and speech-level audio arrive rather than waiting for the full
utterance to finish playing. Prism's playback position remains estimated;
the integration sends no fabricated playback/truncate reports.

The acknowledgment-timeout issue remains open: the timeout starts after the
WebSocket send, so acquiring the writer lock or a blocked write can exceed that
timeout. These live scenarios do not force that condition.

This run covers the separate browser app and the native TEN graph/provider. It
does not validate the hosted Conversational AI Engine, Agents SDK, upstream Go
server/playground, phone/browser compatibility beyond Chromium, or production
hardware performance. It is separate from the earlier full agent-suite report;
that suite's unrelated baseline failures have not been reclassified as passes.

## Harness correction

The first interruption-rule run exposed a probe timing race: the test read its
event snapshot before the new RTC publication arrived. The probe now explicitly
waits for that publication and reads fresh events. The targeted retry passed,
then all three rule cases passed together. No provider change was required.
The initial failure and final successful logs are retained.

## Evidence and reproduction

Detailed evidence remains in the separate test project's
`.cache/e2e-validation-2026-11-16/` directory, outside this repository.
`summary.json` contains the image, revision, timing observations, receiver
statistics, browser errors, lifecycle checks, and SHA-256 hashes of the test
harness. Per-scenario JSON and logs are kept beside it. The summary script
verifies successful test logs, zero browser errors, expected received-audio
and silence outcomes, and cleanup assertions.

The separate test project's README describes the browser end-to-end commands.
Run live checks sequentially against the local server and retain a distinct
`TEN_DEMO_EVIDENCE_DIR`. Generate the required microphone fixtures before their
corresponding tests. The app, test harness, and raw captures are outside the
scope of the TEN source change.

Test call containers were stopped after validation.
