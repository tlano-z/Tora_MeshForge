"""Milestone 5 runtime rebuild with new UVs and Base Color baking."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
import sys
import traceback
from typing import Any

import bpy
from mathutils import Vector
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from roundtrip_scene import (  # noqa: E402
    apply_texture_override,
    clear_scene,
    import_model,
    progress,
    reject_unsupported_features,
    snapshot_scene,
    write_report,
)


ALLOWED_RESOLUTIONS = (512, 1024, 2048, 4096, 8192)
SOURCE_UV_NAME = "ToraMeshForgeSourceUV"
RUNTIME_UV_NAME = "ToraMeshForgeUV"


def arguments() -> argparse.Namespace:
    values = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--bake-dir", type=Path, required=True)
    parser.add_argument("--target-triangles", type=int, required=True)
    parser.add_argument("--texture-override", type=Path)
    parser.add_argument("--texture-resolution-mode", choices=("auto", "match-source", "manual"), default="auto")
    parser.add_argument("--manual-texture-resolution", type=int)
    parser.add_argument("--maximum-texture-resolution", type=int, default=4096)
    parser.add_argument("--uv-mode", choices=("smart", "angle", "consolidated"), default="consolidated")
    parser.add_argument("--uv-margin-pixels", type=int, default=4)
    parser.add_argument("--preserve-small-parts", action="store_true")
    return parser.parse_args(values)


def triangle_count(obj: Any) -> int:
    return sum(max(0, len(poly.vertices) - 2) for poly in obj.data.polygons)


def scene_diagonal(objects: list[Any]) -> float:
    minimum = Vector((math.inf, math.inf, math.inf))
    maximum = Vector((-math.inf, -math.inf, -math.inf))
    for obj in objects:
        for corner in obj.bound_box:
            world = obj.matrix_world @ Vector(corner)
            for axis in range(3):
                minimum[axis] = min(minimum[axis], world[axis])
                maximum[axis] = max(maximum[axis], world[axis])
    return max(1e-6, (maximum - minimum).length)


def select_only(objects: list[Any], active: Any | None = None) -> None:
    bpy.ops.object.mode_set(mode="OBJECT") if bpy.context.object and bpy.context.object.mode != "OBJECT" else None
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.hide_set(False)
        obj.select_set(True)
    bpy.context.view_layer.objects.active = active or (objects[-1] if objects else None)


def duplicate_targets(source_objects: list[Any]) -> list[Any]:
    targets = []
    for source in source_objects:
        original_name = source.name
        source.name = "__TMF_SOURCE__" + original_name
        target = source.copy()
        target.data = source.data.copy()
        target.name = original_name
        source.users_collection[0].objects.link(target)
        targets.append(target)
    return targets


def apply_scale(objects: list[Any]) -> None:
    select_only(objects, objects[0])
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)


def apply_decimate(obj: Any, ratio: float) -> None:
    if ratio >= 0.999999:
        return
    select_only([obj], obj)
    modifier = obj.modifiers.new(name="ToraMeshForge_RuntimeDecimate", type="DECIMATE")
    modifier.decimate_type = "COLLAPSE"
    modifier.ratio = max(0.0001, min(1.0, ratio))
    modifier.use_collapse_triangulate = True
    bpy.ops.object.modifier_apply(modifier=modifier.name)
    obj.data.validate(clean_customdata=False)
    obj.data.update(calc_edges=True)


def decimate_targets(targets: list[Any], target: int, preserve_small_parts: bool) -> dict[str, Any]:
    counts = {obj.name: triangle_count(obj) for obj in targets}
    source_total = sum(counts.values())
    if target >= source_total:
        return {"source_triangles": source_total, "target_triangles": target, "protected_objects": [], "passes": 0}
    threshold = min(2_000, max(100, int(source_total * 0.005)))
    protected = {
        obj.name for obj in targets
        if preserve_small_parts and len(targets) > 1 and counts[obj.name] <= threshold
    }
    protected_total = sum(counts[name] for name in protected)
    reducible = [obj for obj in targets if obj.name not in protected]
    reducible_total = sum(counts[obj.name] for obj in reducible)
    budget = max(4 * len(reducible), target - protected_total)
    ratio = min(1.0, budget / max(1, reducible_total))
    for index, obj in enumerate(reducible):
        progress("decimate", 0.18 + 0.16 * ((index + 1) / len(reducible)), f"Decimating target {index + 1}/{len(reducible)}")
        apply_decimate(obj, ratio)
    passes = 1
    for _ in range(2):
        current = protected_total + sum(triangle_count(obj) for obj in reducible)
        if current <= target + max(50, int(target * 0.005)):
            break
        correction = max(0.0001, (target - protected_total) / max(1, current - protected_total))
        for obj in reducible:
            apply_decimate(obj, correction)
        passes += 1
    return {
        "source_triangles": source_total,
        "target_triangles": target,
        "protected_objects": sorted(protected),
        "small_part_threshold": threshold,
        "passes": passes,
    }


def source_maximum_dimension() -> int:
    dimensions = [max(int(image.size[0]), int(image.size[1])) for image in bpy.data.images if image.source not in {"VIEWER", "GENERATED"}]
    return max(dimensions, default=0)


def choose_resolution(args: argparse.Namespace) -> tuple[int, str]:
    maximum = max(value for value in ALLOWED_RESOLUTIONS if value <= args.maximum_texture_resolution)
    source = source_maximum_dimension()
    if args.texture_resolution_mode == "manual":
        if args.manual_texture_resolution not in ALLOWED_RESOLUTIONS:
            raise ValueError("Manual texture resolution must be one of 512, 1024, 2048, 4096, or 8192.")
        return min(args.manual_texture_resolution, maximum), "manual"
    if source <= 0:
        return min(2048, maximum), "fallback-no-readable-source"
    selected = next((value for value in ALLOWED_RESOLUTIONS if value >= source), 8192)
    return min(selected, maximum), args.texture_resolution_mode


def prepare_runtime_uv(obj: Any) -> Any:
    if not obj.data.uv_layers:
        raise ValueError(f"{obj.name} has no source UV map for Base Color transfer.")
    source_uv = obj.data.uv_layers.get(SOURCE_UV_NAME) or obj.data.uv_layers.active or obj.data.uv_layers[0]
    source_uv.name = SOURCE_UV_NAME
    runtime_uv = obj.data.uv_layers.get(RUNTIME_UV_NAME)
    if runtime_uv is not None:
        obj.data.uv_layers.remove(runtime_uv)
    runtime_uv = obj.data.uv_layers.new(name=RUNTIME_UV_NAME)
    obj.data.uv_layers.active_index = len(obj.data.uv_layers) - 1
    runtime_uv.active_render = True
    return runtime_uv


def unwrap_standard(obj: Any, mode: str, resolution: int, margin_pixels: int) -> None:
    prepare_runtime_uv(obj)
    select_only([obj], obj)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    margin = max(0.0, margin_pixels / resolution)
    if mode == "smart":
        bpy.ops.uv.smart_project(
            angle_limit=math.radians(66.0),
            margin_method="FRACTION",
            island_margin=margin,
            correct_aspect=True,
            scale_to_bounds=True,
        )
    else:
        bpy.ops.uv.unwrap(
            method="CONFORMAL" if mode == "conformal" else "ANGLE_BASED",
            fill_holes=True,
            correct_aspect=True,
            margin_method="FRACTION",
            margin=margin,
        )
        bpy.ops.uv.pack_islands(
            rotate=True,
            scale=True,
            merge_overlap=False,
            margin_method="FRACTION",
            margin=margin,
        )
    bpy.ops.object.mode_set(mode="OBJECT")


def polygon_center(obj: Any, polygon: Any) -> Vector:
    result = Vector((0.0, 0.0, 0.0))
    for vertex_index in polygon.vertices:
        result += obj.data.vertices[vertex_index].co
    return result / max(1, len(polygon.vertices))


class IndexGroups:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))
        self.weight = [1] * size

    def find(self, value: int) -> int:
        while self.parent[value] != value:
            self.parent[value] = self.parent[self.parent[value]]
            value = self.parent[value]
        return value

    def union(self, first: int, second: int) -> int:
        first = self.find(first)
        second = self.find(second)
        if first == second:
            return first
        if self.weight[first] < self.weight[second]:
            first, second = second, first
        self.parent[second] = first
        self.weight[first] += self.weight[second]
        return first


def quantized_coordinate(obj: Any, vertex_index: int, tolerance: float) -> tuple[int, int, int]:
    coordinate = obj.data.vertices[vertex_index].co
    return tuple(round(float(coordinate[axis]) / tolerance) for axis in range(3))


def create_selective_uv_guide(
    obj: Any,
    tolerance: float,
    maximum_chart_faces: int = 500,
    maximum_join_angle_degrees: float = 10.0,
    allowed_component_links: set[tuple[int, int]] | None = None,
    blocked_source_components: set[int] | None = None,
    restricted_source_components: set[int] | None = None,
) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    blocked_source_components = blocked_source_components or set()
    restricted_source_components = restricted_source_components or set()
    face_groups = IndexGroups(len(obj.data.polygons))
    edge_faces: dict[tuple[int, int], list[tuple[int, tuple[int, int]]]] = {}
    for polygon in obj.data.polygons:
        vertices = list(polygon.vertices)
        for index, first in enumerate(vertices):
            second = vertices[(index + 1) % len(vertices)]
            edge = tuple(sorted((int(first), int(second))))
            edge_faces.setdefault(edge, []).append((polygon.index, (int(first), int(second))))
    for records in edge_faces.values():
        for record in records[1:]:
            face_groups.union(records[0][0], record[0])
    source_component_by_face = [face_groups.find(index) for index in range(len(obj.data.polygons))]

    virtual_edges: dict[
        tuple[tuple[int, int, int], tuple[int, int, int]],
        list[tuple[int, tuple[int, int]]],
    ] = {}
    for records in edge_faces.values():
        if len(records) != 1:
            continue
        face_index, edge = records[0]
        points = tuple(sorted((
            quantized_coordinate(obj, edge[0], tolerance),
            quantized_coordinate(obj, edge[1], tolerance),
        )))
        virtual_edges.setdefault(points, []).append((face_index, edge))

    maximum_join_angle = math.radians(maximum_join_angle_degrees)
    component_links: dict[
        tuple[int, int],
        list[tuple[float, tuple[int, int], tuple[int, int]]],
    ] = {}
    for records in virtual_edges.values():
        if len(records) != 2:
            continue
        first, second = records
        first_component = face_groups.find(first[0])
        second_component = face_groups.find(second[0])
        if first_component == second_component:
            continue
        first_start = quantized_coordinate(obj, first[1][0], tolerance)
        first_end = quantized_coordinate(obj, first[1][1], tolerance)
        second_start = quantized_coordinate(obj, second[1][0], tolerance)
        second_end = quantized_coordinate(obj, second[1][1], tolerance)
        if first_start != second_end or first_end != second_start:
            continue
        first_normal = obj.data.polygons[first[0]].normal
        second_normal = obj.data.polygons[second[0]].normal
        dot = max(-1.0, min(1.0, float(first_normal.dot(second_normal))))
        angle = math.acos(dot)
        if angle > maximum_join_angle:
            continue
        key = tuple(sorted((first_component, second_component)))
        component_links.setdefault(key, []).append((angle, first[1], second[1]))

    ordered_links = sorted(
        component_links.items(),
        key=lambda item: (
            sum(candidate[0] for candidate in item[1]) / len(item[1]),
            -len(item[1]),
        ),
    )
    selected_links: set[tuple[int, int]] = set()
    restricted_roots = [False] * len(obj.data.polygons)
    for component in restricted_source_components:
        restricted_roots[face_groups.find(component)] = True
    for (first_component, second_component), candidates in ordered_links:
        link = (first_component, second_component)
        if allowed_component_links is not None and link not in allowed_component_links:
            continue
        if first_component in blocked_source_components or second_component in blocked_source_components:
            continue
        first_root = face_groups.find(first_component)
        second_root = face_groups.find(second_component)
        if first_root == second_root:
            continue
        restricted = restricted_roots[first_root] or restricted_roots[second_root]
        chart_limit = 250 if restricted else maximum_chart_faces
        angle_limit = math.radians(2.5) if restricted else maximum_join_angle
        mean_angle = sum(candidate[0] for candidate in candidates) / len(candidates)
        if mean_angle > angle_limit:
            continue
        if face_groups.weight[first_root] + face_groups.weight[second_root] > chart_limit:
            continue
        new_root = face_groups.union(first_root, second_root)
        restricted_roots[new_root] = restricted
        selected_links.add((first_component, second_component))

    vertex_groups = IndexGroups(len(obj.data.vertices))
    quantized = [quantized_coordinate(obj, index, tolerance) for index in range(len(obj.data.vertices))]
    stitched_edge_pairs = 0
    for key in selected_links:
        for _angle, first_edge, second_edge in component_links[key]:
            for first_vertex in first_edge:
                matches = [vertex for vertex in second_edge if quantized[vertex] == quantized[first_vertex]]
                if len(matches) != 1:
                    raise RuntimeError("UV guide boundary endpoint correspondence is ambiguous.")
                vertex_groups.union(first_vertex, matches[0])
            stitched_edge_pairs += 1

    grouped_coordinates: dict[int, list[Vector]] = {}
    for vertex in obj.data.vertices:
        grouped_coordinates.setdefault(vertex_groups.find(vertex.index), []).append(vertex.co.copy())
    root_to_guide_index: dict[int, int] = {}
    guide_coordinates: list[tuple[float, float, float]] = []
    for vertex in obj.data.vertices:
        root = vertex_groups.find(vertex.index)
        if root in root_to_guide_index:
            continue
        coordinate = sum(grouped_coordinates[root], Vector((0.0, 0.0, 0.0))) / len(grouped_coordinates[root])
        root_to_guide_index[root] = len(guide_coordinates)
        guide_coordinates.append(tuple(coordinate))
    guide_faces = [
        [root_to_guide_index[vertex_groups.find(int(vertex))] for vertex in polygon.vertices]
        for polygon in obj.data.polygons
    ]
    if any(len(set(face)) != len(face) for face in guide_faces):
        raise RuntimeError("Selective UV guide stitching would create a degenerate face.")
    guide_mesh = bpy.data.meshes.new("TMF_SelectiveUVGuideMesh")
    guide_mesh.from_pydata(guide_coordinates, [], guide_faces)
    guide_mesh.update(calc_edges=True)
    guide_mesh.uv_layers.new(name=SOURCE_UV_NAME)
    guide = bpy.data.objects.new("__TMF_UV_GUIDE__" + obj.name, guide_mesh)
    obj.users_collection[0].objects.link(guide)
    guide.matrix_world = obj.matrix_world.copy()
    final_component_by_face = [face_groups.find(polygon.index) for polygon in obj.data.polygons]
    final_groups = set(final_component_by_face)
    source_component_face_counts: dict[int, int] = {}
    for component in source_component_by_face:
        source_component_face_counts[component] = source_component_face_counts.get(component, 0) + 1
    statistics = {
        "source_components": len(set(source_component_by_face)),
        "guide_components": len(final_groups),
        "selected_component_links": len(selected_links),
        "stitched_boundary_edge_pairs": stitched_edge_pairs,
        "maximum_chart_faces": maximum_chart_faces,
        "maximum_join_angle_degrees": maximum_join_angle_degrees,
    }
    state = {
        "source_component_by_face": source_component_by_face,
        "final_component_by_face": final_component_by_face,
        "selected_links": selected_links,
        "component_links": component_links,
        "source_component_face_counts": source_component_face_counts,
    }
    return guide, statistics, state


def face_component_lists(obj: Any) -> list[list[int]]:
    groups = IndexGroups(len(obj.data.polygons))
    edge_faces: dict[tuple[int, int], list[int]] = {}
    for polygon in obj.data.polygons:
        vertices = list(polygon.vertices)
        for index, first in enumerate(vertices):
            second = vertices[(index + 1) % len(vertices)]
            edge_faces.setdefault(tuple(sorted((int(first), int(second)))), []).append(polygon.index)
    for faces in edge_faces.values():
        for face in faces[1:]:
            groups.union(faces[0], face)
    result: dict[int, list[int]] = {}
    for polygon in obj.data.polygons:
        result.setdefault(groups.find(polygon.index), []).append(polygon.index)
    return list(result.values())


def topology_metrics(obj: Any) -> dict[str, Any]:
    edge_faces: dict[tuple[int, int], int] = {}
    degenerate_faces = 0
    for polygon in obj.data.polygons:
        vertices = [int(vertex) for vertex in polygon.vertices]
        if len(set(vertices)) != len(vertices) or polygon.area <= 1e-18:
            degenerate_faces += 1
        for index, first in enumerate(vertices):
            second = vertices[(index + 1) % len(vertices)]
            edge = tuple(sorted((first, second)))
            edge_faces[edge] = edge_faces.get(edge, 0) + 1
    return {
        "vertices": len(obj.data.vertices),
        "polygons": len(obj.data.polygons),
        "loops": len(obj.data.loops),
        "triangles": triangle_count(obj),
        "components": len(face_component_lists(obj)),
        "edges": len(edge_faces),
        "boundary_edges": sum(count == 1 for count in edge_faces.values()),
        "overused_edges": sum(count > 2 for count in edge_faces.values()),
        "degenerate_faces": degenerate_faces,
    }


def unsafe_consolidated_charts(guide: Any, state: dict[str, Any]) -> tuple[list[dict[str, Any]], float]:
    unsafe: list[dict[str, Any]] = []
    maximum_overlap = 0.0
    source_component_by_face = state["source_component_by_face"]
    for faces in face_component_lists(guide):
        source_components = {source_component_by_face[index] for index in faces}
        if len(source_components) <= 1:
            continue
        metrics = uv_overlap_metrics(guide, RUNTIME_UV_NAME, raster_size=256, polygon_indices=faces)
        maximum_overlap = max(maximum_overlap, float(metrics["overlap_ratio"]))
        if metrics["overlap_ratio"] > 0.001:
            unsafe.append({
                "source_components": source_components,
                "selected_links": {
                    link for link in state["selected_links"]
                    if link[0] in source_components and link[1] in source_components
                },
                "overlap_ratio": float(metrics["overlap_ratio"]),
            })
    return unsafe, maximum_overlap


def choose_balanced_chart_cut(chart: dict[str, Any], state: dict[str, Any]) -> tuple[int, int] | None:
    links: set[tuple[int, int]] = chart["selected_links"]
    if not links:
        return None
    components: set[int] = chart["source_components"]
    adjacency: dict[int, list[tuple[int, tuple[int, int]]]] = {component: [] for component in components}
    for link in links:
        first, second = link
        adjacency[first].append((second, link))
        adjacency[second].append((first, link))
    weights = state["source_component_face_counts"]
    total_weight = sum(weights[component] for component in components)

    ranked: list[tuple[tuple[float, float, int], tuple[int, int]]] = []
    for link in links:
        pending = [link[0]]
        visited: set[int] = set()
        while pending:
            component = pending.pop()
            if component in visited:
                continue
            visited.add(component)
            for neighbour, neighbour_link in adjacency[component]:
                if neighbour_link != link and neighbour not in visited:
                    pending.append(neighbour)
        side_weight = sum(weights[component] for component in visited)
        largest_side = max(side_weight, total_weight - side_weight)
        candidates = state["component_links"][link]
        mean_angle = sum(candidate[0] for candidate in candidates) / len(candidates)
        ranked.append(((largest_side / max(1, total_weight), -mean_angle, len(candidates)), link))
    return min(ranked, key=lambda item: item[0])[1]


def remove_guide(guide: Any) -> None:
    guide_mesh = guide.data
    bpy.data.objects.remove(guide, do_unlink=True)
    if guide_mesh.users == 0:
        bpy.data.meshes.remove(guide_mesh)


def install_welded_guide(obj: Any, guide: Any, maximum_loop_delta: float, tolerance: float) -> dict[str, Any]:
    """Replace the target mesh with the validated guide while retaining loop data."""
    original_mesh = obj.data
    welded_mesh = guide.data
    before = topology_metrics(obj)

    if maximum_loop_delta > tolerance * 1.01:
        raise RuntimeError("Selective boundary welding would move a vertex beyond its merge tolerance.")
    if len(original_mesh.polygons) != len(welded_mesh.polygons) or len(original_mesh.loops) != len(welded_mesh.loops):
        raise RuntimeError("Selective boundary welding changed polygon or loop correspondence.")

    source_uv = original_mesh.uv_layers.get(SOURCE_UV_NAME)
    welded_source_uv = welded_mesh.uv_layers.get(SOURCE_UV_NAME)
    if source_uv is None or welded_source_uv is None:
        raise RuntimeError("Selective boundary welding could not retain the source UV layer for Base Color transfer.")
    source_coordinates = np.empty(len(source_uv.data) * 2, dtype=np.float32)
    source_uv.data.foreach_get("uv", source_coordinates)
    welded_source_uv.data.foreach_set("uv", source_coordinates)

    for material in original_mesh.materials:
        welded_mesh.materials.append(material)
    for original_polygon, welded_polygon in zip(original_mesh.polygons, welded_mesh.polygons):
        welded_polygon.material_index = original_polygon.material_index
        welded_polygon.use_smooth = original_polygon.use_smooth
    for key in original_mesh.keys():
        welded_mesh[key] = original_mesh[key]

    welded_mesh.name = original_mesh.name
    obj.data = welded_mesh
    bpy.data.objects.remove(guide, do_unlink=True)
    if original_mesh.users == 0:
        bpy.data.meshes.remove(original_mesh)
    obj.data.validate(clean_customdata=False)
    obj.data.update(calc_edges=True)

    runtime_uv = obj.data.uv_layers.get(RUNTIME_UV_NAME)
    if runtime_uv is None:
        raise RuntimeError("Selective boundary welding lost the generated runtime UV layer.")
    obj.data.uv_layers.active_index = next(
        index for index, layer in enumerate(obj.data.uv_layers)
        if layer.name == RUNTIME_UV_NAME
    )
    runtime_uv.active_render = True
    after = topology_metrics(obj)
    if after["polygons"] != before["polygons"] or after["loops"] != before["loops"]:
        raise RuntimeError("Selective boundary welding changed face topology.")
    if after["triangles"] != before["triangles"]:
        raise RuntimeError("Selective boundary welding changed the triangle count.")
    if after["degenerate_faces"] > before["degenerate_faces"]:
        raise RuntimeError("Selective boundary welding introduced a degenerate face.")
    if after["overused_edges"] > before["overused_edges"]:
        raise RuntimeError("Selective boundary welding introduced a non-manifold edge shared by more than two faces.")
    if after["boundary_edges"] > before["boundary_edges"]:
        raise RuntimeError("Selective boundary welding increased the number of open boundaries.")
    return {
        "topology_before": before,
        "topology_after": after,
        "welded_vertices": before["vertices"] - after["vertices"],
        "connected_components": before["components"] - after["components"],
        "visible_shape_preserved": True,
        "target_topology_changed": before["vertices"] != after["vertices"],
    }


def unwrap_consolidated(obj: Any, resolution: int, margin_pixels: int) -> dict[str, Any]:
    """Unwrap a selectively stitched guide and install its validated topology."""
    prepare_runtime_uv(obj)
    tolerance = scene_diagonal([obj]) * 1e-7
    guide, consolidation, state = create_selective_uv_guide(obj, tolerance)
    before = {
        "vertices": len(obj.data.vertices),
        "polygons": len(obj.data.polygons),
        "loops": len(obj.data.loops),
    }
    after = {
        "vertices": len(guide.data.vertices),
        "polygons": len(guide.data.polygons),
        "loops": len(guide.data.loops),
    }
    if after["polygons"] != before["polygons"] or after["loops"] != before["loops"]:
        raise RuntimeError("UV guide welding changed face topology; consolidated UV was not applied.")
    maximum_center_delta = 0.0
    maximum_loop_delta = 0.0
    for target_polygon, guide_polygon in zip(obj.data.polygons, guide.data.polygons):
        if target_polygon.loop_total != guide_polygon.loop_total:
            raise RuntimeError("UV guide polygon ordering changed during welding.")
        maximum_center_delta = max(
            maximum_center_delta,
            (polygon_center(obj, target_polygon) - polygon_center(guide, guide_polygon)).length,
        )
        for target_vertex, guide_vertex in zip(target_polygon.vertices, guide_polygon.vertices):
            maximum_loop_delta = max(
                maximum_loop_delta,
                (obj.data.vertices[target_vertex].co - guide.data.vertices[guide_vertex].co).length,
            )
    if maximum_center_delta > scene_diagonal([obj]) * 1e-4:
        raise RuntimeError("UV guide polygon correspondence could not be verified.")
    unwrap_standard(guide, "conformal", resolution, margin_pixels)
    unsafe_charts, initial_maximum_overlap = unsafe_consolidated_charts(guide, state)
    initial_global_overlap = uv_overlap_metrics(guide, RUNTIME_UV_NAME)["overlap_ratio"]
    if initial_global_overlap <= 0.001:
        unsafe_charts = []
    initial_components = consolidation["guide_components"]
    initial_selected_links = len(state["selected_links"])
    active_links = set(state["selected_links"])
    pruned_links: set[tuple[int, int]] = set()
    adaptive_history: list[dict[str, Any]] = []
    maximum_pruning_iterations = 6
    for iteration in range(1, maximum_pruning_iterations + 1):
        if not unsafe_charts:
            break
        cuts = {
            cut for chart in unsafe_charts
            if (cut := choose_balanced_chart_cut(chart, state)) is not None
        }
        if not cuts:
            break
        active_links.difference_update(cuts)
        pruned_links.update(cuts)
        remove_guide(guide)
        guide, consolidation, state = create_selective_uv_guide(
            obj,
            tolerance,
            allowed_component_links=active_links,
        )
        active_links = set(state["selected_links"])
        unwrap_standard(guide, "conformal", resolution, margin_pixels)
        unsafe_charts, maximum_overlap = unsafe_consolidated_charts(guide, state)
        global_overlap = uv_overlap_metrics(guide, RUNTIME_UV_NAME)["overlap_ratio"]
        if global_overlap <= 0.001:
            unsafe_charts = []
        adaptive_history.append({
            "iteration": iteration,
            "cut_links": len(cuts),
            "guide_components": consolidation["guide_components"],
            "unsafe_charts": len(unsafe_charts),
            "maximum_component_overlap_ratio": maximum_overlap,
            "global_overlap_ratio": global_overlap,
        })
        print("TMF_UV_PRUNING " + json.dumps(adaptive_history[-1]), flush=True)

    fallback_links: set[tuple[int, int]] = set()
    fallback_source_components: set[int] = set()
    fallback_history: list[dict[str, Any]] = []
    fallback_maximum_overlap = 0.0
    for fallback_iteration in range(1, 7):
        if not unsafe_charts:
            break
        current_source_components: set[int] = set()
        for chart in unsafe_charts:
            current_source_components.update(chart["source_components"])
        fallback_source_components.update(current_source_components)
        current_links = {
            link for link in active_links
            if link[0] in current_source_components or link[1] in current_source_components
        }
        if not current_links:
            break
        fallback_links.update(current_links)
        active_links.difference_update(current_links)
        pruned_links.update(current_links)
        remove_guide(guide)
        guide, consolidation, state = create_selective_uv_guide(
            obj,
            tolerance,
            allowed_component_links=active_links,
        )
        unwrap_standard(guide, "conformal", resolution, margin_pixels)
        unsafe_charts, fallback_maximum_overlap = unsafe_consolidated_charts(guide, state)
        global_overlap = uv_overlap_metrics(guide, RUNTIME_UV_NAME)["overlap_ratio"]
        if global_overlap <= 0.001:
            unsafe_charts = []
        fallback_history.append({
            "iteration": fallback_iteration,
            "cut_links": len(current_links),
            "guide_components": consolidation["guide_components"],
            "unsafe_charts": len(unsafe_charts),
            "maximum_component_overlap_ratio": fallback_maximum_overlap,
            "global_overlap_ratio": global_overlap,
        })
        print("TMF_UV_FALLBACK " + json.dumps(fallback_history[-1]), flush=True)
    if unsafe_charts:
        raise RuntimeError("Adaptive UV link pruning could not isolate all overlapping consolidated charts.")
    consolidation.update({
        "initial_guide_components": initial_components,
        "initial_selected_component_links": initial_selected_links,
        "adaptive_pruning_iterations": len(adaptive_history),
        "pruned_component_links": len(pruned_links),
        "fallback_pruned_component_links": len(fallback_links),
        "fallback_source_components": len(fallback_source_components),
        "fallback_iterations": len(fallback_history),
        "initial_maximum_component_overlap_ratio": initial_maximum_overlap,
        "initial_global_overlap_ratio": initial_global_overlap,
        "fallback_maximum_component_overlap_ratio": fallback_maximum_overlap,
        "adaptive_history": adaptive_history,
        "fallback_history": fallback_history,
    })
    after = {
        "vertices": len(guide.data.vertices),
        "polygons": len(guide.data.polygons),
        "loops": len(guide.data.loops),
    }
    if after["polygons"] != before["polygons"] or after["loops"] != before["loops"]:
        raise RuntimeError("UV guide fallback changed face topology; consolidated UV was not applied.")
    maximum_center_delta = 0.0
    maximum_loop_delta = 0.0
    for target_polygon, guide_polygon in zip(obj.data.polygons, guide.data.polygons):
        maximum_center_delta = max(
            maximum_center_delta,
            (polygon_center(obj, target_polygon) - polygon_center(guide, guide_polygon)).length,
        )
        for target_vertex, guide_vertex in zip(target_polygon.vertices, guide_polygon.vertices):
            maximum_loop_delta = max(
                maximum_loop_delta,
                (obj.data.vertices[target_vertex].co - guide.data.vertices[guide_vertex].co).length,
            )
    if maximum_center_delta > scene_diagonal([obj]) * 1e-4:
        raise RuntimeError("Final UV guide polygon correspondence could not be verified.")
    guide_uv = guide.data.uv_layers.get(RUNTIME_UV_NAME)
    if guide_uv is None:
        raise RuntimeError("Consolidated UV guide did not produce a runtime UV layer.")
    welding = install_welded_guide(obj, guide, maximum_loop_delta, tolerance)
    result = {
        "mode": "consolidated",
        "guide_merge_tolerance": tolerance,
        "guide_vertices_before": before["vertices"],
        "guide_vertices_after": after["vertices"],
        "maximum_guide_center_delta": maximum_center_delta,
        "maximum_guide_loop_delta": maximum_loop_delta,
        "target_geometry_unchanged": False,
        **consolidation,
        **welding,
    }
    print("TMF_UV_CONSOLIDATION " + json.dumps(result), flush=True)
    return result


def unwrap_target(obj: Any, mode: str, resolution: int, margin_pixels: int) -> dict[str, Any]:
    if mode == "consolidated":
        return unwrap_consolidated(obj, resolution, margin_pixels)
    unwrap_standard(obj, mode, resolution, margin_pixels)
    return {"mode": mode, "target_geometry_unchanged": True}


def roundtrip_bake_target(obj: Any, bake_dir: Path) -> Any:
    """Re-import a prepared target so Cycles sees generated UVs reliably."""
    name = obj.name
    path = bake_dir / f".tmf_bake_target_{safe_name(name)}.fbx"
    select_only([obj], obj)
    bpy.ops.export_scene.fbx(
        filepath=str(path),
        use_selection=True,
        object_types={"MESH"},
        use_mesh_modifiers=True,
        use_custom_props=True,
        add_leaf_bones=False,
        bake_anim=False,
        path_mode="AUTO",
        axis_forward="-Z",
        axis_up="Y",
    )
    mesh = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    if mesh.users == 0:
        bpy.data.meshes.remove(mesh)
    existing = set(bpy.data.objects)
    bpy.ops.import_scene.fbx(filepath=str(path), use_anim=False)
    imported = [candidate for candidate in bpy.data.objects if candidate not in existing and candidate.type == "MESH"]
    if len(imported) != 1:
        raise RuntimeError(f"Expected one bake target after FBX checkpoint; found {len(imported)}.")
    imported[0].name = name
    return imported[0]


def make_emission_copy(material: Any, suffix: str) -> Any:
    result = material.copy() if material else bpy.data.materials.new("ToraBakeSource_" + suffix)
    result.name = "ToraBakeSource_" + suffix
    result.use_nodes = True
    nodes = result.node_tree.nodes
    output = next((node for node in nodes if node.type == "OUTPUT_MATERIAL"), None) or nodes.new("ShaderNodeOutputMaterial")
    principled = next((node for node in nodes if node.type == "BSDF_PRINCIPLED"), None)
    emission = nodes.new("ShaderNodeEmission")
    if principled is not None:
        base = principled.inputs.get("Base Color")
        if base and base.is_linked:
            result.node_tree.links.new(base.links[0].from_socket, emission.inputs["Color"])
        elif base:
            emission.inputs["Color"].default_value = base.default_value
    else:
        emission.inputs["Color"].default_value = material.diffuse_color if material else (1.0, 1.0, 1.0, 1.0)
    for link in list(output.inputs["Surface"].links):
        result.node_tree.links.remove(link)
    result.node_tree.links.new(emission.outputs["Emission"], output.inputs["Surface"])
    return result


def prepare_source_materials(source: Any) -> None:
    for index, slot in enumerate(source.material_slots):
        slot.material = make_emission_copy(slot.material, f"{source.name}_{index}")


def make_target_material(name: str, image: Any) -> tuple[Any, Any, Any]:
    material = bpy.data.materials.new("TMF_Runtime_" + name)
    material.use_nodes = True
    nodes = material.node_tree.nodes
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.inputs["Metallic"].default_value = 0.0
    principled.inputs["Roughness"].default_value = 0.5
    texture = nodes.new("ShaderNodeTexImage")
    texture.image = image
    for node in nodes:
        node.select = False
    nodes.active = texture
    texture.select = True
    material.node_tree.links.new(principled.outputs["BSDF"], output.inputs["Surface"])
    return material, texture, principled


def make_uv_repack_material(name: str, source_material: Any, image: Any) -> Any:
    material = make_emission_copy(source_material, "UVRepack_" + name)
    nodes = material.node_tree.nodes
    uv_map = nodes.new("ShaderNodeUVMap")
    uv_map.uv_map = SOURCE_UV_NAME
    for node in list(nodes):
        if node.type == "TEX_IMAGE" and node.image is not image:
            material.node_tree.links.new(uv_map.outputs["UV"], node.inputs["Vector"])
    bake_target = nodes.new("ShaderNodeTexImage")
    bake_target.image = image
    for node in nodes:
        node.select = False
    bake_target.select = True
    nodes.active = bake_target
    return material


def make_mask_material(name: str, image: Any) -> Any:
    material = bpy.data.materials.new("TMF_Mask_" + name)
    material.use_nodes = True
    nodes = material.node_tree.nodes
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    emission = nodes.new("ShaderNodeEmission")
    emission.inputs["Color"].default_value = (1.0, 1.0, 1.0, 1.0)
    texture = nodes.new("ShaderNodeTexImage")
    texture.image = image
    for node in nodes:
        node.select = False
    nodes.active = texture
    texture.select = True
    material.node_tree.links.new(emission.outputs["Emission"], output.inputs["Surface"])
    return material


def activate_bake_target(target: Any, material: Any, image: Any | None = None) -> None:
    target.active_material_index = 0
    if target.data.uv_layers:
        runtime_uv = target.data.uv_layers.get(RUNTIME_UV_NAME)
        if runtime_uv is None:
            raise ValueError(f"{target.name} has no runtime UV layer named {RUNTIME_UV_NAME}.")
        runtime_index = next(
            index for index, layer in enumerate(target.data.uv_layers)
            if layer.name == RUNTIME_UV_NAME
        )
        target.data.uv_layers.active_index = runtime_index
        runtime_uv.active_render = True
    nodes = material.node_tree.nodes
    texture = next(
        node for node in nodes
        if node.type == "TEX_IMAGE" and (image is None or node.image == image)
    )
    for node in nodes:
        node.select = False
    texture.select = True
    nodes.active = texture
    target.hide_render = False
    target.data.update()
    bpy.context.view_layer.update()


def bake_state(target: Any, material: Any, image: Any) -> dict[str, Any]:
    uv_data = target.data.uv_layers.active.data
    u_values = [loop.uv.x for loop in uv_data]
    v_values = [loop.uv.y for loop in uv_data]
    return {
        "object": target.name,
        "active_object": bpy.context.view_layer.objects.active.name if bpy.context.view_layer.objects.active else None,
        "selected": [obj.name for obj in bpy.context.selected_objects],
        "active_material_index": target.active_material_index,
        "active_node": material.node_tree.nodes.active.name if material.node_tree.nodes.active else None,
        "image": image.name,
        "uv_loops": len(uv_data),
        "uv_bounds": [min(u_values), max(u_values), min(v_values), max(v_values)],
        "hide_render": target.hide_render,
    }


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "object"


def pixels(image: Any) -> np.ndarray:
    values = np.empty(len(image.pixels), dtype=np.float32)
    image.pixels.foreach_get(values)
    return values.reshape((-1, 4))


def write_invalid_mask(base_image: Any, uv_mask: Any, path: Path) -> dict[str, Any]:
    base = pixels(base_image)
    mask = pixels(uv_mask)
    expected = np.max(mask[:, :3], axis=1) > 0.5
    invalid = expected & (base[:, 0] > 0.98) & (base[:, 1] < 0.02) & (base[:, 2] > 0.98)
    invalid_count = int(np.count_nonzero(invalid))
    expected_count = int(np.count_nonzero(expected))
    if expected_count == 0:
        raise RuntimeError("UV occupancy mask bake produced no pixels.")
    output = np.zeros_like(base)
    output[invalid, 0] = 1.0
    output[invalid, 3] = 1.0
    invalid_image = bpy.data.images.new(path.stem, width=base_image.size[0], height=base_image.size[1], alpha=True)
    invalid_image.pixels.foreach_set(output.reshape(-1))
    invalid_image.filepath_raw = str(path)
    invalid_image.file_format = "PNG"
    invalid_image.save()
    return {
        "uv_occupied_pixels": expected_count,
        "invalid_pixels": invalid_count,
        "invalid_pixel_ratio": invalid_count / max(1, expected_count),
        "uv_coverage_ratio": expected_count / max(1, base_image.size[0] * base_image.size[1]),
        "invalid_mask": str(path.resolve()),
    }


def base_bake_changed_pixels(image: Any) -> int:
    values = pixels(image)
    sentinel = (values[:, 0] > 0.98) & (values[:, 1] < 0.02) & (values[:, 2] > 0.98)
    return int(values.shape[0] - np.count_nonzero(sentinel))


def uv_area_metrics(obj: Any, layer_name: str) -> dict[str, Any]:
    layer = obj.data.uv_layers.get(layer_name)
    if layer is None:
        raise ValueError(f"{obj.name} has no UV layer named {layer_name}.")
    areas: list[float] = []
    for polygon in obj.data.polygons:
        coordinates = [layer.data[index].uv for index in polygon.loop_indices]
        area = 0.0
        for index in range(1, len(coordinates) - 1):
            first = coordinates[index] - coordinates[0]
            second = coordinates[index + 1] - coordinates[0]
            area += abs(first.x * second.y - first.y * second.x) * 0.5
        areas.append(float(area))
    nondegenerate = sum(area > 1e-12 for area in areas)
    return {
        "polygons": len(areas),
        "nondegenerate_polygons": nondegenerate,
        "nondegenerate_ratio": nondegenerate / max(1, len(areas)),
        "summed_uv_area": sum(areas),
        "maximum_polygon_uv_area": max(areas, default=0.0),
    }


def uv_overlap_metrics(
    obj: Any,
    layer_name: str,
    raster_size: int = 512,
    polygon_indices: list[int] | None = None,
) -> dict[str, Any]:
    layer = obj.data.uv_layers.get(layer_name)
    if layer is None:
        raise ValueError(f"{obj.name} has no UV layer named {layer_name}.")
    coverage = np.zeros((raster_size, raster_size), dtype=np.uint16)
    polygons = (
        [obj.data.polygons[index] for index in polygon_indices]
        if polygon_indices is not None
        else obj.data.polygons
    )
    for polygon in polygons:
        loops = list(polygon.loop_indices)
        for index in range(1, len(loops) - 1):
            coordinates = np.array([
                tuple(layer.data[loops[0]].uv),
                tuple(layer.data[loops[index]].uv),
                tuple(layer.data[loops[index + 1]].uv),
            ], dtype=np.float64)
            minimum = np.maximum(0, np.floor(np.min(coordinates, axis=0) * raster_size).astype(np.int32))
            maximum = np.minimum(
                raster_size - 1,
                np.ceil(np.max(coordinates, axis=0) * raster_size).astype(np.int32),
            )
            if np.any(maximum < minimum):
                continue
            first, second, third = coordinates
            denominator = (second[1] - third[1]) * (first[0] - third[0]) + (third[0] - second[0]) * (first[1] - third[1])
            if abs(denominator) <= 1e-15:
                continue
            x_values = (np.arange(minimum[0], maximum[0] + 1, dtype=np.float64) + 0.5) / raster_size
            y_values = (np.arange(minimum[1], maximum[1] + 1, dtype=np.float64) + 0.5) / raster_size
            x_grid, y_grid = np.meshgrid(x_values, y_values)
            first_weight = ((second[1] - third[1]) * (x_grid - third[0]) + (third[0] - second[0]) * (y_grid - third[1])) / denominator
            second_weight = ((third[1] - first[1]) * (x_grid - third[0]) + (first[0] - third[0]) * (y_grid - third[1])) / denominator
            third_weight = 1.0 - first_weight - second_weight
            # Exclude shared triangle borders so adjacent faces are not counted
            # as overlaps merely because they meet at the same UV edge.
            inside = (first_weight > 1e-5) & (second_weight > 1e-5) & (third_weight > 1e-5)
            view = coverage[minimum[1] : maximum[1] + 1, minimum[0] : maximum[0] + 1]
            view[inside] += 1
    occupied = int(np.count_nonzero(coverage))
    overlapping = int(np.count_nonzero(coverage > 1))
    maximum_layers = int(np.max(coverage)) if occupied else 0
    return {
        "raster_resolution": [raster_size, raster_size],
        "occupied_samples": occupied,
        "overlapping_samples": overlapping,
        "overlap_ratio": overlapping / max(1, occupied),
        "maximum_overlap_layers": maximum_layers,
    }


def configure_bake(diagonal: float, margin_pixels: int) -> None:
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 1
    bake = scene.render.bake
    bake.target = "IMAGE_TEXTURES"
    bake.save_mode = "INTERNAL"
    bake.use_selected_to_active = False
    bake.use_clear = False
    bake.margin = margin_pixels
    bake.margin_type = "EXTEND"
    bake.cage_extrusion = 0.0
    bake.max_ray_distance = 0.0
    bake.use_cage = False


def bake_object(source: Any, target: Any, resolution: int, margin_pixels: int, bake_dir: Path, diagonal: float) -> dict[str, Any]:
    name = safe_name(target.name)
    base_path = bake_dir / f"basecolor_{name}.png"
    invalid_path = bake_dir / f"invalid_basecolor_{name}.png"
    base_image = bpy.data.images.new(
        "TMF_BaseColor_" + name,
        width=resolution,
        height=resolution,
        alpha=True,
        float_buffer=False,
    )
    base_image.generated_color = (1.0, 0.0, 1.0, 0.0)
    base_image.colorspace_settings.name = "sRGB"
    uv_metrics = uv_area_metrics(target, RUNTIME_UV_NAME)
    if uv_metrics["nondegenerate_ratio"] < 0.95 or uv_metrics["summed_uv_area"] <= 1e-6:
        raise RuntimeError(
            "Generated runtime UVs collapsed to zero-area islands; choose Angle Based UVs or reduce the island margin."
        )
    overlap_metrics = uv_overlap_metrics(target, RUNTIME_UV_NAME)
    if overlap_metrics["overlap_ratio"] > 0.001:
        raise RuntimeError(
            f"Generated runtime UV overlap is too high ({overlap_metrics['overlap_ratio']:.2%})."
        )
    source_material = target.material_slots[0].material if target.material_slots else None
    if source_material is None:
        raise ValueError(f"{target.name} has no source material for Base Color transfer.")
    bake_material = make_uv_repack_material(name, source_material, base_image)
    target.data.materials.clear()
    target.data.materials.append(bake_material)
    activate_bake_target(target, bake_material, base_image)

    source.hide_render = True
    source.hide_set(True)
    configure_bake(diagonal, margin_pixels)
    select_only([target], target)
    bpy.context.scene.render.bake.use_selected_to_active = False
    bpy.context.scene.render.bake.use_clear = False
    print("TMF_BASE_BAKE_STATE " + json.dumps(bake_state(target, bake_material, base_image)), flush=True)
    result = bpy.ops.object.bake(type="EMIT")
    print("TMF_BASE_BAKE_RESULT " + json.dumps(sorted(result)), flush=True)
    changed_pixels = base_bake_changed_pixels(base_image)
    if changed_pixels == 0:
        raise RuntimeError("Base Color UV repack bake produced no pixels.")
    target_material, texture_node, principled = make_target_material(name, base_image)
    target_material.node_tree.links.new(texture_node.outputs["Color"], principled.inputs["Base Color"])
    base_image.filepath_raw = str(base_path)
    base_image.file_format = "PNG"
    base_image.save()

    uv_mask = bpy.data.images.new("TMF_UVMask_" + name, width=resolution, height=resolution, alpha=True)
    uv_mask.generated_color = (0.0, 0.0, 0.0, 0.0)
    mask_material = make_mask_material(name, uv_mask)
    target.data.materials.clear()
    target.data.materials.append(mask_material)
    activate_bake_target(target, mask_material)
    select_only([target], target)
    bpy.context.scene.render.bake.use_selected_to_active = False
    bpy.context.scene.render.bake.use_clear = True
    bpy.context.scene.render.bake.margin = 0
    print("TMF_MASK_BAKE_STATE " + json.dumps(bake_state(target, mask_material, uv_mask)), flush=True)
    source.hide_render = True
    source.hide_set(True)
    bpy.context.view_layer.update()
    result = bpy.ops.object.bake(type="EMIT")
    print("TMF_MASK_BAKE_RESULT " + json.dumps(sorted(result)), flush=True)
    diagnostics = write_invalid_mask(base_image, uv_mask, invalid_path)

    target.data.materials.clear()
    target.data.materials.append(target_material)
    source_uv = target.data.uv_layers.get(SOURCE_UV_NAME)
    if source_uv is not None:
        target.data.uv_layers.remove(source_uv)
    runtime_uv = target.data.uv_layers.get(RUNTIME_UV_NAME)
    if runtime_uv is not None:
        target.data.uv_layers.active_index = list(target.data.uv_layers).index(runtime_uv)
        runtime_uv.active_render = True
    base_image.pack()
    return {
        "object": target.name,
        "basecolor": str(base_path.resolve()),
        "resolution": [resolution, resolution],
        "changed_pixels": changed_pixels,
        "uv_area": uv_metrics,
        "uv_overlap": overlap_metrics,
        **diagnostics,
    }


def export_targets(path: Path, targets: list[Any]) -> None:
    select_only(targets, targets[0])
    path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.export_scene.fbx(
        filepath=str(path),
        use_selection=True,
        object_types={"MESH"},
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
        raise RuntimeError("Runtime Rebuild did not create a non-empty FBX.")


def validate_output(source: dict[str, Any], output: dict[str, Any], target: int, resolution: int, bake_results: list[dict[str, Any]]) -> dict[str, Any]:
    tolerance = max(50, int(target * 0.01))
    uv_ok = all(len(layers) > 0 for layers in output.get("uv_layers", {}).values())
    texture_sizes = [(item["width"], item["height"]) for item in output["textures"]]
    checks = {
        "output_reloaded": output["meshes"] > 0,
        "target_reached": output["triangles"] <= target + tolerance,
        "mesh_count_preserved": output["meshes"] == source["meshes"],
        "mesh_names_preserved": output["mesh_names"] == source["mesh_names"],
        "new_uv_present": uv_ok,
        "basecolor_texture_present": any(width == resolution and height == resolution for width, height in texture_sizes),
        "basecolor_bakes_exist": all(Path(item["basecolor"]).is_file() for item in bake_results),
        "invalid_masks_exist": all(Path(item["invalid_mask"]).is_file() for item in bake_results),
    }
    failed = [name for name, passed in checks.items() if not passed]
    warnings = []
    invalid_ratio = max((item["invalid_pixel_ratio"] for item in bake_results), default=0.0)
    if invalid_ratio > 0.01:
        warnings.append(f"Base Color bake has {invalid_ratio:.2%} invalid pixels inside UV islands.")
    return {"passed": not failed, "checks": checks, "failed_checks": failed, "warnings": warnings}


def snapshot_with_uv() -> dict[str, Any]:
    result = snapshot_scene()
    result["uv_layers"] = {
        obj.name: [layer.name for layer in obj.data.uv_layers]
        for obj in bpy.context.scene.objects if obj.type == "MESH"
    }
    return result


def main() -> int:
    args = arguments()
    try:
        args.input = args.input.expanduser().resolve()
        args.output = args.output.expanduser().resolve()
        args.report = args.report.expanduser().resolve()
        args.bake_dir = args.bake_dir.expanduser().resolve()
        if args.texture_override:
            args.texture_override = args.texture_override.expanduser().resolve()
        if args.target_triangles < 1_000:
            raise ValueError("Target triangles must be at least 1,000.")
        args.bake_dir.mkdir(parents=True, exist_ok=True)
        progress("scene", 0.03, "Clearing Blender scene")
        clear_scene()
        progress("import", 0.07, f"Importing {args.input.name}")
        import_model(args.input)
        texture_override = apply_texture_override(args.texture_override)
        reject_unsupported_features()
        source_objects = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
        if not source_objects:
            raise ValueError("Runtime Rebuild requires at least one mesh object.")
        source_snapshot = snapshot_with_uv()
        resolution, resolution_reason = choose_resolution(args)
        diagonal = scene_diagonal(source_objects)
        targets = duplicate_targets(source_objects)
        apply_scale(source_objects + targets)
        decimation = decimate_targets(targets, args.target_triangles, args.preserve_small_parts)

        bake_results = []
        uv_results = []
        for index, (source, target) in enumerate(zip(source_objects, targets)):
            progress("uv", 0.36 + 0.08 * ((index + 1) / len(targets)), f"Generating UVs {index + 1}/{len(targets)}")
            uv_results.append(unwrap_target(target, args.uv_mode, resolution, args.uv_margin_pixels))
            target = roundtrip_bake_target(target, args.bake_dir)
            targets[index] = target
            progress("bake", 0.46 + 0.30 * ((index + 1) / len(targets)), f"Baking Base Color {index + 1}/{len(targets)}")
            bake_results.append(bake_object(source, target, resolution, args.uv_margin_pixels, args.bake_dir, diagonal))

        progress("export", 0.80, "Exporting Runtime Rebuild FBX")
        export_targets(args.output, targets)
        progress("reload", 0.87, "Reloading Runtime Rebuild FBX")
        clear_scene()
        import_model(args.output)
        output_snapshot = snapshot_with_uv()
        validation = validate_output(source_snapshot, output_snapshot, args.target_triangles, resolution, bake_results)
        if not validation["passed"]:
            raise RuntimeError("Runtime Rebuild validation failed: " + ", ".join(validation["failed_checks"]))
        report = {
            "operation": "runtime_rebuild",
            "backend": {"remesh": "blender_decimate", "uv": "blender_uv", "bake": "blender_uv_repack_base_color"},
            "source": source_snapshot,
            "output": {**output_snapshot, "path": str(args.output.resolve()), "file_size_bytes": args.output.stat().st_size},
            "decimation": {**decimation, "output_triangles": output_snapshot["triangles"]},
            "uv": {"mode": args.uv_mode, "margin_pixels": args.uv_margin_pixels, "objects": uv_results},
            "texture": {"resolution_mode": args.texture_resolution_mode, "selected_resolution": resolution, "reason": resolution_reason},
            "bake": {"maps": ["basecolor"], "method": "source_uv_to_runtime_uv", "device": "cpu", "objects": bake_results},
            "material": {"metallic": 0.0, "roughness": 0.5, "normal_map": False},
            "texture_override": texture_override,
            "validation": validation,
            "warnings": validation["warnings"],
            "errors": [],
        }
        progress("report", 0.96, "Writing Runtime Rebuild report")
        write_report(args.report, report)
        return 0
    except Exception as exc:
        error = {
            "operation": "runtime_rebuild",
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
