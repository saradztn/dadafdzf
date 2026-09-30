#!/usr/bin/env python3
"""GTA SA / MTA:SA IFP animation writer + reader (ANP3 format).

Pure Python, no bpy. Format: the flat int16-compressed ANP3 layout used by
GTA San Andreas and loaded by MTA's engineLoadIFP / animlib:

    "ANP3" + u32 body_size
    pkg_name[24] + u32 num_anims
    per animation:
        anim_name[24] + u32 num_bones + u32 kf_data_size + u32 flag(1)
        per bone:
            bone_name[24] + u32 type (3=rot, 4=rot+trans)
                        + u32 num_keyframes + i32 bone_id   (DFF frame id)
            per keyframe:
                i16 qx qy qz qw   (x4096)
                u16 time          (1/60 s, cumulative from anim start)
                [i16 tx ty tz]    (x1024, type 4 only)

Quantisation: |quat component| < 8 (unit quats), |translation| < 32 units,
animation length < ~1092 s (int16 time ticks).
"""
import os
import struct

ROT_SCALE = 4096.0
TRANS_SCALE = 1024.0
TIME_UNITS_PER_SEC = 60.0
INT16_MAX = 32767
INT16_MIN = -32768


class IfpError(ValueError):
    pass


def _name24(s):
    b = s.encode("ascii", errors="replace")[:23]
    return b + b"\x00" * (24 - len(b))


def write_anp3(filepath, pkg_name, anim_name, bones):
    """Write one ANP3 IFP with a single animation.

    bones: list of dicts:
        name: str            (must match the DFF frame name)
        bone_id: int         (the DFF frame id of that bone)
        keyframes: list of (time_s, quat (x,y,z,w), trans (x,y,z) or None)
    Returns (num_bones, num_keyframes).
    """
    has_trans = any(kf[2] is not None for b in bones for kf in b["keyframes"])
    # SA stores per-bone type; keep it uniform for a clean custom skeleton
    body = bytearray()
    body.extend(_name24(pkg_name))
    body.extend(struct.pack("<I", 1))  # one animation

    # animation header (36 bytes): name + bone count + kf data size + flag
    body.extend(_name24(anim_name))
    body.extend(struct.pack("<I", len(bones)))
    data_size_off = len(body)
    body.extend(struct.pack("<I", 0))
    body.extend(struct.pack("<I", 1))  # compressed keyframes

    kf_bytes = 0
    for b in bones:
        body.extend(_name24(b["name"]))
        bt = 4 if (has_trans and any(kf[2] is not None for kf in b["keyframes"])) else 3
        body.extend(struct.pack("<I", bt))
        body.extend(struct.pack("<I", len(b["keyframes"])))
        body.extend(struct.pack("<i", int(b["bone_id"])))
        per_kf = 16 if bt == 4 else 10
        kf_bytes += per_kf * len(b["keyframes"])
        for time_s, q, t in b["keyframes"]:
            for c in q:
                if abs(c) * ROT_SCALE > INT16_MAX:
                    raise IfpError(
                        f"{b['name']}: quaternion component {c:.3f} exceeds "
                        f"int16 range at x{ROT_SCALE:.0f} (normalize the rotation)")
            ticks = int(round(time_s * TIME_UNITS_PER_SEC))
            if ticks < INT16_MIN or ticks > INT16_MAX:
                raise IfpError(
                    f"{b['name']}: animation time {time_s:.2f}s exceeds int16 "
                    f"range ({INT16_MAX / TIME_UNITS_PER_SEC:.0f}s max)")
            body.extend(struct.pack("<4h", *[int(round(c * ROT_SCALE)) for c in q]))
            body.extend(struct.pack("<H", ticks & 0xFFFF))
            if bt == 4:
                tv = t if t is not None else (0.0, 0.0, 0.0)
                for c in tv:
                    if abs(c) * TRANS_SCALE > INT16_MAX:
                        raise IfpError(
                            f"{b['name']}: translation {c:.3f} exceeds int16 "
                            f"range at x{TRANS_SCALE:.0f} (max ~31.5 units)")
                body.extend(struct.pack("<3h", *[int(round(c * TRANS_SCALE)) for c in tv]))

    struct.pack_into("<I", body, data_size_off, kf_bytes)
    out = b"ANP3" + struct.pack("<I", len(body)) + bytes(body)
    parent = os.path.dirname(os.path.abspath(filepath))
    os.makedirs(parent, exist_ok=True)
    with open(filepath, "wb") as f:
        f.write(out)
    return len(bones), sum(len(b["keyframes"]) for b in bones)


def read_anp3(filepath):
    """Parse an ANP3 IFP back (used for validation). Returns a dict."""
    data = open(filepath, "rb").read()
    if len(data) < 8 or data[:4] != b"ANP3":
        raise IfpError(f"{filepath}: not an ANP3 IFP (bad magic)")
    (body_size,) = struct.unpack_from("<I", data, 4)
    if 8 + body_size != len(data):
        raise IfpError(f"{filepath}: size field {body_size} != actual {len(data) - 8}")
    pkg = data[8:32].split(b"\x00")[0].decode("ascii", "replace")
    (num_anims,) = struct.unpack_from("<I", data, 32)
    if num_anims < 1:
        raise IfpError("no animations")
    off = 36
    anims = []
    for _ in range(num_anims):
        a = {"name": data[off:off + 24].split(b"\x00")[0].decode("ascii", "replace")}
        off += 24
        (n_bones, data_size, flag) = struct.unpack_from("<III", data, off)
        off += 12
        bones, kf_total = [], 0
        for _ in range(n_bones):
            bn = data[off:off + 24].split(b"\x00")[0].decode("ascii", "replace")
            off += 24
            (bt, n_kf, bone_id) = struct.unpack_from("<IIi", data, off)
            off += 12
            if bt not in (3, 4):
                raise IfpError(f"bone {bn}: unknown keyframe type {bt}")
            per = 16 if bt == 4 else 10
            kfs = []
            for _ in range(n_kf):
                qx, qy, qz, qw = struct.unpack_from("<4h", data, off)
                (t,) = struct.unpack_from("<H", data, off + 8)
                tr = None
                if bt == 4:
                    tx, ty, tz = struct.unpack_from("<3h", data, off + 10)
                    tr = (tx / TRANS_SCALE, ty / TRANS_SCALE, tz / TRANS_SCALE)
                off += per
                kfs.append((t / TIME_UNITS_PER_SEC,
                            (qx / ROT_SCALE, qy / ROT_SCALE,
                             qz / ROT_SCALE, qw / ROT_SCALE),
                            tr))
            bones.append({"name": bn, "type": bt, "bone_id": bone_id,
                          "keyframes": kfs})
            kf_total += n_kf
        if kf_total == 0:
            raise IfpError(f"animation {a['name']}: no keyframes")
        a["bones"] = bones
        anims.append(a)
    return {"pkg": pkg, "anims": anims, "off_end": off}


if __name__ == "__main__":
    import sys
    for f in sys.argv[1:]:
        d = read_anp3(f)
        for a in d["anims"]:
            nb = len(a["bones"])
            nk = sum(len(b["keyframes"]) for b in a["bones"])
            t0 = a["bones"][0]["keyframes"][0][0]
            t1 = a["bones"][0]["keyframes"][-1][0]
            print(f"{f}: pkg={d['pkg']!r} anim={a['name']!r} "
                  f"bones={nb} keyframes={nk} t=[{t0:.2f}s..{t1:.2f}s] "
                  f"-> read OK")
