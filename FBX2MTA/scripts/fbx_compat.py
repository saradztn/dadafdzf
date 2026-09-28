#!/usr/bin/env python3
"""FBX version detection (pure Python, no bpy needed).

Blender 4.2's FBX importer only accepts FBX >= 7.1 (version number >= 7100).
Older files (e.g. FBX 6.100 from 3ds Max 2008-era tools) are bridged through
Blender 4.5's new importer, which supports them (see scripts/bridge_old_fbx.py).
"""
import re
import struct

FBX_MIN_OK = 7100  # Blender 4.2 importer minimum


def _plausible_version(value):
    return 5000 <= value <= 9999


def fbx_version(path):
    """Return the FBX spec version int (e.g. 6100, 7100, 7400) or None."""
    try:
        with open(path, "rb") as f:
            head = f.read(128)
    except OSError:
        return None
    if head.startswith(b"Kaydara FBX Binary"):
        # Two known binary layouts:
        #  7.x : "Kaydara FBX Binary" + 2 spaces + \0 + 0x1A + endian + u32 ver
        #  6.x : "Kaydara FBX Binary" + ("  \0" or "00\0") + u32 ver
        # -> find the NUL terminator, then scan a few u32 slots for a version
        nul = head.find(b"\x00", 18, 32)  # NUL right after the magic, if any
        if nul == -1:
            nul = 20
        for off in range(nul + 1, min(nul + 5, len(head) - 3)):
            try:
                v = struct.unpack_from("<I", head, off)[0]
            except struct.error:
                continue
            if _plausible_version(v):
                return v
        # no NUL seen / nothing plausible right after: try right after magic
        for off in range(18, min(24, len(head) - 3)):
            try:
                v = struct.unpack_from("<I", head, off)[0]
            except struct.error:
                continue
            if _plausible_version(v):
                return v
        return None
    # ascii: "Kaydara FBX" text with "FBXVersion: NNNN"
    if b"Kaydara FBX" in head[:64]:
        m = re.search(rb"FBXVersion:\s*(\d+)", head)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                return None
    return None


def needs_bridge(path):
    """True when the file is a known FBX older than what Blender 4.2 imports."""
    v = fbx_version(path)
    return v is not None and v < FBX_MIN_OK
