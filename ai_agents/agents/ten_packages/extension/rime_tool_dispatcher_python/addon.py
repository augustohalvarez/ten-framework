"""Register the Prism tool dispatcher with TEN."""

from ten_runtime import Addon, register_addon_as_extension

from .extension import RimeToolDispatcherExtension


@register_addon_as_extension("rime_tool_dispatcher_python")
class RimeToolDispatcherAddon(Addon):
    """Create one dispatcher for the graph's Prism session."""

    def on_create_instance(self, ten_env, name, context):
        ten_env.on_create_instance_done(
            RimeToolDispatcherExtension(name), context
        )
