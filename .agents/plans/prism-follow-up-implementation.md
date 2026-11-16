# Prism follow-up implementation

1. Fix fatal call shutdown, interrupted caption closure, and stale caption queues.
   Add discriminating tests, run the Python suites, and exercise the native RTC
   path before starting the feature work.
2. Implement the feature backlog: session controls, transcripts/diagnostics,
   reply review, manual turns, and long-text coverage. Keep Python 3.10 and the
   direct WebSocket adapter. Document the extension and application interfaces.
3. Verify the complete change, refresh the separate test app, and commit/push
   the branch. The fourth review finding about write deadlines is excluded.

Status: step 1 in progress.
