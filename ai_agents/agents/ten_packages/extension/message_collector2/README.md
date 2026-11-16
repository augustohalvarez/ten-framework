# RTC message collector

`message` accepts a JSON object and publishes its UTF-8 JSON as base64 packet
groups on `data`. Each packet is `message_id|one_based_part|total_parts|content`,
at most 1024 bytes. Packet groups complete before another group is sent, with
40 ms between packets.

Transcription messages have `data_type: "transcribe"`, `role`, `stream_id`,
optional `turn_id`, `text`, and `is_final`. With `coalesce_transcripts: true`,
pending cumulative partial text for
the same role, stream, and turn is replaced by its newest value. A final message
replaces that turn's pending partial and remains ordered with other finals.
Coalescing defaults to false for other consumers. The Prism graph enables it.
The queue holds at most 64 complete messages and applies backpressure at capacity.

`flush` discards pending messages. With the optional boolean `preserve_final`
set to `true`, it discards only partial messages. An in-flight packet group is
finished to preserve framing. Neither this command nor its completion measures
audio playback on the receiving client.
