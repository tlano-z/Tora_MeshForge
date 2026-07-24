"""Generate and measure Smart Project UVs on a rebuilt topology probe."""
from __future__ import annotations

import argparse
import heapq
import json
import math
from pathlib import Path
import sys
import time
import traceback
from typing import Any

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from roundtrip_scene import clear_scene, import_model  # noqa: E402
from runtime_rebuild_scene import RUNTIME_UV_NAME, uv_area_metrics, uv_overlap_metrics  # noqa: E402
from uv_merge_planner import adaptive_region_samples, select_disjoint_candidates  # noqa: E402


ADAPTIVE_SAMPLE_MINIMUM_NONDEGENERATE_RATIO = 0.9999
ADAPTIVE_SAMPLE_MAXIMUM_OVERLAP_RATIO = 0.001


def arguments() -> argparse.Namespace:
    values = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--mode", choices=("smart", "regions", "organize"), default="smart")
    parser.add_argument("--angle-degrees", type=float, default=66.0)
    parser.add_argument("--regions", type=int, default=96)
    parser.add_argument("--adaptive-initial-regions", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--curvature-weight", type=float, default=24.0)
    parser.add_argument("--unwrap-method", choices=("angle", "conformal"), default="angle")
    parser.add_argument("--repair-degenerate", action="store_true")
    parser.add_argument("--repair-overlap-regions", action="store_true")
    parser.add_argument("--merge-regions", action="store_true")
    parser.add_argument("--target-regions", type=int, default=1)
    parser.add_argument("--maximum-chart-faces", type=int, default=0)
    parser.add_argument("--maximum-merge-trials", type=int, default=0)
    parser.add_argument("--maximum-merge-batch-size", type=int, default=128)
    parser.add_argument("--maximum-angle-stretch", type=float, default=30.0)
    parser.add_argument("--maximum-area-stretch", type=float, default=2.5)
    parser.add_argument("--organize-islands", action="store_true")
    parser.add_argument("--organization-packing", choices=("efficient", "grouped"), default="efficient")
    parser.add_argument("--resolution", type=int, default=2048)
    parser.add_argument("--margin-pixels", type=int, default=4)
    return parser.parse_args(values)


def select_only(obj: Any) -> None:
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def generate_uv(obj: Any, angle_degrees: float, resolution: int, margin_pixels: int) -> None:
    existing = obj.data.uv_layers.get(RUNTIME_UV_NAME)
    if existing is not None:
        obj.data.uv_layers.remove(existing)
    runtime_uv = obj.data.uv_layers.new(name=RUNTIME_UV_NAME)
    obj.data.uv_layers.active_index = len(obj.data.uv_layers) - 1
    runtime_uv.active_render = True
    select_only(obj)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(
        angle_limit=math.radians(angle_degrees),
        margin_method="FRACTION",
        island_margin=max(0.0, margin_pixels / resolution),
        correct_aspect=True,
        scale_to_bounds=True,
    )
    bpy.ops.object.mode_set(mode="OBJECT")


def region_seams(obj: Any, requested_regions: int, curvature_weight: float) -> tuple[dict[str, Any], dict[str, Any]]:
    mesh = obj.data
    face_count = len(mesh.polygons)
    requested_regions = max(2, min(requested_regions, face_count))
    centroids = np.array([tuple(polygon.center) for polygon in mesh.polygons], dtype=np.float64)
    normals = np.array([tuple(polygon.normal) for polygon in mesh.polygons], dtype=np.float64)
    extent = np.maximum(np.ptp(centroids, axis=0), 1e-12)
    normalized_centroids = (centroids - np.min(centroids, axis=0)) / extent
    features = np.concatenate((normalized_centroids, normals * 0.20), axis=1)

    center = np.mean(normalized_centroids, axis=0)
    first_seed = int(np.argmax(np.sum((normalized_centroids - center) ** 2, axis=1)))
    seeds = [first_seed]
    minimum_feature_distance = np.sum((features - features[first_seed]) ** 2, axis=1)
    for _ in range(1, requested_regions):
        seed = int(np.argmax(minimum_feature_distance))
        seeds.append(seed)
        distance = np.sum((features - features[seed]) ** 2, axis=1)
        minimum_feature_distance = np.minimum(minimum_feature_distance, distance)

    edge_faces: dict[tuple[int, int], list[int]] = {}
    for polygon in mesh.polygons:
        for edge in polygon.edge_keys:
            edge_faces.setdefault(tuple(sorted((int(edge[0]), int(edge[1])))), []).append(polygon.index)
    adjacency: list[list[int]] = [[] for _ in range(face_count)]
    for faces in edge_faces.values():
        if len(faces) != 2:
            continue
        first, second = faces
        adjacency[first].append(second)
        adjacency[second].append(first)

    labels = np.full(face_count, -1, dtype=np.int32)
    distances = np.full(face_count, np.inf, dtype=np.float64)
    queue: list[tuple[float, int, int]] = []
    for label, seed in enumerate(seeds):
        distances[seed] = 0.0
        labels[seed] = label
        heapq.heappush(queue, (0.0, label, seed))
    diagonal = max(1e-12, float(np.linalg.norm(np.ptp(centroids, axis=0))))
    while queue:
        distance, label, face = heapq.heappop(queue)
        if distance > distances[face] + 1e-15 or labels[face] != label:
            continue
        for neighbour in adjacency[face]:
            dot = float(np.clip(np.dot(normals[face], normals[neighbour]), -1.0, 1.0))
            angle = math.acos(dot) / math.pi
            step = float(np.linalg.norm(centroids[face] - centroids[neighbour])) / diagonal
            candidate = distance + max(1e-12, step) * (1.0 + curvature_weight * angle * angle)
            if candidate + 1e-15 < distances[neighbour]:
                distances[neighbour] = candidate
                labels[neighbour] = label
                heapq.heappush(queue, (candidate, label, neighbour))

    if np.any(labels < 0):
        raise RuntimeError("Region UV segmentation left unassigned faces.")
    merged_small_regions: list[dict[str, int]] = []
    while True:
        region_sizes = np.bincount(labels)
        small_regions = [
            region for region, size in enumerate(region_sizes)
            if 0 < size < 12
        ]
        if not small_regions:
            break
        changed = False
        for region in small_regions:
            neighbours: dict[int, int] = {}
            for faces in edge_faces.values():
                if len(faces) != 2:
                    continue
                first_region = int(labels[faces[0]])
                second_region = int(labels[faces[1]])
                if first_region == region and second_region != region:
                    neighbours[second_region] = neighbours.get(second_region, 0) + 1
                elif second_region == region and first_region != region:
                    neighbours[first_region] = neighbours.get(first_region, 0) + 1
            if not neighbours:
                continue
            destination = max(neighbours, key=lambda item: (neighbours[item], region_sizes[item]))
            face_total = int(np.count_nonzero(labels == region))
            labels[labels == region] = destination
            merged_small_regions.append({
                "region": region,
                "faces": face_total,
                "destination": destination,
                "shared_edges": neighbours[destination],
            })
            changed = True
        if not changed:
            break
    edge_by_key = {tuple(sorted((int(edge.vertices[0]), int(edge.vertices[1])))): edge for edge in mesh.edges}
    seam_edges = 0
    for key, faces in edge_faces.items():
        use_seam = len(faces) != 2 or labels[faces[0]] != labels[faces[1]]
        edge_by_key[key].use_seam = bool(use_seam)
        seam_edges += int(use_seam)
    region_sizes = np.bincount(labels, minlength=requested_regions)
    positive_sizes = region_sizes[region_sizes > 0]
    report = {
        "requested_regions": requested_regions,
        "produced_regions": int(np.count_nonzero(region_sizes)),
        "seam_edges": seam_edges,
        "minimum_region_faces": int(np.min(positive_sizes)),
        "median_region_faces": int(np.median(positive_sizes)),
        "maximum_region_faces": int(np.max(positive_sizes)),
        "curvature_weight": curvature_weight,
        "merged_small_regions": merged_small_regions,
    }
    state = {
        "labels": labels,
        "adjacency": adjacency,
        "edge_faces": edge_faces,
        "centroids": centroids,
        "normals": normals,
        "diagonal": diagonal,
        "curvature_weight": curvature_weight,
    }
    return report, state


def state_from_existing_uv(obj: Any) -> dict[str, Any]:
    mesh = obj.data
    layer = mesh.uv_layers.get(RUNTIME_UV_NAME)
    if layer is None:
        raise ValueError(f"{obj.name} has no UV layer named {RUNTIME_UV_NAME}.")
    face_count = len(mesh.polygons)
    parents = list(range(face_count))

    def find(item: int) -> int:
        while parents[item] != item:
            parents[item] = parents[parents[item]]
            item = parents[item]
        return item

    def union(first: int, second: int) -> None:
        first_root = find(first)
        second_root = find(second)
        if first_root != second_root:
            parents[second_root] = first_root

    face_uv_by_vertex: list[dict[int, np.ndarray]] = []
    edge_faces: dict[tuple[int, int], list[int]] = {}
    for polygon in mesh.polygons:
        face_uv_by_vertex.append({
            int(mesh.loops[loop_index].vertex_index): np.asarray(layer.data[loop_index].uv, dtype=np.float64)
            for loop_index in polygon.loop_indices
        })
        for edge in polygon.edge_keys:
            edge_faces.setdefault(tuple(sorted((int(edge[0]), int(edge[1])))), []).append(polygon.index)
    adjacency: list[list[int]] = [[] for _ in range(face_count)]
    for edge_key, faces in edge_faces.items():
        if len(faces) != 2:
            continue
        first, second = faces
        adjacency[first].append(second)
        adjacency[second].append(first)
        first_uv = face_uv_by_vertex[first]
        second_uv = face_uv_by_vertex[second]
        if all(
            vertex in first_uv
            and vertex in second_uv
            and float(np.linalg.norm(first_uv[vertex] - second_uv[vertex])) <= 1e-7
            for vertex in edge_key
        ):
            union(first, second)
    root_to_label: dict[int, int] = {}
    labels = np.empty(face_count, dtype=np.int32)
    for face_index in range(face_count):
        root = find(face_index)
        labels[face_index] = root_to_label.setdefault(root, len(root_to_label))
    centroids = np.array([tuple(polygon.center) for polygon in mesh.polygons], dtype=np.float64)
    normals = np.array([tuple(polygon.normal) for polygon in mesh.polygons], dtype=np.float64)
    diagonal = max(1e-12, float(np.linalg.norm(np.ptp(centroids, axis=0))))
    state = {
        "labels": labels,
        "adjacency": adjacency,
        "edge_faces": edge_faces,
        "centroids": centroids,
        "normals": normals,
        "diagonal": diagonal,
        "curvature_weight": 0.0,
    }
    apply_region_seams(obj, state)
    return state


def apply_region_seams(obj: Any, state: dict[str, Any]) -> int:
    labels = state["labels"]
    edge_by_key = {
        tuple(sorted((int(edge.vertices[0]), int(edge.vertices[1])))): edge
        for edge in obj.data.edges
    }
    seam_edges = 0
    for key, faces in state["edge_faces"].items():
        use_seam = len(faces) != 2 or labels[faces[0]] != labels[faces[1]]
        edge_by_key[key].use_seam = bool(use_seam)
        seam_edges += int(use_seam)
    return seam_edges


def region_faces(labels: np.ndarray) -> dict[int, list[int]]:
    result: dict[int, list[int]] = {}
    for face_index, region in enumerate(labels):
        result.setdefault(int(region), []).append(face_index)
    return result


def _interior_angles(points: np.ndarray) -> list[float]:
    angles: list[float] = []
    for index in range(3):
        first = points[(index + 1) % 3] - points[index]
        second = points[(index + 2) % 3] - points[index]
        denominator = float(np.linalg.norm(first) * np.linalg.norm(second))
        if denominator <= 1e-20:
            angles.append(0.0)
            continue
        cosine = float(np.clip(np.dot(first, second) / denominator, -1.0, 1.0))
        angles.append(math.degrees(math.acos(cosine)))
    return angles


def uv_chart_quality(obj: Any, face_indices: list[int], raster_size: int = 256) -> dict[str, Any]:
    layer = obj.data.uv_layers.get(RUNTIME_UV_NAME)
    if layer is None:
        raise RuntimeError("UV chart quality requires the runtime UV layer.")
    mesh = obj.data
    densities: list[float] = []
    angle_errors: list[float] = []
    positive = 0
    negative = 0
    triangles = 0
    nondegenerate = 0
    for face_index in face_indices:
        polygon = mesh.polygons[face_index]
        loops = list(polygon.loop_indices)
        for index in range(1, len(loops) - 1):
            triangle_loops = (loops[0], loops[index], loops[index + 1])
            points_3d = np.array([
                tuple(mesh.vertices[mesh.loops[loop].vertex_index].co)
                for loop in triangle_loops
            ], dtype=np.float64)
            points_uv = np.array([
                tuple(layer.data[loop].uv)
                for loop in triangle_loops
            ], dtype=np.float64)
            triangles += 1
            signed_uv_area = float(np.cross(points_uv[1] - points_uv[0], points_uv[2] - points_uv[0])) * 0.5
            uv_area = abs(signed_uv_area)
            surface_area = float(np.linalg.norm(np.cross(points_3d[1] - points_3d[0], points_3d[2] - points_3d[0]))) * 0.5
            if uv_area <= 1e-12 or surface_area <= 1e-20:
                continue
            nondegenerate += 1
            if signed_uv_area > 0:
                positive += 1
            else:
                negative += 1
            densities.append(uv_area / surface_area)
            angles_3d = _interior_angles(points_3d)
            angles_uv = _interior_angles(points_uv)
            angle_errors.extend(abs(first - second) for first, second in zip(angles_3d, angles_uv))
    median_density = float(np.median(densities)) if densities else 0.0
    area_deviation = (
        np.abs(np.log2(np.maximum(np.asarray(densities), 1e-30) / median_density))
        if median_density > 0.0 else np.asarray([], dtype=np.float64)
    )
    overlap = uv_overlap_metrics(
        obj,
        RUNTIME_UV_NAME,
        raster_size=raster_size,
        polygon_indices=face_indices,
    )
    oriented = positive + negative
    return {
        "triangles": triangles,
        "nondegenerate_triangles": nondegenerate,
        "nondegenerate_ratio": nondegenerate / max(1, triangles),
        "minor_orientation_ratio": min(positive, negative) / max(1, oriented),
        "angle_stretch_p95_degrees": float(np.percentile(angle_errors, 95)) if angle_errors else math.inf,
        "angle_stretch_max_degrees": max(angle_errors, default=math.inf),
        "area_log2_stretch_p95": float(np.percentile(area_deviation, 95)) if len(area_deviation) else math.inf,
        "area_log2_stretch_max": float(np.max(area_deviation)) if len(area_deviation) else math.inf,
        "overlap_ratio": overlap["overlap_ratio"],
        "overlapping_samples": overlap["overlapping_samples"],
        "maximum_overlap_layers": overlap["maximum_overlap_layers"],
    }


def unwrap_selected_chart(obj: Any, face_indices: list[int], margin: float, unwrap_method: str) -> None:
    select_only(obj)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="DESELECT")
    bpy.ops.object.mode_set(mode="OBJECT")
    for face_index in face_indices:
        obj.data.polygons[face_index].select = True
    previous_sync = bpy.context.scene.tool_settings.use_uv_select_sync
    bpy.context.scene.tool_settings.use_uv_select_sync = True
    try:
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.uv.unwrap(
            method="CONFORMAL" if unwrap_method == "conformal" else "ANGLE_BASED",
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
    finally:
        if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.context.scene.tool_settings.use_uv_select_sync = previous_sync


def region_merge_candidates(
    obj: Any,
    state: dict[str, Any],
    rejected_pairs: set[tuple[int, int]],
    maximum_chart_faces: int,
    minimum_shared_edges: int,
    maximum_boundary_angle_degrees: float,
) -> list[dict[str, Any]]:
    labels: np.ndarray = state["labels"]
    mesh = obj.data
    faces_by_region = region_faces(labels)
    surface_area = {
        region: sum(float(mesh.polygons[index].area) for index in faces)
        for region, faces in faces_by_region.items()
    }
    pairs: dict[tuple[int, int], dict[str, Any]] = {}
    for edge_key, faces in state["edge_faces"].items():
        if len(faces) != 2:
            continue
        first_region = int(labels[faces[0]])
        second_region = int(labels[faces[1]])
        if first_region == second_region:
            continue
        pair = tuple(sorted((first_region, second_region)))
        if pair in rejected_pairs:
            continue
        vertices = mesh.vertices[edge_key[0]].co, mesh.vertices[edge_key[1]].co
        length = float((vertices[0] - vertices[1]).length)
        dot = float(np.clip(np.dot(state["normals"][faces[0]], state["normals"][faces[1]]), -1.0, 1.0))
        angle = math.acos(dot)
        item = pairs.setdefault(pair, {
            "shared_length": 0.0,
            "weighted_angle": 0.0,
            "shared_edges": 0,
            "edge_keys": [],
        })
        item["shared_length"] = float(item["shared_length"]) + length
        item["weighted_angle"] = float(item["weighted_angle"]) + angle * length
        item["shared_edges"] = int(item["shared_edges"]) + 1
        item["edge_keys"].append(edge_key)
    result: list[dict[str, Any]] = []
    for (first, second), item in pairs.items():
        first_faces = len(faces_by_region[first])
        second_faces = len(faces_by_region[second])
        combined_faces = first_faces + second_faces
        if maximum_chart_faces > 0 and combined_faces > maximum_chart_faces:
            continue
        shared_length = float(item["shared_length"])
        mean_angle = float(item["weighted_angle"]) / max(shared_length, 1e-20)
        shared_edge_keys: list[tuple[int, int]] = item["edge_keys"]
        if (
            len(shared_edge_keys) < minimum_shared_edges
            or math.degrees(mean_angle) > maximum_boundary_angle_degrees
        ):
            continue
        vertex_edges: dict[int, list[int]] = {}
        for edge_index, edge_key in enumerate(shared_edge_keys):
            vertex_edges.setdefault(edge_key[0], []).append(edge_index)
            vertex_edges.setdefault(edge_key[1], []).append(edge_index)
        remaining = set(range(len(shared_edge_keys)))
        component_count = 0
        while remaining:
            component_count += 1
            stack = [remaining.pop()]
            while stack:
                edge_index = stack.pop()
                for vertex in shared_edge_keys[edge_index]:
                    for neighbour in vertex_edges[vertex]:
                        if neighbour in remaining:
                            remaining.remove(neighbour)
                            stack.append(neighbour)
        endpoint_count = sum(len(edges) == 1 for edges in vertex_edges.values())
        branch_count = sum(len(edges) > 2 for edges in vertex_edges.values())
        if component_count != 1 or endpoint_count != 2 or branch_count:
            continue
        normalized_boundary = shared_length / max(
            1e-20,
            math.sqrt(surface_area[first] + surface_area[second]),
        )
        balance = min(first_faces, second_faces) / max(first_faces, second_faces)
        score = normalized_boundary * math.exp(-5.0 * mean_angle) * (0.75 + 0.25 * balance)
        result.append({
            "first": first,
            "second": second,
            "first_faces": first_faces,
            "second_faces": second_faces,
            "combined_faces": combined_faces,
            "shared_edges": int(item["shared_edges"]),
            "shared_length": shared_length,
            "mean_boundary_angle_degrees": math.degrees(mean_angle),
            "shared_components": component_count,
            "shared_endpoints": endpoint_count,
            "shared_branches": branch_count,
            "score": score,
        })
    result.sort(key=lambda item: (item["score"], item["shared_edges"]), reverse=True)
    return result


def merge_regions_constrained(
    obj: Any,
    state: dict[str, Any],
    target_regions: int,
    maximum_chart_faces: int,
    maximum_trials: int,
    maximum_batch_size: int,
    maximum_angle_stretch: float,
    maximum_area_stretch: float,
    margin: float,
    unwrap_method: str,
) -> dict[str, Any]:
    labels: np.ndarray = state["labels"]
    target_regions = max(1, target_regions)
    rejected_pairs: set[tuple[int, int]] = set()
    accepted_sample: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    accepted_count = 0
    rejection_reasons = {
        "error": 0,
        "degenerate": 0,
        "mixed_orientation": 0,
        "overlap": 0,
        "angle_stretch": 0,
        "area_stretch": 0,
    }
    trial_count = 0
    batch_history: list[dict[str, Any]] = []
    stop_reason = ""
    if obj.data.uv_layers.get(RUNTIME_UV_NAME) is None:
        raise RuntimeError("Constrained merge requires the runtime UV layer.")
    starting_regions = len(region_faces(labels))
    density_scale = math.sqrt(max(1, len(obj.data.polygons)) / 50_000.0)
    minimum_shared_edges = max(1, math.ceil(4.0 * min(1.0, density_scale)))
    maximum_boundary_angle_degrees = min(45.0, 18.0 / max(0.4, density_scale))
    while True:
        current_faces = region_faces(labels)
        current_region_count = len(current_faces)
        if current_region_count <= target_regions:
            stop_reason = "target_reached"
            break
        if maximum_trials > 0 and trial_count >= maximum_trials:
            stop_reason = "trial_budget_exhausted"
            break
        candidates = region_merge_candidates(
            obj,
            state,
            rejected_pairs,
            maximum_chart_faces,
            minimum_shared_edges,
            maximum_boundary_angle_degrees,
        )
        if not candidates:
            stop_reason = "no_eligible_candidates"
            break
        remaining_trials = (
            maximum_trials - trial_count
            if maximum_trials > 0
            else len(candidates)
        )
        maximum_count = min(
            maximum_batch_size,
            current_region_count - target_regions,
            remaining_trials,
        )
        selected = select_disjoint_candidates(candidates, maximum_count)
        if not selected:
            stop_reason = "no_eligible_candidates"
            break
        layer = obj.data.uv_layers.get(RUNTIME_UV_NAME)
        if layer is None:
            raise RuntimeError("Constrained merge lost the runtime UV layer.")
        trials: list[dict[str, Any]] = []
        selected_faces: list[int] = []
        for candidate in selected:
            first = int(candidate["first"])
            second = int(candidate["second"])
            first_faces = current_faces[first]
            second_faces = current_faces[second]
            combined_faces = first_faces + second_faces
            loop_indices = [
                loop_index
                for face_index in combined_faces
                for loop_index in obj.data.polygons[face_index].loop_indices
            ]
            trials.append({
                "candidate": candidate,
                "first": first,
                "second": second,
                "second_faces": second_faces,
                "combined_faces": combined_faces,
                "loop_indices": loop_indices,
                "saved_uv": np.array(
                    [tuple(layer.data[index].uv) for index in loop_indices],
                    dtype=np.float64,
                ),
            })
            selected_faces.extend(combined_faces)
            labels[np.asarray(second_faces, dtype=np.int32)] = first
        apply_region_seams(obj, state)
        trial_count += len(trials)
        batch_error: str | None = None
        try:
            unwrap_selected_chart(obj, selected_faces, margin, unwrap_method)
        except Exception as exc:
            batch_error = f"{type(exc).__name__}: {exc}"
        accepted_in_batch = 0
        rejected_in_batch = 0
        changed_regions: set[int] = set()
        rejected_in_round: set[tuple[int, int]] = set()
        for trial in trials:
            first = int(trial["first"])
            second = int(trial["second"])
            if batch_error is not None:
                quality: dict[str, Any] = {"error": batch_error}
            else:
                try:
                    quality = uv_chart_quality(obj, trial["combined_faces"])
                except Exception as exc:
                    quality = {"error": f"{type(exc).__name__}: {exc}"}
            accepted_trial = (
                "error" not in quality
                and float(quality["nondegenerate_ratio"]) >= 1.0
                and float(quality["minor_orientation_ratio"]) <= 0.001
                and float(quality["overlap_ratio"]) <= 0.001
                and float(quality["angle_stretch_p95_degrees"]) <= maximum_angle_stretch
                and float(quality["area_log2_stretch_p95"]) <= maximum_area_stretch
            )
            record = {**trial["candidate"], "quality": quality}
            if accepted_trial:
                accepted_count += 1
                accepted_in_batch += 1
                changed_regions.update((first, second))
                if len(accepted_sample) < 16:
                    accepted_sample.append(record)
                continue
            rejected_in_batch += 1
            labels[np.asarray(trial["second_faces"], dtype=np.int32)] = second
            layer = obj.data.uv_layers.get(RUNTIME_UV_NAME)
            if layer is None:
                raise RuntimeError("Constrained merge lost the runtime UV layer during rollback.")
            for loop_index, coordinate in zip(trial["loop_indices"], trial["saved_uv"]):
                layer.data[loop_index].uv = coordinate
            rejected_in_round.add(tuple(sorted((first, second))))
            if "error" in quality:
                rejection_reasons["error"] += 1
            else:
                if float(quality["nondegenerate_ratio"]) < 1.0:
                    rejection_reasons["degenerate"] += 1
                if float(quality["minor_orientation_ratio"]) > 0.001:
                    rejection_reasons["mixed_orientation"] += 1
                if float(quality["overlap_ratio"]) > 0.001:
                    rejection_reasons["overlap"] += 1
                if float(quality["angle_stretch_p95_degrees"]) > maximum_angle_stretch:
                    rejection_reasons["angle_stretch"] += 1
                if float(quality["area_log2_stretch_p95"]) > maximum_area_stretch:
                    rejection_reasons["area_stretch"] += 1
            if len(rejected) < 16:
                rejected.append(record)
        rejected_pairs = {
            pair for pair in rejected_pairs
            if not changed_regions.intersection(pair)
        }
        rejected_pairs.update(rejected_in_round)
        apply_region_seams(obj, state)
        ending_regions = len(region_faces(labels))
        batch_history.append({
            "round": len(batch_history) + 1,
            "starting_regions": current_region_count,
            "sampled_target_regions": current_region_count - len(selected),
            "eligible_candidates": len(candidates),
            "selected_candidates": len(selected),
            "accepted_candidates": accepted_in_batch,
            "rejected_candidates": rejected_in_batch,
            "ending_regions": ending_regions,
            "cumulative_trials": trial_count,
        })
    apply_region_seams(obj, state)
    final_regions = region_faces(labels)
    return {
        "enabled": True,
        "starting_regions": starting_regions,
        "target_regions": target_regions,
        "produced_regions": len(final_regions),
        "trial_count": trial_count,
        "accepted_count": accepted_count,
        "rejected_count": trial_count - accepted_count,
        "strategy": "adaptive_disjoint_batches",
        "batch_count": len(batch_history),
        "batch_history": batch_history,
        "stop_reason": stop_reason,
        "search_complete": stop_reason != "trial_budget_exhausted",
        "quality_limited": False,
        "search_limited": stop_reason == "no_eligible_candidates" and len(final_regions) > target_regions,
        "limit_classification": (
            "candidate_space_exhausted"
            if stop_reason == "no_eligible_candidates" and len(final_regions) > target_regions
            else "none"
        ),
        "rejection_reasons": rejection_reasons,
        "maximum_chart_faces": maximum_chart_faces,
        "maximum_trials": maximum_trials,
        "maximum_batch_size": maximum_batch_size,
        "candidate_prefilter": {
            "minimum_shared_edges": minimum_shared_edges,
            "maximum_boundary_angle_degrees": maximum_boundary_angle_degrees,
            "mesh_density_scale": density_scale,
        },
        "maximum_angle_stretch": maximum_angle_stretch,
        "maximum_area_stretch": maximum_area_stretch,
        "accepted_sample": accepted_sample,
        "rejected_sample": rejected,
    }


def _polygon_uv_area(obj: Any, layer: Any, face_indices: list[int]) -> float:
    total = 0.0
    for face_index in face_indices:
        loops = list(obj.data.polygons[face_index].loop_indices)
        coordinates = [np.asarray(layer.data[index].uv, dtype=np.float64) for index in loops]
        for index in range(1, len(coordinates) - 1):
            total += abs(float(np.cross(coordinates[index] - coordinates[0], coordinates[index + 1] - coordinates[0]))) * 0.5
    return total


def _cluster_regions(charts: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    if not charts:
        return []
    centers = np.array([chart["center_3d"] for chart in charts], dtype=np.float64)
    extent = np.maximum(np.ptp(centers, axis=0), 1e-12)
    features = (centers - np.min(centers, axis=0)) / extent
    cluster_count = min(8, max(1, int(round(math.sqrt(len(charts)) / 2.0))))
    center = np.mean(features, axis=0)
    seeds = [int(np.argmax(np.sum((features - center) ** 2, axis=1)))]
    minimum_distance = np.sum((features - features[seeds[0]]) ** 2, axis=1)
    for _ in range(1, cluster_count):
        seed = int(np.argmax(minimum_distance))
        seeds.append(seed)
        minimum_distance = np.minimum(
            minimum_distance,
            np.sum((features - features[seed]) ** 2, axis=1),
        )
    cluster_centers = features[seeds].copy()
    assignments = np.zeros(len(charts), dtype=np.int32)
    for _ in range(12):
        distances = np.sum((features[:, None, :] - cluster_centers[None, :, :]) ** 2, axis=2)
        assignments = np.argmin(distances, axis=1).astype(np.int32)
        for cluster in range(cluster_count):
            members = features[assignments == cluster]
            if len(members):
                cluster_centers[cluster] = np.mean(members, axis=0)
    groups = [
        [chart for index, chart in enumerate(charts) if int(assignments[index]) == cluster]
        for cluster in range(cluster_count)
    ]
    groups = [group for group in groups if group]
    groups.sort(key=lambda group: (
        -float(np.mean([chart["center_3d"][2] for chart in group])),
        float(np.mean([chart["center_3d"][0] for chart in group])),
        float(np.mean([chart["center_3d"][1] for chart in group])),
    ))
    for group in groups:
        group.sort(key=lambda chart: (
            -float(chart["height"]),
            -float(chart["center_3d"][2]),
            float(chart["center_3d"][0]),
            float(chart["center_3d"][1]),
        ))
    return groups


def organize_region_islands(
    obj: Any,
    state: dict[str, Any],
    margin: float,
    packing_mode: str = "efficient",
) -> dict[str, Any]:
    layer = obj.data.uv_layers.get(RUNTIME_UV_NAME)
    if layer is None:
        raise RuntimeError("Island organization requires the runtime UV layer.")
    mesh = obj.data
    charts: list[dict[str, Any]] = []
    for region, faces in region_faces(state["labels"]).items():
        loop_indices = [
            loop_index
            for face_index in faces
            for loop_index in mesh.polygons[face_index].loop_indices
        ]
        uv = np.array([tuple(layer.data[index].uv) for index in loop_indices], dtype=np.float64)
        points_3d = np.array([
            tuple(mesh.vertices[mesh.loops[index].vertex_index].co)
            for index in loop_indices
        ], dtype=np.float64)
        center_3d = np.mean(points_3d, axis=0)
        centered_3d = points_3d - center_3d
        covariance = centered_3d.T @ centered_3d / max(1, len(centered_3d))
        eigenvalues, eigenvectors = np.linalg.eigh(covariance)
        principal = eigenvectors[:, int(np.argmax(eigenvalues))]
        stable_axis = int(np.argmax(np.abs(principal)))
        if principal[stable_axis] < 0:
            principal = -principal
        longitudinal = centered_3d @ principal
        uv_center = np.mean(uv, axis=0)
        centered_uv = uv - uv_center
        uv_direction = np.sum((longitudinal - np.mean(longitudinal))[:, None] * centered_uv, axis=0)
        if float(np.linalg.norm(uv_direction)) > 1e-15:
            current_angle = math.atan2(float(uv_direction[1]), float(uv_direction[0]))
            rotation = math.pi * 0.5 - current_angle
            cosine = math.cos(rotation)
            sine = math.sin(rotation)
            matrix = np.array(((cosine, -sine), (sine, cosine)), dtype=np.float64)
            centered_uv = centered_uv @ matrix.T
        surface_area = sum(float(mesh.polygons[index].area) for index in faces)
        uv_area = _polygon_uv_area(obj, layer, faces)
        density_scale = math.sqrt(surface_area / max(uv_area, 1e-20))
        centered_uv *= density_scale
        minimum = np.min(centered_uv, axis=0)
        maximum = np.max(centered_uv, axis=0)
        local_uv = centered_uv - minimum
        charts.append({
            "region": region,
            "faces": faces,
            "face_count": len(faces),
            "loop_indices": loop_indices,
            "local_uv": local_uv,
            "width": max(1e-12, float(maximum[0] - minimum[0])),
            "height": max(1e-12, float(maximum[1] - minimum[1])),
            "center_3d": center_3d,
            "surface_area": surface_area,
        })
    groups = _cluster_regions(charts)
    padding = max(1e-5, margin)
    group_gap = padding * 3.0

    group_report = [
        {
            "index": index,
            "regions": [int(chart["region"]) for chart in group],
            "faces": sum(int(chart["face_count"]) for chart in group),
            "center_3d": [
                float(np.mean([chart["center_3d"][axis] for chart in group]))
                for axis in range(3)
            ],
        }
        for index, group in enumerate(groups)
    ]
    if packing_mode == "efficient":
        for chart in charts:
            for loop_index, coordinate in zip(chart["loop_indices"], chart["local_uv"]):
                layer.data[loop_index].uv = coordinate
        select_only(obj)
        previous_sync = bpy.context.scene.tool_settings.use_uv_select_sync
        bpy.context.scene.tool_settings.use_uv_select_sync = True
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.uv.pack_islands(
            rotate=False,
            scale=True,
            merge_overlap=False,
            margin_method="FRACTION",
            margin=margin,
        )
        bpy.ops.object.mode_set(mode="OBJECT")
        bpy.context.scene.tool_settings.use_uv_select_sync = previous_sync
        return {
            "enabled": True,
            "packing_mode": packing_mode,
            "group_count": len(groups),
            "padding": padding,
            "groups": group_report,
        }

    def layout(scale: float, include_positions: bool = False) -> tuple[bool, dict[int, tuple[float, float]], float]:
        y = padding
        x = padding
        row_height = 0.0
        positions: dict[int, tuple[float, float]] = {}
        for group_index, group in enumerate(groups):
            if group_index:
                x += group_gap
                if x > 1.0 - padding:
                    y += row_height
                    x = padding
                    row_height = 0.0
            for chart in group:
                outer_width = float(chart["width"]) * scale + padding * 2.0
                outer_height = float(chart["height"]) * scale + padding * 2.0
                if outer_width > 1.0 - padding * 2.0:
                    return False, {}, math.inf
                if x + outer_width > 1.0 - padding and x > padding:
                    y += row_height
                    x = padding
                    row_height = 0.0
                if y + outer_height > 1.0 - padding:
                    return False, {}, y + outer_height
                if include_positions:
                    positions[int(chart["region"])] = (x + padding, y + padding)
                x += outer_width
                row_height = max(row_height, outer_height)
        used_height = y + row_height + padding
        return used_height <= 1.0, positions, used_height

    maximum_dimension = max(max(float(chart["width"]), float(chart["height"])) for chart in charts)
    low = 0.0
    high = 1.0 / max(maximum_dimension, 1e-20)
    while layout(high)[0] and high < 1e12:
        low = high
        high *= 2.0
    for _ in range(48):
        middle = (low + high) * 0.5
        if layout(middle)[0]:
            low = middle
        else:
            high = middle
    fits, positions, used_height = layout(low, include_positions=True)
    if not fits or not positions:
        raise RuntimeError("Could not organize UV regions into the 0-1 atlas.")
    vertical_offset = max(0.0, (1.0 - used_height) * 0.5)
    for chart in charts:
        origin = np.asarray(positions[int(chart["region"])], dtype=np.float64)
        coordinates = chart["local_uv"] * low + origin + np.array((0.0, vertical_offset))
        for loop_index, coordinate in zip(chart["loop_indices"], coordinates):
            layer.data[loop_index].uv = coordinate
    return {
        "enabled": True,
        "packing_mode": packing_mode,
        "group_count": len(groups),
        "packing_scale": low,
        "used_height": used_height,
        "padding": padding,
        "groups": group_report,
    }


def split_region(state: dict[str, Any], region: int) -> tuple[int, int] | None:
    labels: np.ndarray = state["labels"]
    faces = np.flatnonzero(labels == region)
    if len(faces) < 8:
        return None
    centroids: np.ndarray = state["centroids"]
    normals: np.ndarray = state["normals"]
    region_centroid = np.mean(centroids[faces], axis=0)
    first_seed = int(faces[np.argmax(np.sum((centroids[faces] - region_centroid) ** 2, axis=1))])
    second_seed = int(faces[np.argmax(np.sum((centroids[faces] - centroids[first_seed]) ** 2, axis=1))])
    if first_seed == second_seed:
        return None
    allowed = np.zeros(len(labels), dtype=np.bool_)
    allowed[faces] = True
    local_labels = np.full(len(labels), -1, dtype=np.int8)
    distances = np.full(len(labels), np.inf, dtype=np.float64)
    queue: list[tuple[float, int, int]] = []
    for label, seed in enumerate((first_seed, second_seed)):
        local_labels[seed] = label
        distances[seed] = 0.0
        heapq.heappush(queue, (0.0, label, seed))
    while queue:
        distance, label, face = heapq.heappop(queue)
        if distance > distances[face] + 1e-15 or local_labels[face] != label:
            continue
        for neighbour in state["adjacency"][face]:
            if not allowed[neighbour]:
                continue
            dot = float(np.clip(np.dot(normals[face], normals[neighbour]), -1.0, 1.0))
            angle = math.acos(dot) / math.pi
            step = float(np.linalg.norm(centroids[face] - centroids[neighbour])) / state["diagonal"]
            candidate = distance + max(1e-12, step) * (1.0 + state["curvature_weight"] * angle * angle)
            if candidate + 1e-15 < distances[neighbour]:
                distances[neighbour] = candidate
                local_labels[neighbour] = label
                heapq.heappush(queue, (candidate, label, neighbour))
    counts = np.bincount(local_labels[faces], minlength=2)
    if len(counts) < 2 or int(np.min(counts)) < 4:
        return None
    new_region = int(np.max(labels)) + 1
    labels[(labels == region) & (local_labels == 1)] = new_region
    return int(counts[0]), int(counts[1])


def generate_region_uv(
    obj: Any,
    requested_regions: int,
    curvature_weight: float,
    resolution: int,
    margin_pixels: int,
    unwrap_method: str,
    repair_degenerate: bool,
    repair_overlap_regions: bool,
    merge_regions: bool,
    target_regions: int,
    maximum_chart_faces: int,
    maximum_merge_trials: int,
    maximum_merge_batch_size: int,
    maximum_angle_stretch: float,
    maximum_area_stretch: float,
    organize_islands: bool,
    organization_packing: str,
) -> dict[str, Any]:
    existing = obj.data.uv_layers.get(RUNTIME_UV_NAME)
    if existing is not None:
        obj.data.uv_layers.remove(existing)
    runtime_uv = obj.data.uv_layers.new(name=RUNTIME_UV_NAME)
    obj.data.uv_layers.active_index = len(obj.data.uv_layers) - 1
    runtime_uv.active_render = True
    segmentation, state = region_seams(obj, requested_regions, curvature_weight)
    margin = max(0.0, margin_pixels / resolution)

    def unwrap_once(*, pack: bool = True) -> None:
        select_only(obj)
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.uv.unwrap(
            method="CONFORMAL" if unwrap_method == "conformal" else "ANGLE_BASED",
            fill_holes=True,
            correct_aspect=True,
            margin_method="FRACTION",
            margin=margin,
        )
        if pack:
            bpy.ops.uv.pack_islands(
                rotate=True,
                scale=True,
                merge_overlap=False,
                margin_method="FRACTION",
                margin=margin,
            )
        bpy.ops.object.mode_set(mode="OBJECT")

    def repair_overlaps(stage: str) -> list[dict[str, Any]]:
        history: list[dict[str, Any]] = []
        if not repair_overlap_regions:
            return history
        for iteration in range(4):
            global_overlap = uv_overlap_metrics(obj, RUNTIME_UV_NAME)
            global_area = uv_area_metrics(obj, RUNTIME_UV_NAME)
            if global_overlap["overlap_ratio"] <= 0.001 and global_area["nondegenerate_ratio"] >= 0.999:
                break
            labels: np.ndarray = state["labels"]
            region_faces: dict[int, list[int]] = {}
            for face_index, region in enumerate(labels):
                region_faces.setdefault(int(region), []).append(face_index)
            candidates: list[tuple[int, float, int]] = []
            for region, faces in region_faces.items():
                if len(faces) < 8:
                    continue
                metrics = uv_overlap_metrics(
                    obj,
                    RUNTIME_UV_NAME,
                    raster_size=256,
                    polygon_indices=faces,
                )
                if metrics["overlapping_samples"] > 1:
                    candidates.append((int(metrics["overlapping_samples"]), float(metrics["overlap_ratio"]), region))
            candidates.sort(reverse=True)
            split_regions: list[dict[str, int]] = []
            for _samples, _ratio, region in candidates[:32]:
                sizes = split_region(state, region)
                if sizes is not None:
                    split_regions.append({"region": region, "first_faces": sizes[0], "second_faces": sizes[1]})
            if not split_regions:
                break
            seam_edges = apply_region_seams(obj, state)
            history.append({
                "stage": stage,
                "iteration": iteration + 1,
                "global_overlap_ratio_before": global_overlap["overlap_ratio"],
                "nondegenerate_ratio_before": global_area["nondegenerate_ratio"],
                "candidate_regions": len(candidates),
                "split_regions": split_regions,
                "seam_edges": seam_edges,
            })
            unwrap_once()
        return history

    unwrap_once()
    overlap_history = repair_overlaps("initial")

    repair_history: list[dict[str, int]] = []
    if repair_degenerate:
        for repair_iteration in range(3):
            layer = obj.data.uv_layers.get(RUNTIME_UV_NAME)
            if layer is None:
                raise RuntimeError("Region unwrap lost its runtime UV layer.")
            degenerate_faces: list[int] = []
            for polygon in obj.data.polygons:
                coordinates = [layer.data[index].uv for index in polygon.loop_indices]
                area = 0.0
                for index in range(1, len(coordinates) - 1):
                    first = coordinates[index] - coordinates[0]
                    second = coordinates[index + 1] - coordinates[0]
                    area += abs(first.x * second.y - first.y * second.x) * 0.5
                if area <= 1e-12:
                    degenerate_faces.append(polygon.index)
            if not degenerate_faces:
                break
            edge_by_key = {
                tuple(sorted((int(edge.vertices[0]), int(edge.vertices[1])))): edge
                for edge in obj.data.edges
            }
            newly_marked = 0
            for face_index in degenerate_faces:
                for edge_key in obj.data.polygons[face_index].edge_keys:
                    edge = edge_by_key[tuple(sorted((int(edge_key[0]), int(edge_key[1]))))]
                    if not edge.use_seam:
                        edge.use_seam = True
                        newly_marked += 1
            repair_history.append({
                "iteration": repair_iteration + 1,
                "degenerate_faces": len(degenerate_faces),
                "new_seam_edges": newly_marked,
            })
            if newly_marked == 0:
                break
            unwrap_once()

    if merge_regions:
        merge_report = merge_regions_constrained(
            obj,
            state,
            target_regions,
            maximum_chart_faces,
            maximum_merge_trials,
            maximum_merge_batch_size,
            maximum_angle_stretch,
            maximum_area_stretch,
            margin,
            unwrap_method,
        )
        unwrap_once()
        overlap_history.extend(repair_overlaps("post_merge"))
    else:
        merge_report = {
            "enabled": False,
            "starting_regions": len(region_faces(state["labels"])),
            "target_regions": target_regions,
            "produced_regions": len(region_faces(state["labels"])),
            "trial_count": 0,
            "accepted_count": 0,
            "rejected_count": 0,
            "strategy": "adaptive_disjoint_batches",
            "batch_count": 0,
            "batch_history": [],
            "stop_reason": "disabled",
            "search_complete": True,
            "quality_limited": False,
            "search_limited": False,
            "limit_classification": "disabled",
        }

    if organize_islands:
        unwrap_once(pack=False)
        organization = organize_region_islands(obj, state, margin, organization_packing)
    else:
        organization = {"enabled": False}

    labels = state["labels"]
    final_region_sizes = np.bincount(labels)
    segmentation["produced_regions"] = int(np.count_nonzero(final_region_sizes))
    segmentation["minimum_region_faces"] = int(np.min(final_region_sizes[final_region_sizes > 0]))
    segmentation["median_region_faces"] = int(np.median(final_region_sizes[final_region_sizes > 0]))
    segmentation["maximum_region_faces"] = int(np.max(final_region_sizes))
    segmentation["seam_edges"] = sum(bool(edge.use_seam) for edge in obj.data.edges)
    segmentation["overlap_region_repair"] = overlap_history
    segmentation["degenerate_repair"] = repair_history
    segmentation["constrained_merge"] = merge_report
    merge_report["produced_regions_after_repair"] = segmentation["produced_regions"]
    segmentation["organization"] = organization
    return segmentation


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


def main() -> int:
    args = arguments()
    started = time.perf_counter()
    try:
        clear_scene()
        import_model(args.input.expanduser().resolve())
        meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
        if len(meshes) != 1:
            raise ValueError(f"UV probe expects one mesh object; found {len(meshes)}.")
        obj = meshes[0]
        if args.mode == "smart":
            generate_uv(obj, args.angle_degrees, args.resolution, args.margin_pixels)
            segmentation: dict[str, Any] = {}
        elif args.mode == "regions":
            selected_regions = args.regions
            sampling_report: dict[str, Any] = {
                "enabled": args.adaptive_initial_regions,
                "maximum_requested_regions": args.regions,
                "face_count": len(obj.data.polygons),
                "acceptance": {
                    "minimum_nondegenerate_ratio": ADAPTIVE_SAMPLE_MINIMUM_NONDEGENERATE_RATIO,
                    "maximum_overlap_ratio": ADAPTIVE_SAMPLE_MAXIMUM_OVERLAP_RATIO,
                },
                "samples": [],
            }
            if args.adaptive_initial_regions:
                candidates = adaptive_region_samples(len(obj.data.polygons), args.regions)
                sampling_report["candidates"] = candidates
                selected_regions = args.regions
                for candidate_regions in candidates:
                    sample_started = time.perf_counter()
                    sample_segmentation = generate_region_uv(
                        obj,
                        candidate_regions,
                        args.curvature_weight,
                        args.resolution,
                        args.margin_pixels,
                        args.unwrap_method,
                        args.repair_degenerate,
                        args.repair_overlap_regions,
                        False,
                        1,
                        0,
                        0,
                        args.maximum_merge_batch_size,
                        args.maximum_angle_stretch,
                        args.maximum_area_stretch,
                        False,
                        args.organization_packing,
                    )
                    sample_area = uv_area_metrics(obj, RUNTIME_UV_NAME)
                    sample_overlap = uv_overlap_metrics(obj, RUNTIME_UV_NAME)
                    sample_passed = bool(
                        sample_area["nondegenerate_ratio"]
                        >= ADAPTIVE_SAMPLE_MINIMUM_NONDEGENERATE_RATIO
                        and sample_overlap["overlap_ratio"]
                        <= ADAPTIVE_SAMPLE_MAXIMUM_OVERLAP_RATIO
                    )
                    sample_record = {
                        "requested_regions": candidate_regions,
                        "produced_regions": sample_segmentation["produced_regions"],
                        "nondegenerate_ratio": sample_area["nondegenerate_ratio"],
                        "overlap_ratio": sample_overlap["overlap_ratio"],
                        "elapsed_seconds": time.perf_counter() - sample_started,
                        "passed": sample_passed,
                    }
                    sampling_report["samples"].append(sample_record)
                    print("TMF_UV_REGION_SAMPLE " + json.dumps(sample_record), flush=True)
                    if sample_passed:
                        selected_regions = candidate_regions
                        break
                sampling_report["selected_regions"] = selected_regions
                sampling_report["fallback_to_maximum"] = not any(
                    bool(item["passed"]) for item in sampling_report["samples"]
                )
            segmentation = generate_region_uv(
                obj,
                selected_regions,
                args.curvature_weight,
                args.resolution,
                args.margin_pixels,
                args.unwrap_method,
                args.repair_degenerate,
                args.repair_overlap_regions,
                args.merge_regions,
                args.target_regions,
                args.maximum_chart_faces,
                args.maximum_merge_trials,
                args.maximum_merge_batch_size,
                args.maximum_angle_stretch,
                args.maximum_area_stretch,
                args.organize_islands,
                args.organization_packing,
            )
            segmentation["initial_region_sampling"] = sampling_report
        else:
            state = state_from_existing_uv(obj)
            labels = state["labels"]
            sizes = np.bincount(labels)
            organization = organize_region_islands(
                obj,
                state,
                max(0.0, args.margin_pixels / args.resolution),
                args.organization_packing,
            )
            segmentation = {
                "requested_regions": int(np.count_nonzero(sizes)),
                "produced_regions": int(np.count_nonzero(sizes)),
                "minimum_region_faces": int(np.min(sizes[sizes > 0])),
                "median_region_faces": int(np.median(sizes[sizes > 0])),
                "maximum_region_faces": int(np.max(sizes)),
                "seam_edges": sum(bool(edge.use_seam) for edge in obj.data.edges),
                "organization": organization,
            }
        area = uv_area_metrics(obj, RUNTIME_UV_NAME)
        overlap = uv_overlap_metrics(obj, RUNTIME_UV_NAME)
        export_target(obj, args.output)
        report = {
            "operation": "retopology_uv_probe",
            "input": str(args.input.resolve()),
            "output": str(args.output.resolve()),
            "angle_degrees": args.angle_degrees,
            "mode": args.mode,
            "segmentation": segmentation,
            "unwrap_method": args.unwrap_method,
            "repair_degenerate": args.repair_degenerate,
            "repair_overlap_regions": args.repair_overlap_regions,
            "merge_regions": args.merge_regions,
            "target_regions": args.target_regions,
            "maximum_chart_faces": args.maximum_chart_faces,
            "maximum_merge_trials": args.maximum_merge_trials,
            "maximum_merge_batch_size": args.maximum_merge_batch_size,
            "organize_islands": args.organize_islands,
            "organization_packing": args.organization_packing,
            "resolution": args.resolution,
            "margin_pixels": args.margin_pixels,
            "uv_area": area,
            "uv_overlap": overlap,
            "elapsed_seconds": time.perf_counter() - started,
            "passed": area["nondegenerate_ratio"] >= 0.999 and overlap["overlap_ratio"] <= 0.001,
        }
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("TMF_RETOPOLOGY_UV " + json.dumps(report), flush=True)
        return 0 if report["passed"] else 1
    except Exception as exc:
        failure = {
            "operation": "retopology_uv_probe",
            "errors": [{"type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc()}],
        }
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(failure, indent=2), encoding="utf-8")
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
