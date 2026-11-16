# Prism extension feature backlog

Baseline: `feat/prism-realtime-extension` at `5c787998677db2b8f8b1afbe1f6eedcf6b38df26`.
This backlog records future work; none of these entries enables a new feature.
The protocol authority is Rime's `interfaces/speech_to_speech.asyncapi.yaml`.

## Session controls

- [ ] **Interruption rules** — expose `interrupt_min_words`,
      `interrupt_phrases`, and `acknowledgment_phrases`, including acknowledged
      updates. Keep flushing on confirmed `speech_started`, not speech hints.
      Verify that an acknowledgment preserves the reply, a stop phrase interrupts,
      and changes apply to the next reply. [SERVE-266](https://linear.app/rime/issue/SERVE-266)
- [ ] **Turn timing** — expose `end_of_turn_min_silence_ms` and
      `prism_end_of_turn_timeout_ms`. Validate their relationship and document that
      they are fixed at session initialization. Verify a pause below the minimum
      does not split a turn. [SERVE-274](https://linear.app/rime/issue/SERVE-274)
- [ ] **Speech speed** — expose `speed` at initialization and through an
      acknowledged update. Verify allowed values and the next reply's applied
      setting. [SERVE-270](https://linear.app/rime/issue/SERVE-270)
- [ ] **Recording controls** — expose `prism_recording.keep` and `external_id`
      at initialization, and surface `prism_effective_recording`. Verify explicit
      no-recording requests and permission refusals; retain server/account authority.
      [Server implementation](https://github.com/rimelabs/rime/pull/3398)

## Events and diagnostics

- [ ] **Live caller transcripts** — forward transcription deltas with the
      cumulative `transcript`, replacing displayed text when earlier words change.
      Preserve turn identity and final/failure handling.
      [SERVE-267](https://linear.app/rime/issue/SERVE-267)
- [ ] **Server timestamps** — preserve `server_time_ms` through TEN events and
      the application. Distinguish server send time from local receipt time and
      measured playback. [SERVE-269](https://linear.app/rime/issue/SERVE-269)
- [ ] **Session build information** — expose `session.version` and `request_id`
      through session-ready diagnostics. This API predates the November feature
      batch. [SERVE-272](https://linear.app/rime/issue/SERVE-272)
- [ ] **Reply endings** — surface `response.done.status_details`, including
      `audio_cut`, `max_output_tokens`, and review endings. Preserve final text
      corrections and do not treat generation completion as playback completion.
      [Token-limit reason](https://github.com/rimelabs/rime/pull/3873),
      [spoken-prefix correction](https://github.com/rimelabs/rime/pull/3874)
- [ ] **Long-text regression coverage** — retain the adapter's existing lack
      of a 4,000-character cap. Verify larger typed text/history and server rejection
      above its context-based limit. The server owns that limit.
      [SERVE-271](https://linear.app/rime/issue/SERVE-271)

## Additional control flows

- [ ] **Reply review** — expose initial `prism_review` settings and a typed
      asynchronous handler for `prism.review.requested` / `prism.review.result`.
      Correlate every chunk and preserve review-scoped error identity. Verify allow,
      replace, stop, timeout policies, multiple concurrent chunks, interruption,
      and cancellation without blocking audio or the WebSocket reader.
      [SERVE-265](https://linear.app/rime/issue/SERVE-265)
- [ ] **Client-controlled turns** — support `turn_detection: null`,
      `prism.input_audio_buffer.start`, and `input_audio_buffer.commit`, together
      with manual-mode response creation. Add application controls and verify
      push-to-talk, empty commits, cancellation, and subsequent turns.
      [SERVE-273](https://linear.app/rime/issue/SERVE-273)

## Integration constraints

TEN supports custom properties, commands, data events, and asynchronous work.
These features can use the Python 3.10 WebSocket adapter. Review and manual-turn
controls need graph/application wiring in addition to provider changes. Exposing
them through a hosted Engine or its Agents SDK needs corresponding product work.

The RTC extension does not expose measured receiver playback. Continue using
Prism's estimate until a receiving client supplies a supported measurement.
Do not infer `prism.playback.drained` from frame delivery, unpublishing, a flush
result, or `response.done`.
