# TEN agent suite validation

Revision: `9579ad2786dbc8df950150444b48d7b9c327ca1d`.
Scope: `ai_agents/Taskfile.yml`'s `task test`: standalone extension runners and
the Go API server. Provider implementation code was unchanged during this work.

## Results

All 74 extension runners were checked. Across the 73 runners that invoke tests:

| Result | Cases |
| --- | ---: |
| Passed | 1,450 |
| Failed | 8 |
| Skipped | 9 |
| Total | 1,467 |

The Prism suite contributes **88 passing tests**. AssemblyAI's four tests and
Google TTS's 36 tests pass after installing their dependencies. CosyVoice's 54
tests, Fish ASR's 45 tests, and TEN turn detection's basic test also pass after
the complete runtime/test setup. The Go server command succeeds for both
packages; those packages contain no Go test files, so this verifies compilation
and the checks run by `go test`, without claiming Go behavioral coverage.

The overall agent test command remains failing. The remaining assertion
failures reproduce on the pre-Prism commit
`1103fc22e8a5ebd9d674213c48d16bd90f48e2d8`, using the same dependency environments.
The affected provider source trees are byte-identical between that commit and
the reviewed branch.

| Provider | Failures | Reproduced behavior |
| --- | ---: | --- |
| `nvidia_riva_tts_python` | 2 | A test iterates an async generator synchronously; another calls nonexistent `create_config`. |
| `typesafe_jev_python` | 4 | Tests expect fake-only `payload`/`status` attributes on native TEN `CmdResult` objects. |
| `whisper_stt_python` | 2 | Configuration mocks do not return the value/error tuple expected by the TEN property API. |

An earlier exact-float comparison failure in `ezai_tw_tts_python` did not
reproduce after setup normalization: the complete nine-test suite passed on
both the current and pre-Prism source snapshots. It is not counted as a
remaining failure.

Seven skipped cases are credential-gated Anthropic API checks. The remaining
two skips come from the existing Alibaba and Blaze STT test definitions. No
vendor credentials were supplied for this standalone-suite run.

## Runner coverage gap

`text_webhook/tests/bin/start` sets a path and exits successfully without
invoking its tests. That runner is included in the 74 discovered runners but
contributes no passing cases above. Direct test collection also fails because
its old scaffold imports `ten`, while the installed runtime provides
`ten_runtime`. This is an existing runner/test maintenance issue, not a missing
PyPI package to install under that name.

## Environment repairs

The test environment is separate from the demo worker image and both source
repositories. All 74 provider environments passed `uv pip check`; their resolved
Python dependencies were saved separately.

- Installed each provider's declared runtime and test/development dependencies
  in an isolated Python 3.10 environment. This preserves incompatible vendor
  version requirements without changing the demo's dependencies.
- Kept pytest at 8.3.4, respected RTZR's older pytest-asyncio range, and corrected
  a conflicting asyncio-plugin upgrade in the Typesafe environment.
- Installed the shared TEN system packages with `tman` and selected each
  standalone app's base directory, clearing an inherited demo-app path. This
  fixes framework interface resolution for the newer TEN test harness.
- Added directly imported dependencies absent from provider metadata: `httpx`
  for Anthropic, and OpenAI plus `httpx[http2]` for TEN turn detection.
- Used TEN runtime 0.11.75 and base 0.7.65, matching the integration. Python is
  3.10.12 in Linux AMD64 under Colima on the ARM64 Mac.
- Used the native ARM64 Task CLI after its emulated AMD64 executable crashed.
  Both are the same verified official Task 3.54.0 release.
- Used the verified Go 1.24.3 native Linux ARM64 compiler targeting Linux AMD64
  with `CGO_ENABLED=0`, after the emulated compiler crashed in standard-library
  compilation. The server has no application C bindings.

The normal aggregate task was rerun after setup repairs. It reaches Typesafe
as its 34th runner and exits on that provider's four reproduced test failures. Individual official
`task test-extension` commands were also used to reach suites beyond an early
aggregate failure; passing counts are not limited to the aggregate prefix.
No tests or provider source were rewritten to make this run pass.

## Evidence

The separate project's ignored directory is:
`/Users/augustoalvarez/test/ten-prism-demo/.cache/full-suite-validation-2026-11-16/`.
It contains the immutable source snapshot, setup scripts, official task logs,
per-suite JUnit reports, dependency locks/checks, pre-Prism baseline comparisons,
and Go results. Initial environment failures are retained separately from the
corrected results. This run establishes standalone test results; the earlier
live Prism/RTC verification remains documented in the integration follow-up.
