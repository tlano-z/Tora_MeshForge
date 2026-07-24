"""Deterministic, area-weighted bidirectional surface-distance QA."""
from __future__ import annotations

import argparse
from bisect import bisect_left
import json
import math
from pathlib import Path
import sys
from typing import Any

import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree

sys.path.insert(0, str(Path(__file__).resolve().parent))
from roundtrip_scene import clear_scene, import_model  # noqa: E402


GOLDEN_A = 0.7548776662466927
GOLDEN_B = 0.5698402909980532


def arguments() -> argparse.Namespace:
    values = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=25_000)
    parser.add_argument("candidates", nargs="+", help="LABEL=FBX_PATH")
    return parser.parse_args(values)


def build_surface() -> dict[str, Any]:
    vertices: list[Vector] = []
    polygons: list[tuple[int, int, int]] = []
    triangles: list[tuple[Vector, Vector, Vector]] = []
    cumulative_areas: list[float] = []
    total_area = 0.0
    minimum = Vector((math.inf, math.inf, math.inf))
    maximum = Vector((-math.inf, -math.inf, -math.inf))
    triangle_count = 0
    for obj in bpy.context.scene.objects:
        if obj.type != "MESH":
            continue
        mesh = obj.data
        mesh.calc_loop_triangles()
        world_vertices = [obj.matrix_world @ vertex.co for vertex in mesh.vertices]
        offset = len(vertices)
        vertices.extend(point.copy() for point in world_vertices)
        for point in world_vertices:
            for axis in range(3):
                minimum[axis] = min(minimum[axis], point[axis])
                maximum[axis] = max(maximum[axis], point[axis])
        for triangle in mesh.loop_triangles:
            indices = tuple(int(value) for value in triangle.vertices)
            a, b, c = (world_vertices[index].copy() for index in indices)
            area = ((b - a).cross(c - a)).length * 0.5
            if area <= 1e-20:
                continue
            polygons.append(tuple(offset + index for index in indices))
            triangles.append((a, b, c))
            total_area += float(area)
            cumulative_areas.append(total_area)
            triangle_count += 1
    if not vertices or not polygons or total_area <= 0.0:
        raise ValueError("Surface comparison requires non-degenerate mesh triangles.")
    return {
        "tree": BVHTree.FromPolygons(vertices, polygons, all_triangles=True),
        "triangles": triangles,
        "cumulative_areas": cumulative_areas,
        "total_area": total_area,
        "diagonal": (maximum - minimum).length,
        "bounds": {"minimum": list(minimum), "maximum": list(maximum)},
        "triangle_count": triangle_count,
    }


def area_weighted_points(surface: dict[str, Any], count: int) -> list[Vector]:
    """Return deterministic quasi-random points distributed by triangle area."""
    total_area = float(surface["total_area"])
    cumulative = surface["cumulative_areas"]
    triangles = surface["triangles"]
    sample_count = max(1, int(count))
    points: list[Vector] = []
    for index in range(sample_count):
        area_position = (index + 0.5) / sample_count * total_area
        triangle_index = min(len(triangles) - 1, bisect_left(cumulative, area_position))
        a, b, c = triangles[triangle_index]
        first = ((index + 1) * GOLDEN_A) % 1.0
        second = ((index + 1) * GOLDEN_B) % 1.0
        root = math.sqrt(first)
        points.append(a * (1.0 - root) + b * (root * (1.0 - second)) + c * (root * second))
    return points


def distances(points: list[Vector], surface: BVHTree) -> list[float]:
    result: list[float] = []
    for point in points:
        nearest = surface.find_nearest(point)
        if nearest is not None:
            result.append(float(nearest[3]))
    return result


def directional_statistics(values: list[float], diagonal: float) -> dict[str, Any]:
    ordered = sorted(values)
    rms = math.sqrt(sum(value * value for value in values) / max(1, len(values)))
    p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
    maximum = ordered[-1]
    return {
        "sample_count": len(values),
        "rms": rms,
        "p95": p95,
        "max": maximum,
        "rms_percent_of_bbox_diagonal": rms / diagonal * 100.0,
        "p95_percent_of_bbox_diagonal": p95 / diagonal * 100.0,
        "max_percent_of_bbox_diagonal": maximum / diagonal * 100.0,
    }


def statistics(source_to_candidate: list[float], candidate_to_source: list[float], diagonal: float) -> dict[str, Any]:
    combined = source_to_candidate + candidate_to_source
    symmetric = directional_statistics(combined, diagonal)
    return {
        "sampling_method": "deterministic_area_weighted",
        "samples_per_direction": min(len(source_to_candidate), len(candidate_to_source)),
        "sample_count": symmetric["sample_count"],
        "symmetric_rms": symmetric["rms"],
        "symmetric_p95": symmetric["p95"],
        "symmetric_max": symmetric["max"],
        "rms_percent_of_bbox_diagonal": symmetric["rms_percent_of_bbox_diagonal"],
        "p95_percent_of_bbox_diagonal": symmetric["p95_percent_of_bbox_diagonal"],
        "max_percent_of_bbox_diagonal": symmetric["max_percent_of_bbox_diagonal"],
        "source_to_candidate": directional_statistics(source_to_candidate, diagonal),
        "candidate_to_source": directional_statistics(candidate_to_source, diagonal),
    }


def main() -> int:
    args = arguments()
    clear_scene()
    import_model(args.source)
    source_surface = build_surface()
    source_points = area_weighted_points(source_surface, args.samples)
    results: dict[str, Any] = {}
    for candidate in args.candidates:
        label, path = candidate.split("=", 1)
        clear_scene()
        import_model(Path(path))
        candidate_surface = build_surface()
        candidate_points = area_weighted_points(candidate_surface, args.samples)
        results[label] = statistics(
            distances(source_points, candidate_surface["tree"]),
            distances(candidate_points, source_surface["tree"]),
            float(source_surface["diagonal"]),
        )
    report = {
        "source": str(args.source.resolve()),
        "source_triangles": int(source_surface["triangle_count"]),
        "source_bounds": source_surface["bounds"],
        "source_bbox_diagonal": source_surface["diagonal"],
        "requested_samples_per_direction": args.samples,
        "sampling_method": "deterministic_area_weighted",
        "direction_weighting": "equal",
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("TMF_SURFACE_JSON " + json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
