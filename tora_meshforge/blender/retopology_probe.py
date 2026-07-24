"""Compare topology-rebuilding strategies on a static mesh fixture."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
import time
import traceback
from typing import Any

import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from roundtrip_scene import clear_scene, import_model  # noqa: E402


def arguments() -> argparse.Namespace:
    values = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--method", choices=("quadriflow", "voxel", "voxel-quadriflow"), required=True)
    parser.add_argument("--target-triangles", type=int, default=50_000)
    parser.add_argument("--voxel-divisions", type=int, default=512)
    return parser.parse_args(values)


class IndexGroups:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))
        self.weight = [1] * size

    def find(self, value: int) -> int:
        while self.parent[value] != value:
            self.parent[value] = self.parent[self.parent[value]]
            value = self.parent[value]
        return value

    def union(self, first: int, second: int) -> None:
        first = self.find(first)
        second = self.find(second)
        if first == second:
            return
        if self.weight[first] < self.weight[second]:
            first, second = second, first
        self.parent[second] = first
        self.weight[first] += self.weight[second]


def triangle_count(obj: Any) -> int:
    return sum(max(0, len(polygon.vertices) - 2) for polygon in obj.data.polygons)


def object_diagonal(obj: Any) -> float:
    minimum = Vector((math.inf, math.inf, math.inf))
    maximum = Vector((-math.inf, -math.inf, -math.inf))
    for corner in obj.bound_box:
        world = obj.matrix_world @ Vector(corner)
        for axis in range(3):
            minimum[axis] = min(minimum[axis], world[axis])
            maximum[axis] = max(maximum[axis], world[axis])
    return max(1e-12, (maximum - minimum).length)


def select_only(obj: Any) -> None:
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    obj.hide_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def topology_metrics(obj: Any) -> dict[str, Any]:
    groups = IndexGroups(len(obj.data.polygons))
    edge_faces: dict[tuple[int, int], list[int]] = {}
    degenerate_faces = 0
    for polygon in obj.data.polygons:
        vertices = [int(vertex) for vertex in polygon.vertices]
        if len(set(vertices)) != len(vertices) or polygon.area <= 1e-18:
            degenerate_faces += 1
        for index, first in enumerate(vertices):
            second = vertices[(index + 1) % len(vertices)]
            edge_faces.setdefault(tuple(sorted((first, second))), []).append(polygon.index)
    for faces in edge_faces.values():
        for face in faces[1:]:
            groups.union(faces[0], face)
    return {
        "vertices": len(obj.data.vertices),
        "polygons": len(obj.data.polygons),
        "triangles": triangle_count(obj),
        "components": len({groups.find(index) for index in range(len(obj.data.polygons))}),
        "edges": len(edge_faces),
        "boundary_edges": sum(len(faces) == 1 for faces in edge_faces.values()),
        "overused_edges": sum(len(faces) > 2 for faces in edge_faces.values()),
        "degenerate_faces": degenerate_faces,
    }


def duplicate_target(source: Any) -> Any:
    target = source.copy()
    target.data = source.data.copy()
    target.name = "RetopologyProbe"
    source.users_collection[0].objects.link(target)
    select_only(target)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    return target


def apply_decimate(obj: Any, target_triangles: int) -> None:
    current = triangle_count(obj)
    if current <= target_triangles:
        return
    modifier = obj.modifiers.new(name="ProbeTargetDecimate", type="DECIMATE")
    modifier.decimate_type = "COLLAPSE"
    modifier.ratio = max(0.0001, min(1.0, target_triangles / current))
    modifier.use_collapse_triangulate = True
    select_only(obj)
    bpy.ops.object.modifier_apply(modifier=modifier.name)
    obj.data.validate(clean_customdata=False)
    obj.data.update(calc_edges=True)


def run_quadriflow(obj: Any, target_triangles: int) -> dict[str, Any]:
    select_only(obj)
    target_faces = max(1_000, target_triangles // 2)
    result = bpy.ops.object.quadriflow_remesh(
        use_mesh_symmetry=False,
        use_preserve_sharp=True,
        use_preserve_boundary=False,
        preserve_attributes=False,
        smooth_normals=True,
        mode="FACES",
        target_faces=target_faces,
        seed=0,
    )
    if "FINISHED" not in result:
        raise RuntimeError(f"Quadriflow did not finish: {sorted(result)}")
    apply_decimate(obj, target_triangles)
    return {"target_faces": target_faces, "operator_result": sorted(result)}


def apply_voxel_remesh(obj: Any, divisions: int) -> dict[str, Any]:
    diagonal = object_diagonal(obj)
    voxel_size = diagonal / max(64, divisions)
    obj.data.remesh_voxel_size = voxel_size
    obj.data.remesh_voxel_adaptivity = 0.0
    obj.data.use_remesh_preserve_volume = True
    obj.data.use_remesh_fix_poles = True
    obj.data.use_remesh_preserve_attributes = False
    select_only(obj)
    result = bpy.ops.object.voxel_remesh()
    if "FINISHED" not in result:
        raise RuntimeError(f"Voxel Remesh did not finish: {sorted(result)}")
    select_only(obj)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.normals_make_consistent(inside=False)
    bpy.ops.object.mode_set(mode="OBJECT")
    obj.data.validate(clean_customdata=False)
    obj.data.update(calc_edges=True)
    return {
        "voxel_divisions": divisions,
        "voxel_size": voxel_size,
        "operator_result": sorted(result),
        "remesh_result": topology_metrics(obj),
    }


def run_voxel(obj: Any, target_triangles: int, divisions: int) -> dict[str, Any]:
    result = apply_voxel_remesh(obj, divisions)
    apply_decimate(obj, target_triangles)
    return result


def run_voxel_quadriflow(obj: Any, target_triangles: int, divisions: int) -> dict[str, Any]:
    voxel = apply_voxel_remesh(obj, divisions)
    quadriflow = run_quadriflow(obj, target_triangles)
    return {"voxel": voxel, "quadriflow": quadriflow}


def export_target(obj: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    select_only(obj)
    bpy.ops.export_scene.fbx(
        filepath=str(path.resolve()),
        use_selection=True,
        object_types={"MESH"},
        use_mesh_modifiers=True,
        use_custom_props=False,
        add_leaf_bones=False,
        bake_anim=False,
        path_mode="AUTO",
        axis_forward="-Z",
        axis_up="Y",
    )
    if not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError("Retopology probe did not create an FBX output.")


def main() -> int:
    args = arguments()
    started = time.perf_counter()
    try:
        args.input = args.input.expanduser().resolve()
        args.output = args.output.expanduser().resolve()
        args.report = args.report.expanduser().resolve()
        clear_scene()
        import_model(args.input)
        sources = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
        if len(sources) != 1:
            raise ValueError(f"Retopology probe currently expects one mesh object; found {len(sources)}.")
        source = sources[0]
        source_metrics = topology_metrics(source)
        target = duplicate_target(source)
        source.hide_render = True
        source.hide_set(True)
        if args.method == "quadriflow":
            method = run_quadriflow(target, args.target_triangles)
        elif args.method == "voxel":
            method = run_voxel(target, args.target_triangles, args.voxel_divisions)
        else:
            method = run_voxel_quadriflow(target, args.target_triangles, args.voxel_divisions)
        target_metrics = topology_metrics(target)
        export_target(target, args.output)
        report = {
            "operation": "retopology_probe",
            "method": args.method,
            "input": str(args.input),
            "output": str(args.output),
            "target_triangles": args.target_triangles,
            "source": source_metrics,
            "result": target_metrics,
            "method_details": method,
            "elapsed_seconds": time.perf_counter() - started,
            "passed_basic_validation": (
                target_metrics["triangles"] <= args.target_triangles * 1.02
                and target_metrics["triangles"] >= args.target_triangles * 0.5
                and target_metrics["degenerate_faces"] == 0
            ),
        }
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("TMF_RETOPOLOGY_PROBE " + json.dumps(report), flush=True)
        return 0
    except Exception as exc:
        failure = {
            "operation": "retopology_probe",
            "method": args.method,
            "errors": [{"type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc()}],
        }
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(failure, indent=2), encoding="utf-8")
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
