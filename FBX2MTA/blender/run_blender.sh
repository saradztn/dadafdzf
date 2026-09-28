#!/usr/bin/env bash
# Run headless Blender (bpy wheel) with the stub X11/GL libraries.
# Usage: run_blender.sh <python-script> [args...]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export LD_PRELOAD="$ROOT/blender/x11_missing.so"
export LD_LIBRARY_PATH="$ROOT/blender/stublibs${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
exec "$ROOT/blender/venv/bin/python" "$@"
