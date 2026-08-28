"""cli-anything-clash-verge — agent-native CLI for Clash Verge Rev.

Three real backends, in priority order:

1. ``core.verge`` / ``core.clash`` / ``core.profiles``
   Direct read/write of Clash Verge's native YAML state
   (``verge.yaml``, ``config.yaml``, ``profiles.yaml``, ``profiles/*.yaml``).
   Always available, needs no running process.

2. ``core.controller``
   The Mihomo (Clash Meta) External Controller REST API. This is the exact
   surface the Tauri shell itself consumes through ``tauri-plugin-mihomo``,
   just over HTTP instead of the app's private IPC socket.

3. ``core.process``
   Detection of the Clash Verge GUI and the ``verge-mihomo`` sidecar.

Nothing here re-implements packet routing. All proxying is done by the real
mihomo core; the CLI only edits state and calls the controller.
"""

__version__ = "1.0.0"

__all__ = ["__version__"]
