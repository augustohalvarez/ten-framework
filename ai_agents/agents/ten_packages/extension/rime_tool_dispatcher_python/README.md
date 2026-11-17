# Prism tool dispatcher

`rime_tool_dispatcher_python` executes tools for the Prism realtime graph using
TEN's standard MLLM messages. Set the required `provider_extension` property to
the model provider's graph node name, such as `v2v`.

Route tool extensions' `tool_register` commands and the provider's
`mllm_server_function_call` data to this extension. Registration retains the
originating extension as the tool destination and forwards its metadata to the
provider. Results use `mllm_client_function_call_output` with the original call
ID. The shared call controller must not also receive those registrations or
function calls.

Each tool runs on an owned asynchronous task. Missing tools, malformed
arguments, failed commands, and unsupported results produce a structured
`tool_failed` result. Transport failures delivering a result are reported
separately. Shutdown cancels and awaits pending tasks. The provider owns tool
acknowledgments, duplicate suppression, and continuation ordering; interruption
can leave a tool running, and the provider rejects stale continuations.

Regression tests live in `rime_mllm_python/tests/test_tool_dispatcher.py` and run
with the Prism provider suite through the repository's agent test command.
