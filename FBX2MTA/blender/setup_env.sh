#!/usr/bin/env bash
# (Re)create the headless Blender (bpy) environment.
# The venv is large (~1GB) and may not survive environment snapshots;
# this script rebuilds it in one shot.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [ ! -x "blender/venv/bin/python" ]; then
    echo "[setup] Creating Blender bpy venv (downloads ~350MB)..."
    python3 -m venv blender/venv
    blender/venv/bin/pip install --quiet --no-input bpy==4.2.23
fi
blender/venv/bin/python -c "import bpy; print('[setup] Blender', bpy.app.version_string, 'ready')" \
    2>/dev/null || LD_PRELOAD="$ROOT/blender/x11_missing.so" LD_LIBRARY_PATH="$ROOT/blender/stublibs" \
    blender/venv/bin/python -c "import bpy; print('[setup] Blender', bpy.app.version_string, 'ready')"
