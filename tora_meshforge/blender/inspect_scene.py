"""Headless Blender scene inspector. Run with Blender, not regular Python."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import platform
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
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--original-path", type=Path)
    parser.add_argument("--texture-override", type=Path)
    return parser.parse_args(values)


def clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for collection in (bpy.data.meshes, bpy.data.materials, bpy.data.images, bpy.data.armatures, bpy.data.actions):
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
    """Replace one unresolved image without guessing across multiple materials."""
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
        return {
            "requested": True,
            "used": False,
            "path": str(path.resolve()),
            "reason": f"Expected exactly one unresolved source image, found {len(missing)}.",
        }
    image = missing[0]
    image.filepath = str(path.resolve())
    image.source = "FILE"
    image.reload()
    return {"requested": True, "used": True, "path": str(path.resolve()), "image": image.name}


def vector_min(left: list[float], right: Any) -> list[float]:
    return [min(left[index], float(right[index])) for index in range(3)]


def vector_max(left: list[float], right: Any) -> list[float]:
    return [max(left[index], float(right[index])) for index in range(3)]


def inspect_geometry() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    minimum = [math.inf, math.inf, math.inf]
    maximum = [-math.inf, -math.inf, -math.inf]
    objects: list[dict[str, Any]] = []
    vertices = polygons = triangles = 0
    for index, obj in enumerate(meshes):
        mesh = obj.data
        object_triangles = sum(max(0, len(poly.vertices) - 2) for poly in mesh.polygons)
        vertices += len(mesh.vertices)
        polygons += len(mesh.polygons)
        triangles += object_triangles
        object_min = [math.inf, math.inf, math.inf]
        object_max = [-math.inf, -math.inf, -math.inf]
        for corner in obj.bound_box:
            # Blender 4.2 exposes bound_box corners as bpy_prop_array values,
            # which must be converted before Matrix multiplication.
            world = obj.matrix_world @ Vector(corner)
            object_min = vector_min(object_min, world)
            object_max = vector_max(object_max, world)
        minimum = vector_min(minimum, object_min)
        maximum = vector_max(maximum, object_max)
        objects.append({
            "name": obj.name,
            "vertices": len(mesh.vertices),
            "polygons": len(mesh.polygons),
            "triangles": object_triangles,
            "material_slots": len(obj.material_slots),
            "uv_layers": len(mesh.uv_layers),
            "bounding_box": {"minimum": object_min, "maximum": object_max},
        })
        progress("geometry", 0.35 + 0.3 * ((index + 1) / max(1, len(meshes))), f"Inspecting mesh {index + 1}/{len(meshes)}")
    if not meshes:
        minimum = maximum = [0.0, 0.0, 0.0]
    return {
        "objects": len(bpy.context.scene.objects),
        "meshes": len(meshes),
        "vertices": vertices,
        "polygons": polygons,
        "triangles": triangles,
        "materials": len(bpy.data.materials),
        "bounding_box": {"minimum": minimum, "maximum": maximum},
    }, objects


def inspect_textures() -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    missing: list[str] = []
    maximum_dimension = 0
    for image in bpy.data.images:
        if image.source in {"VIEWER", "GENERATED"}:
            continue
        width, height = int(image.size[0]), int(image.size[1])
        maximum_dimension = max(maximum_dimension, width, height)
        absolute = bpy.path.abspath(image.filepath) if image.filepath else ""
        is_packed = image.packed_file is not None
        exists = bool(absolute and Path(absolute).is_file()) or is_packed
        if not exists:
            missing.append(image.name)
        entries.append({
            "name": image.name,
            "width": width,
            "height": height,
            "source": image.source,
            "filepath": image.filepath,
            "resolved_path": absolute,
            "packed": is_packed,
            "exists": exists,
        })
    return {"count": len(entries), "maximum_dimension": maximum_dimension, "images": entries, "missing_files": missing}


def inspect_features() -> dict[str, Any]:
    shape_objects = [obj.name for obj in bpy.context.scene.objects if obj.type == "MESH" and obj.data.shape_keys]
    armatures = [obj.name for obj in bpy.context.scene.objects if obj.type == "ARMATURE"]
    has_animation = bool(bpy.data.actions)
    if not has_animation:
        has_animation = any(obj.animation_data is not None for obj in bpy.context.scene.objects)
    return {
        "armature": bool(armatures),
        "armature_objects": armatures,
        "animation": has_animation,
        "action_count": len(bpy.data.actions),
        "shape_keys": bool(shape_objects),
        "shape_key_objects": shape_objects,
    }


def inspect_devices() -> dict[str, Any]:
    devices: list[dict[str, Any]] = []
    try:
        preferences = bpy.context.preferences.addons["cycles"].preferences
        preferences.get_devices()
        for device in preferences.devices:
            devices.append({"name": device.name, "type": device.type, "available": True, "enabled": bool(device.use)})
    except Exception as exc:
        devices.append({"name": "Cycles detection failed", "type": "UNKNOWN", "available": False, "error": str(exc)})
    return {
        "cpu": {"name": platform.processor() or platform.machine(), "logical_threads": os.cpu_count()},
        "blender": {"version": bpy.app.version_string, "python": platform.python_version()},
        "render_devices": devices,
        "gpu_available": any(item["type"] not in {"CPU", "UNKNOWN"} and item["available"] for item in devices),
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
    original = args.original_path or args.input
    try:
        progress("scene", 0.05, "Clearing Blender scene")
        clear_scene()
        progress("import", 0.12, f"Importing {original.name}")
        import_model(args.input)
        texture_override = apply_texture_override(args.texture_override)
        progress("geometry", 0.35, "Collecting geometry statistics")
        geometry, objects = inspect_geometry()
        progress("textures", 0.70, "Inspecting textures")
        textures = inspect_textures()
        progress("features", 0.78, "Detecting unsupported features")
        features = inspect_features()
        progress("devices", 0.86, "Detecting render devices")
        devices = inspect_devices()
        report = {
            "source": {
                "path": str(original.resolve()),
                "format": original.suffix.lower().lstrip("."),
                "file_size_bytes": args.input.stat().st_size,
            },
            "geometry": geometry,
            "objects": objects,
            "textures": textures,
            "texture_override": texture_override,
            "features": features,
            "devices": devices,
            "warnings": [],
            "errors": [],
        }
        progress("report", 0.94, "Writing inspection report")
        write_report(args.report, report)
        return 0
    except Exception as exc:
        error = {"status": "failure", "errors": [{"type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc()}]}
        try:
            write_report(args.report, error)
        finally:
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
