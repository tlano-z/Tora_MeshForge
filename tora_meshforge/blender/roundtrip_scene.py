"""Static FBX round trip and reload validation for Milestone 3."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
import traceback
from typing import Any

import bpy
from mathutils import Vector


PREFIX = "TMF_PROGRESS "


def progress(stage: str, fraction: float, message: str) -> None:
    print(PREFIX + json.dumps({"stage": stage, "progress": fraction, "message": message}), flush=True)


def arguments() -> argparse.Namespace:
    values = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--texture-override", type=Path)
    return parser.parse_args(values)


def clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    collections = (
        bpy.data.meshes,
        bpy.data.materials,
        bpy.data.images,
        bpy.data.armatures,
        bpy.data.actions,
        bpy.data.cameras,
        bpy.data.lights,
    )
    for collection in collections:
        for block in list(collection):
            collection.remove(block)


def import_model(path: Path) -> None:
    suffix = path.suffix.lower()
    if suffix == ".fbx":
        bpy.ops.import_scene.fbx(filepath=str(path), use_anim=True)
    elif suffix in {".glb", ".gltf"}:
        bpy.ops.import_scene.gltf(filepath=str(path))
    elif suffix == ".obj":
        if hasattr(bpy.ops.wm, "obj_import"):
            bpy.ops.wm.obj_import(filepath=str(path))
        else:
            bpy.ops.import_scene.obj(filepath=str(path))
    else:
        raise ValueError(f"Unsupported input format: {suffix}")


def apply_texture_override(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"requested": False, "used": False}
    if not path.is_file():
        raise FileNotFoundError(f"Texture override does not exist: {path}")
    missing = []
    for image in bpy.data.images:
        if image.source in {"VIEWER", "GENERATED"}:
            continue
        absolute = bpy.path.abspath(image.filepath) if image.filepath else ""
        if image.packed_file is None and not (absolute and Path(absolute).is_file()):
            missing.append(image)
    if len(missing) != 1:
        raise ValueError(f"Texture override requires exactly one unresolved image; found {len(missing)}.")
    image = missing[0]
    image.filepath = str(path.resolve())
    image.source = "FILE"
    image.reload()
    return {"requested": True, "used": True, "path": str(path.resolve()), "image": image.name}


def reject_unsupported_features() -> None:
    armatures = [obj.name for obj in bpy.context.scene.objects if obj.type == "ARMATURE"]
    shape_keys = [obj.name for obj in bpy.context.scene.objects if obj.type == "MESH" and obj.data.shape_keys]
    animated = bool(bpy.data.actions) or any(obj.animation_data is not None for obj in bpy.context.scene.objects)
    problems = []
    if armatures:
        problems.append(f"armatures: {', '.join(armatures)}")
    if shape_keys:
        problems.append(f"shape keys: {', '.join(shape_keys)}")
    if animated:
        problems.append("animation data")
    if problems:
        raise ValueError("Static round trip rejected unsupported features (" + "; ".join(problems) + ").")


def _bounds(meshes: list[Any]) -> dict[str, list[float]]:
    minimum = [math.inf, math.inf, math.inf]
    maximum = [-math.inf, -math.inf, -math.inf]
    for obj in meshes:
        for corner in obj.bound_box:
            world = obj.matrix_world @ Vector(corner)
            minimum = [min(minimum[index], float(world[index])) for index in range(3)]
            maximum = [max(maximum[index], float(world[index])) for index in range(3)]
    if not meshes:
        minimum = maximum = [0.0, 0.0, 0.0]
    return {"minimum": minimum, "maximum": maximum}


def snapshot_scene() -> dict[str, Any]:
    objects = list(bpy.context.scene.objects)
    meshes = [obj for obj in objects if obj.type == "MESH"]
    material_slots = {
        obj.name: [slot.material.name if slot.material else None for slot in obj.material_slots]
        for obj in meshes
    }
    image_entries = []
    for image in bpy.data.images:
        if image.source in {"VIEWER", "GENERATED"}:
            continue
        image_entries.append({
            "name": image.name,
            "width": int(image.size[0]),
            "height": int(image.size[1]),
            "packed": image.packed_file is not None,
            "filepath": image.filepath,
        })
    return {
        "objects": len(objects),
        "meshes": len(meshes),
        "vertices": sum(len(obj.data.vertices) for obj in meshes),
        "polygons": sum(len(obj.data.polygons) for obj in meshes),
        "triangles": sum(sum(max(0, len(poly.vertices) - 2) for poly in obj.data.polygons) for obj in meshes),
        "object_names": sorted(obj.name for obj in objects if obj.type in {"MESH", "EMPTY"}),
        "mesh_names": sorted(obj.name for obj in meshes),
        "hierarchy": sorted([
            {"name": obj.name, "parent": obj.parent.name if obj.parent else None}
            for obj in objects
            if obj.type in {"MESH", "EMPTY"}
        ], key=lambda item: item["name"]),
        "material_slots": material_slots,
        "materials": sorted(material.name for material in bpy.data.materials),
        "textures": image_entries,
        "bounding_box": _bounds(meshes),
    }


def export_static_fbx(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.export_scene.fbx(
        filepath=str(path),
        use_selection=False,
        object_types={"EMPTY", "MESH"},
        use_mesh_modifiers=True,
        use_custom_props=True,
        add_leaf_bones=False,
        bake_anim=False,
        path_mode="COPY",
        embed_textures=True,
        axis_forward="-Z",
        axis_up="Y",
    )
    if not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError("Blender did not create a non-empty FBX output.")


def _bounds_close(source: dict[str, Any], output: dict[str, Any]) -> tuple[bool, float]:
    values = []
    for key in ("minimum", "maximum"):
        values.extend(abs(float(a) - float(b)) for a, b in zip(source[key], output[key]))
    delta = max(values, default=0.0)
    source_values = [abs(float(value)) for key in ("minimum", "maximum") for value in source[key]]
    scale = max(source_values, default=1.0)
    tolerance = max(1e-6, scale * 1e-4)
    return delta <= tolerance, delta


def validate_snapshots(source: dict[str, Any], output: dict[str, Any]) -> dict[str, Any]:
    bounds_match, bounds_delta = _bounds_close(source["bounding_box"], output["bounding_box"])
    checks = {
        "mesh_count_preserved": source["meshes"] == output["meshes"],
        "vertex_count_preserved": source["vertices"] == output["vertices"],
        "triangle_count_preserved": source["triangles"] == output["triangles"],
        "object_names_preserved": source["object_names"] == output["object_names"],
        "mesh_names_preserved": source["mesh_names"] == output["mesh_names"],
        "hierarchy_preserved": source["hierarchy"] == output["hierarchy"],
        "material_slots_preserved": source["material_slots"] == output["material_slots"],
        "bounding_box_preserved": bounds_match,
    }
    warnings = []
    if len(output["textures"]) < len(source["textures"]):
        warnings.append("The reloaded FBX exposes fewer texture images than the source scene.")
    failed = [name for name, passed in checks.items() if not passed]
    return {
        "passed": not failed,
        "checks": checks,
        "failed_checks": failed,
        "bounding_box_max_delta": bounds_delta,
        "warnings": warnings,
    }


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temporary.replace(path)


def main() -> int:
    args = arguments()
    try:
        progress("scene", 0.04, "Clearing Blender scene")
        clear_scene()
        progress("import", 0.12, f"Importing {args.input.name}")
        import_model(args.input)
        texture_override = apply_texture_override(args.texture_override)
        reject_unsupported_features()
        progress("analyze", 0.34, "Capturing source scene metrics")
        source = snapshot_scene()
        if source["meshes"] == 0:
            raise ValueError("Static round trip requires at least one mesh object.")
        progress("export", 0.48, "Exporting static FBX")
        export_static_fbx(args.output)
        progress("reload", 0.70, "Reloading exported FBX")
        clear_scene()
        import_model(args.output)
        progress("validate", 0.86, "Validating reloaded output")
        output = snapshot_scene()
        validation = validate_snapshots(source, output)
        if not validation["passed"]:
            raise RuntimeError("Round-trip validation failed: " + ", ".join(validation["failed_checks"]))
        report = {
            "operation": "static_fbx_round_trip",
            "source": source,
            "output": {**output, "path": str(args.output.resolve()), "file_size_bytes": args.output.stat().st_size},
            "texture_override": texture_override,
            "validation": validation,
            "warnings": validation["warnings"],
            "errors": [],
        }
        progress("report", 0.96, "Writing round-trip report")
        write_report(args.report, report)
        return 0
    except Exception as exc:
        error = {
            "operation": "static_fbx_round_trip",
            "status": "failure",
            "warnings": [],
            "errors": [{"type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc()}],
        }
        try:
            write_report(args.report, error)
        finally:
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
