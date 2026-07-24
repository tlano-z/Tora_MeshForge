"""Fast Optimize: per-object Blender collapse decimation with UV preservation."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
import traceback
from typing import Any

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
from roundtrip_scene import (  # noqa: E402
    apply_texture_override,
    clear_scene,
    export_static_fbx,
    import_model,
    progress,
    reject_unsupported_features,
    snapshot_scene,
    write_report,
)


def arguments() -> argparse.Namespace:
    values = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--target-triangles", type=int, required=True)
    parser.add_argument("--texture-override", type=Path)
    parser.add_argument("--preserve-small-parts", action="store_true")
    return parser.parse_args(values)


def triangle_count(obj: Any) -> int:
    return sum(max(0, len(poly.vertices) - 2) for poly in obj.data.polygons)


def scene_snapshot() -> dict[str, Any]:
    result = snapshot_scene()
    result["uv_layers"] = {
        obj.name: [layer.name for layer in obj.data.uv_layers]
        for obj in bpy.context.scene.objects
        if obj.type == "MESH"
    }
    return result


def apply_decimate(obj: Any, ratio: float) -> None:
    if ratio >= 0.999999:
        return
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    modifier = obj.modifiers.new(name="ToraMeshForge_Decimate", type="DECIMATE")
    modifier.decimate_type = "COLLAPSE"
    modifier.ratio = max(0.0001, min(1.0, ratio))
    modifier.use_collapse_triangulate = True
    bpy.ops.object.modifier_apply(modifier=modifier.name)
    obj.data.validate(clean_customdata=False)
    obj.data.update(calc_edges=True)


def decimate_scene(target: int, preserve_small_parts: bool) -> dict[str, Any]:
    objects = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    counts = {obj.name: triangle_count(obj) for obj in objects}
    source_total = sum(counts.values())
    if source_total <= 0:
        raise ValueError("Fast Optimize requires at least one non-empty mesh.")
    if target >= source_total:
        return {
            "requested_target": target,
            "source_triangles": source_total,
            "protected_objects": [],
            "object_source_triangles": counts,
            "passes": 0,
            "message": "The source already meets the requested triangle target.",
        }

    small_threshold = min(2_000, max(100, int(source_total * 0.005)))
    protected = {
        obj.name
        for obj in objects
        if preserve_small_parts and len(objects) > 1 and counts[obj.name] <= small_threshold
    }
    protected_total = sum(counts[name] for name in protected)
    reducible = [obj for obj in objects if obj.name not in protected]
    reducible_total = sum(counts[obj.name] for obj in reducible)
    budget = max(4 * len(reducible), target - protected_total)
    if reducible_total <= 0:
        raise ValueError("All mesh objects are protected as small parts; the target cannot be reached.")

    passes = 0
    ratio = min(1.0, budget / reducible_total)
    for index, obj in enumerate(reducible):
        progress("decimate", 0.34 + 0.24 * ((index + 1) / len(reducible)), f"Decimating object {index + 1}/{len(reducible)}")
        apply_decimate(obj, ratio)
    passes += 1

    # Collapse ratios are approximate. Correct a small overshoot with at most
    # two additional proportional passes while keeping protected objects intact.
    for _ in range(2):
        current_reducible = sum(triangle_count(obj) for obj in reducible)
        current_total = protected_total + current_reducible
        tolerance = max(50, int(target * 0.005))
        if current_total <= target + tolerance or current_reducible <= 4 * len(reducible):
            break
        correction = max(0.0001, (target - protected_total) / current_reducible)
        for obj in reducible:
            apply_decimate(obj, correction)
        passes += 1

    return {
        "requested_target": target,
        "source_triangles": source_total,
        "protected_objects": sorted(protected),
        "protected_triangles": protected_total,
        "small_part_threshold": small_threshold,
        "object_source_triangles": counts,
        "passes": passes,
    }


def bounds_delta(source: dict[str, Any], output: dict[str, Any]) -> tuple[float, float]:
    deltas = [
        abs(float(a) - float(b))
        for key in ("minimum", "maximum")
        for a, b in zip(source[key], output[key])
    ]
    diagonal = math.sqrt(sum((float(b) - float(a)) ** 2 for a, b in zip(source["minimum"], source["maximum"])))
    return max(deltas, default=0.0), max(1e-6, diagonal * 0.02)


def validate_optimized(source: dict[str, Any], output: dict[str, Any], target: int) -> dict[str, Any]:
    delta, tolerance = bounds_delta(source["bounding_box"], output["bounding_box"])
    target_tolerance = max(50, int(target * 0.01))
    source_uv = source["uv_layers"]
    output_uv = output["uv_layers"]
    uv_preserved = all(len(output_uv.get(name, [])) >= len(layers) for name, layers in source_uv.items())
    source_texture_sizes = sorted((item["width"], item["height"]) for item in source["textures"])
    output_texture_sizes = sorted((item["width"], item["height"]) for item in output["textures"])
    checks = {
        "output_reloaded": output["meshes"] > 0,
        "triangle_count_reduced": output["triangles"] < source["triangles"] or target >= source["triangles"],
        "target_reached": output["triangles"] <= target + target_tolerance,
        "object_names_preserved": source["object_names"] == output["object_names"],
        "mesh_names_preserved": source["mesh_names"] == output["mesh_names"],
        "hierarchy_preserved": source["hierarchy"] == output["hierarchy"],
        "material_slots_preserved": source["material_slots"] == output["material_slots"],
        "uv_layers_preserved": uv_preserved,
        "texture_count_preserved": len(output["textures"]) >= len(source["textures"]),
        "texture_dimensions_preserved": source_texture_sizes == output_texture_sizes,
        "bounding_box_within_tolerance": delta <= tolerance,
    }
    failed = [name for name, passed in checks.items() if not passed]
    warnings = []
    ratio = output["triangles"] / max(1, source["triangles"])
    if ratio <= 0.1:
        warnings.append("Aggressive reduction may damage silhouettes, thin parts, or facial features; visual review is required.")
    return {
        "passed": not failed,
        "checks": checks,
        "failed_checks": failed,
        "target_tolerance": target_tolerance,
        "bounding_box_max_delta": delta,
        "bounding_box_tolerance": tolerance,
        "warnings": warnings,
    }


def main() -> int:
    args = arguments()
    try:
        if args.target_triangles < 1_000:
            raise ValueError("Target triangles must be at least 1,000.")
        progress("scene", 0.04, "Clearing Blender scene")
        clear_scene()
        progress("import", 0.10, f"Importing {args.input.name}")
        import_model(args.input)
        texture_override = apply_texture_override(args.texture_override)
        reject_unsupported_features()
        progress("analyze", 0.25, "Capturing source geometry and UV metrics")
        source = scene_snapshot()
        decimation = decimate_scene(args.target_triangles, args.preserve_small_parts)
        progress("export", 0.62, "Exporting optimized static FBX")
        export_static_fbx(args.output)
        progress("reload", 0.76, "Reloading optimized FBX")
        clear_scene()
        import_model(args.output)
        progress("validate", 0.88, "Validating optimized output")
        output = scene_snapshot()
        validation = validate_optimized(source, output, args.target_triangles)
        if not validation["passed"]:
            raise RuntimeError("Fast Optimize validation failed: " + ", ".join(validation["failed_checks"]))
        report = {
            "operation": "fast_optimize",
            "backend": "blender_decimate",
            "source": source,
            "output": {**output, "path": str(args.output.resolve()), "file_size_bytes": args.output.stat().st_size},
            "decimation": {
                **decimation,
                "output_triangles": output["triangles"],
                "reduction_ratio": output["triangles"] / max(1, source["triangles"]),
            },
            "texture_override": texture_override,
            "validation": validation,
            "warnings": validation["warnings"],
            "errors": [],
        }
        progress("report", 0.96, "Writing Fast Optimize report")
        write_report(args.report, report)
        return 0
    except Exception as exc:
        error = {
            "operation": "fast_optimize",
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
