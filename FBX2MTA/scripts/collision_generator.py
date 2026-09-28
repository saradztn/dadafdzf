"""Collision (COL) generation for MTA:SA - REAL collision geometry.

Runs AFTER the DFF export (inside the Blender/bpy process) and:
  1. duplicates the processed mesh (the exact geometry exported to the DFF,
     already in identity-transform world space  =>  DFF and COL live in the
     SAME space/scale/rotation/position - transforms match exactly);
  2. decimates it to the collision triangle budget (AUTO/LOW/MEDIUM/HIGH/CUSTOM)
     - the COLLAPSE decimator preserves the outer silhouette, floors, walls
     and major shapes while shedding interior detail;
  3. cleans it with bmesh: remove doubles, dissolve degenerates, remove loose
     verts, remove disconnected micro-islands (useless interior/decoration
     leftovers), triangulate, validate no NaN/Infinity;
  4. tags the object  dff.type = 'COL'  and exports it with DragonFF's
     OFFICIAL COL operator  bpy.ops.export_col.scene  (export_version '3' =
     GTA SA COL3, compatible with engineLoadCOL()/engineReplaceCOL() in MTA:SA).

No fake/empty COL: the export is verified for a non-empty file, and the
pipeline driver re-parses the file with DragonFF's own col module afterwards.
"""
import math

import bmesh


# triangle budgets for the quality presets (AUTO is model-size dependent)
QUALITY_BUDGETS = {
    "LOW": 1000,
    "MEDIUM": 3000,
    "HIGH": 10000,
}


def pick_collision_budget(orig_tris, quality="AUTO", custom_max=None, log=print):
    """Choose the target collision triangle count (never above the source).

    AUTO:
        small model  (<= 2000 tris)  -> keep full detail (minimal reduction)
        medium model (<= 10000 tris) -> 3000 tris
        large model  (> 10000 tris)  -> 5000 tris
    """
    q = (quality or "AUTO").upper()
    if q == "AUTO":
        if orig_tris <= 2000:
            budget, why = orig_tris, "small model - collision kept at full detail"
        elif orig_tris <= 10000:
            budget, why = 3000, "medium model - reduced to 3000 tris"
        else:
            budget, why = 5000, "large model - reduced to 5000 tris"
    elif q == "CUSTOM":
        budget, why = int(custom_max or 3000), f"custom maximum ({custom_max} tris)"
    else:
        budget, why = QUALITY_BUDGETS[q], f"quality preset {q}"
    budget = max(1, min(int(budget), orig_tris))
    log(f"Original collision triangles: {orig_tris}")
    log(f"Target collision triangles: {budget} ({why})")
    return budget


def _bmesh_cleanup(bm, log):
    """Cleanup pass: doubles, degenerates, loose verts, tiny islands.

    Returns stats dict. Raises RuntimeError on non-finite coordinates.
    """
    # 1. merge duplicate vertices
    bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=0.001)

    # 2. dissolve degenerate (zero-length) edges
    zero_edges = [e for e in bm.edges if e.calc_length() < 1e-6]
    if zero_edges:
        bmesh.ops.dissolve_degenerate(bm, edges=zero_edges, dist=0.001)

    # 3. remove loose (face-less) vertices
    loose = [v for v in bm.verts if not v.link_faces]
    if loose:
        bmesh.ops.delete(bm, geom=loose, context="VERTS")

    # 4. remove disconnected micro-islands (< 0.5% of total area) -
    #    interior geometry / small decoration leftovers that only waste
    #    collision space. The main body (largest component) is always kept.
    if len(bm.faces) > 16:
        bm.faces.ensure_lookup_table()
        seen = set()
        islands = []
        for f in bm.faces:
            if f.index in seen:
                continue
            island = set()
            stack = [f]
            while stack:
                cf = stack.pop()
                if cf.index in island:
                    continue
                island.add(cf.index)
                for e in cf.edges:
                    for lf in e.link_faces:
                        if lf.index != cf.index and lf.index not in island:
                            stack.append(lf)
            seen |= island
            islands.append(island)
        if len(islands) > 1:
            areas = sorted(
                ((sum(bm.faces[i].calc_area() for i in isl), isl) for isl in islands),
                key=lambda t: t[0], reverse=True,
            )
            total = sum(a for a, _ in areas) or 1.0
            removed = set()
            for area, isl in areas[1:]:
                if area / total < 0.005:
                    removed |= isl
            if removed:
                bm.faces.ensure_lookup_table()
                bmesh.ops.delete(bm, geom=[bm.faces[i] for i in removed],
                                 context="FACES")
                log(f"Collision cleanup: removed {len(removed)} faces of "
                    f"disconnected micro-islands")

    # 5. vertices that became loose again
    loose = [v for v in bm.verts if not v.link_faces]
    if loose:
        bmesh.ops.delete(bm, geom=loose, context="VERTS")

    # 6. validate: no NaN / Infinity in any coordinate
    for v in bm.verts:
        co = v.co
        if not (math.isfinite(co.x) and math.isfinite(co.y) and math.isfinite(co.z)):
            raise RuntimeError("collision mesh contains NaN/Infinity after cleanup")

    return {"verts": len(bm.verts), "faces": len(bm.faces)}


def _decimate(obj, target_tris, log):
    """Collapse-decimate the object to ~target_tris. Returns the new tri count.

    The COLLAPSE collapse mode removes geometry by area importance, so the
    outer silhouette / floors / walls / major shapes are preserved while
    interior detail goes first.
    """
    cur = len(obj.data.polygons)
    if target_tris >= cur:
        return cur
    ratio = max(target_tris / cur, 0.001)
    mod = obj.modifiers.new("COL_Decimate", "DECIMATE")
    mod.decimate_type = "COLLAPSE"
    mod.ratio = ratio
    import bpy
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.modifier_apply(modifier=mod.name)
    new_count = len(obj.data.polygons)
    log(f"Collision decimation: {cur} -> {new_count} triangles (ratio {ratio:.3f})")
    return new_count


def build_collision_mesh(source_objs, budget, log):
    """Create the collision object from the processed model mesh(es).

    source_objs: list of the processed mesh objects (the ones exported to the
    DFF). A single object is duplicated; several are joined into one.
    Returns (col_obj, stats). Raises on failure - caller must remove col_obj.
    """
    import bpy

    log(f"[INFO] Creating collision mesh from {len(source_objs)} object(s)")

    # duplicate (join when there is more than one mesh object)
    dups = []
    for ob in source_objs:
        d = ob.copy()
        d.data = ob.data.copy()
        ob.users_collection[0].objects.link(d)
        dups.append(d)
    if len(dups) > 1:
        bpy.ops.object.select_all(action="DESELECT")
        for d in dups:
            d.select_set(True)
        bpy.context.view_layer.objects.active = dups[0]
        bpy.ops.object.join()
    col_obj = dups[0]
    col_obj.name = "COL_collision"
    col_obj.data.name = "COL_collision"

    # vertex weights are meaningless for collision - drop them
    for vg in list(col_obj.vertex_groups):
        col_obj.vertex_groups.remove(vg)

    orig_tris = len(col_obj.data.polygons)
    final_tris = _decimate(col_obj, budget, log)

    # triangulate + full cleanup in bmesh
    me = col_obj.data
    bm = bmesh.new()
    bm.from_mesh(me)
    bmesh.ops.triangulate(bm, faces=list(bm.faces))
    stats = _bmesh_cleanup(bm, log)
    bm.to_mesh(me)
    bm.free()
    me.update()
    tris = len(me.polygons)
    log(f"Collision optimized: {stats['verts']} vertices / {tris} triangles")

    # tag as DragonFF collision object
    col_obj.dff.type = "COL"
    return col_obj, {
        "original_tris": orig_tris,
        "decimated_tris": final_tris,
        "final_tris": tris,
        "verts": len(me.vertices),
    }


def export_col(col_obj, filepath, log):
    """Export the collision object with DragonFF's official COL operator.

    export_version '3' = GTA SA (PC/Xbox) COL3 - the format used by
    engineLoadCOL()/engineReplaceCOL() in MTA:SA.
    Raises RuntimeError when no usable file is produced.
    """
    import bpy

    log(f"[INFO] Exporting COL using DragonFF: {filepath}")
    bpy.ops.object.select_all(action="DESELECT")
    col_obj.select_set(True)
    bpy.context.view_layer.objects.active = col_obj
    r = bpy.ops.export_col.scene(
        filepath=filepath,
        export_version="3",
        only_selected=True,
        apply_transformations=True,   # matrix_world (identity here) -> same space as DFF
        clean_mesh=True,              # 1/128 quantization + zero-area face removal
        export_face_groups=False,
    )
    import os
    ok = (r == {"FINISHED"}) and os.path.exists(filepath) and os.path.getsize(filepath) > 0
    if not ok:
        raise RuntimeError(f"COL export produced no usable file (op result={r})")
    size = os.path.getsize(filepath)
    log(f"[SUCCESS] model.col created: {filepath} ({size} bytes)")
    return size


def remove_collision_obj(col_obj, log=None):
    """Delete a collision object from the scene (cleanup after export/failure)."""
    import bpy
    if col_obj is None:
        return
    try:
        bpy.ops.object.select_all(action="DESELECT")
        col_obj.select_set(True)
        bpy.context.view_layer.objects.active = col_obj
        bpy.ops.object.delete(use_global=False)
    except Exception as e:  # pragma: no cover - scene hygiene only
        if log:
            log(f"[WARN] could not delete collision object: {e}")
