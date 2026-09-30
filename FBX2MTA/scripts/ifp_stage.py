#!/usr/bin/env python3
"""IFP export stage - runs INSIDE the main Blender session (imported by
convert.py after the DFF export).

The scene at this point is already in the final Y-up clump space (same
transforms as the DFF), so the baked tracks play correctly on the exported
DFF: per-frame bone values are the bone's local matrix in PARENT space
(root: clump space). At the rest frame they equal the DFF's rest frames.

bone_id = the DFF frame id of the same-named frame (parsed from the just
exported DFF with DragonFF's gtaLib), which is what MTA's engineLoadIFP
matches against the model.
"""
import os

import mathutils

import bpy

import gta_ifp


def _sanitized(name, fallback):
    s = "".join(c if (c.isascii() and (c.isalnum() or c == "_")) else "_"
                for c in name)
    return s[:23] or fallback


def _fbx_fps(path, default=30.0):
    """Read the frame rate declared in a binary FBX (Blender writes
    CustomFrameRate=double; Maya/Max write Frames per second=int).
    Blender's own FBX importer discards it, so it is needed to time the
    baked keyframes at the source speed."""
    import struct

    def _first_sane_double(window):
        for off in range(4, max(1, len(window) - 8)):
            v = struct.unpack_from("<d", window, off)[0]
            if v == v and 1.0 <= v <= 240.0:
                return v
        return None

    def _first_sane_int(window):
        for off in range(4, max(1, len(window) - 4)):
            v = struct.unpack_from("<I", window, off)[0]
            if 1 <= v <= 240:
                return float(v)
        return None

    try:
        data = open(path, "rb").read()
        i = data.find(b"CustomFrameRate")
        if i >= 0:
            v = _first_sane_double(data[i:i + 64])
            if v:
                return v
        i = data.find(b"Frames per second")
        if i >= 0:
            v = _first_sane_int(data[i:i + 64])
            if v:
                return v
    except Exception:
        pass
    return default


def export_ifp(dff_path, ifp_path, model_name, anim_name=None,
               fbx_path=None):
    """Bake the scene's armature animation to an ANP3 IFP.

    Returns a status dict (never raises for 'no animation' cases):
        present: bool  success: bool  reason: str|None  anim: str|None
        frames: int  fps: float  bones: int  file: str|None
    """
    def done(**kw):
        base = {"present": False, "success": False, "reason": None,
                "anim": None, "frames": 0, "fps": 30.0, "bones": 0,
                "file": None}
        base.update(kw)
        return base

    armatures = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    if not armatures:
        return done(reason="no armature in scene (static model - nothing to "
                           "animate)")
    arm = max(armatures, key=lambda o: len(o.data.bones))

    # ---- animation detection (action keys on pose bones)
    ad = arm.animation_data
    action = (ad.action if (ad and ad.action) else None)
    has_keys = False
    if action:
        for fc in action.fcurves:
            if fc.data_path.startswith("pose.bones[") and fc.keyframe_points:
                has_keys = True
                break
    if not has_keys:
        # object-level animation of the armature also counts
        for fc in (arm.animation_data.action.fcurves if arm.animation_data
                   and arm.animation_data.action else []):
            if not fc.data_path.startswith("pose.bones[") and fc.keyframe_points:
                has_keys = True
                break
    if not has_keys:
        return done(reason="armature has no animation keys (static skeleton)")

    # ---- frame range + fps
    # the animation's own range defines the baked span (the scene range
    # after an FBX import is often the untouched Blender default 1..250)
    f0, f1 = None, None

    def _span(fr):
        nonlocal f0, f1
        a0, a1 = int(fr[0]), int(fr[1])
        f0 = a0 if f0 is None else min(f0, a0)
        f1 = a1 if f1 is None else max(f1, a1)

    for o in [arm] + [o for o in bpy.data.objects if o.type == "MESH"]:
        a = o.animation_data.action if o.animation_data else None
        if a is not None:
            _span(a.frame_range)
    if f0 is None:
        f0, f1 = (int(bpy.context.scene.frame_start),
                  int(bpy.context.scene.frame_end))
    if f1 < f0:
        f1 = f0
    # fps priority: the source FBX's declared rate (Blender's importer
    # discards it) -> scene fps -> 30
    fps = None
    if fbx_path and os.path.exists(fbx_path):
        fps = _fbx_fps(fbx_path)
    if fps is None:
        try:
            fps = float(bpy.context.scene.render.fps) / \
                float(bpy.context.scene.render.fps_base)
        except Exception:
            fps = 30.0
    if fps <= 0 or fps > 240:
        fps = 30.0
    frames = list(range(f0, f1 + 1))
    if len(frames) > 6553:  # int16 time ticks safety (60/s)
        step = len(frames) // 6553
        frames = frames[::step]

    # ---- bone ids
    # With a DFF: bone id = the exported DFF's frame id for the same name.
    # IFP-only (no DFF): bone id = rig order (index in the armature + 1),
    # which is EXACTLY the frame id the DFF of this same rig would get
    # (frame 0 = armature root, bones 1..N in armature order) - so the IFP
    # stays consistent with a DFF exported later from the same FBX.
    dff_frames = {}
    if dff_path and os.path.exists(dff_path):
        import sys
        sys.path.insert(0, str(__import__("os").path.join(
            __import__("os").path.dirname(__file__), "..", "dragonff",
            "DragonFF")))
        from gtaLib import dff as dffmod
        d = dffmod.dff()
        d.load_file(dff_path)
        for cl in d.clumps:
            for i, fr in enumerate(cl.frame_list):
                if fr.name not in dff_frames:
                    # RenderWare frame id = index in the clump's frame table
                    dff_frames[fr.name] = i
        dff_order = list(dff_frames)
        if not dff_frames:
            return done(present=True,
                        reason="DFF contains no frames - cannot map "
                               "animation bones to DFF frame ids")
        # keep only bones that exist in the DFF, in DFF (parent-first) order
        names = [n for n in dff_order if arm.data.bones.get(n)]
        if not names:
            return done(present=True,
                        reason="no animation bones match DFF frame names")
        skipped = [b.name for b in arm.data.bones if b.name not in dff_frames]
    else:
        names = [b.name for b in arm.data.bones]
        dff_frames = {n: i + 1 for i, n in enumerate(names)}
        skipped = []

    # ---- bake tracks: local matrix in parent space per frame
    pb_map = {b.name: b for b in arm.pose.bones}

    def bone_world(f, name):
        pb = pb_map[name]
        return arm.matrix_world @ pb.matrix  # evaluated at frame f

    tracks = {n: [] for n in names}
    for f in frames:
        bpy.context.scene.frame_set(f)
        t_s = (f - frames[0]) / fps
        # read all world matrices first (parent reads must not be affected
        # by anything, but batch-reading keeps it deterministic)
        worlds = {n: bone_world(f, n) for n in names}
        for n in names:
            pb = pb_map[n]
            if pb.parent is not None and pb.parent.name in worlds:
                local = worlds[pb.parent.name].inverted() @ worlds[n]
            else:
                local = worlds[n]  # root: clump space
            q = local.to_quaternion()
            tracks[n].append((t_s, (q.x, q.y, q.z, q.w),
                              tuple(local.translation)))

    # ---- write ANP3
    anim = anim_name or _sanitized(model_name, "anim")
    # ANP3 names are truncated to 23 chars - keep them unique after truncation
    write_names, used = {}, set()
    for n in names:
        base = _sanitized(n, "bone")
        k, c = base, 1
        while k in used:
            c += 1
            k = f"{base[:20]}_{c}"
        write_names[n], used = k, used | {k}
    bones = [{"name": write_names[n], "bone_id": int(dff_frames[n]),
              "keyframes": tracks[n]} for n in names]
    n_b, n_kf = gta_ifp.write_anp3(ifp_path, _sanitized(model_name, "anim"),
                                   anim, bones)
    if n_kf == 0:
        return done(present=True, reason="no keyframes baked")
    reason = None
    if skipped:
        reason = f"{len(skipped)} bone(s) without a DFF frame skipped: " \
                 + ", ".join(skipped[:5])
        if len(skipped) > 5:
            reason += f" (+{len(skipped) - 5} more)"
    return done(present=True, success=True, reason=reason, anim=anim,
                frames=len(frames), fps=fps, bones=n_b, file=ifp_path,
                id_source="DFF" if dff_path and os.path.exists(dff_path)
                else "rig-order")
