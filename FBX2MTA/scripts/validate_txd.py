#!/usr/bin/env python3
"""Standalone TXD validator - parses the .txd with DragonFF's OWN TXD module
(gtaLib/txd.py from the official Parik27/DragonFF) and verifies:

  1. file exists and size > 0                          (no empty/fake TXD)
  2. valid RenderWare stream: Texture Dictionary chunk (0x16), RW 3.6.0.3
  3. dictionary struct: texture count matches the number of readable
     Texture Native sections
  4. every texture: non-empty name, width/height > 0, pixel data length
     consistent with the format (BGRA8888: 4 bytes/pixel)

Writes a JSON report next to the file (when --report is given) and exits
0 (valid) / 1 (invalid).

Usage:
    python3 validate_txd.py <file.txd> [--report out.json]
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                 "dragonff", "DragonFF")))
from gtaLib.txd import txd  # noqa: E402  (DragonFF's own TXD parser)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--report", default="")
    args = ap.parse_args()

    errors = []
    info = {"textures": [], "rw_version": None, "device_id": None}

    if not os.path.exists(args.file) or os.path.getsize(args.file) == 0:
        errors.append("file missing or empty")
    else:
        t = txd()
        try:
            t.load_file(args.file)
        except Exception as e:
            errors.append(f"TXD parse failed: {e}")
            t = None
        # a SA (PC) TXD must start with the Texture Dictionary chunk
        # (id 0x16) carrying the RW 3.6.0.3 stamp (0x1803FFFF)
        with open(args.file, "rb") as f:
            raw = f.read(12)
        import struct
        chunk_id, chunk_size, stamp = struct.unpack("<III", raw)
        info["rw_stamp"] = f"{stamp:#010x}"
        if chunk_id != 0x16:
            errors.append(f"bad root chunk id {chunk_id:#06x} (need 0x16)")
        if stamp != 0x1803FFFF:
            errors.append(
                f"wrong RW version stamp {stamp:#010x} "
                f"(SA needs 0x1803FFFF / 3.6.0.3)")
        if t is not None:
            info["rw_version"] = t.rw_version
            info["device_id"] = int(t.device_id)
            if not t.native_textures:
                errors.append("no textures in dictionary")
            for tex in t.native_textures:
                nm = tex.name or "<empty>"
                if not tex.name:
                    errors.append("texture with empty name")
                if tex.width <= 0 or tex.height <= 0:
                    errors.append(
                        f"texture {nm}: bad size {tex.width}x{tex.height}")
                pix = tex.pixels[0] if tex.pixels else b""
                expect = tex.width * tex.height * 4
                if tex.d3d_format == 21 and len(pix) != expect:
                    errors.append(
                        f"texture {nm}: pixel data {len(pix)} bytes != "
                        f"expected {expect} (BGRA8888)")
                info["textures"].append(
                    {"name": nm, "width": tex.width, "height": tex.height,
                     "bytes": len(pix)})

    out = {"info": info, "errors": errors}
    if args.report:
        with open(args.report, "w") as f:
            json.dump(out, f, indent=2)
    print(json.dumps(out, indent=2))
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
