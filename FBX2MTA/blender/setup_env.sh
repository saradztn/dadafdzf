#!/usr/bin/env bash
# (Re)create the headless Blender (bpy) environment (Linux/macOS entry point).
# The cross-platform logic lives in setup_env.py (also used by Windows).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec python3 "$ROOT/blender/setup_env.py"
