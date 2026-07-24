"""Inspect mesh and UV fragmentation in Blender background mode."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import sys
from typing import Any

import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from roundtrip_scene import clear_scene, import_model  # noqa: E402


class DisjointSet:
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

    def sizes(self) -> list[int]:
        counts = Counter(self.find(index) for index in range(len(self.parent)))
        return sorted(counts.values(), reverse=True)


def arguments() -> argparse.Namespace:
    values = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--uv-name", default="ToraMeshForgeUV")
    return parser.parse_args(values)


def object_diagonal(obj: Any) -> float:
    minimum = Vector((math.inf, math.inf, math.inf))
    maximum = Vector((-math.inf, -math.inf, -math.inf))
    for vertex in obj.data.vertices:
        coordinate = vertex.co
        for axis in range(3):
            minimum[axis] = min(minimum[axis], coordinate[axis])
            maximum[axis] = max(maximum[axis], coordinate[axis])
    return max(1e-12, (maximum - minimum).length)


def quantized_vertex(obj: Any, index: int, tolerance: float) -> tuple[int, int, int]:
    coordinate = obj.data.vertices[index].co
    return tuple(round(float(coordinate[axis]) / tolerance) for axis in range(3))


def mesh_edges(obj: Any, virtual_tolerance: float | None = None) -> dict[tuple[Any, Any], list[int]]:
    result: dict[tuple[Any, Any], list[int]] = defaultdict(list)
    virtual = None
    if virtual_tolerance is not None:
        virtual = [quantized_vertex(obj, vertex.index, virtual_tolerance) for vertex in obj.data.vertices]
    for polygon in obj.data.polygons:
        vertices = list(polygon.vertices)
        for index, first in enumerate(vertices):
            second = vertices[(index + 1) % len(vertices)]
            first_key: Any = virtual[first] if virtual is not None else int(first)
            second_key: Any = virtual[second] if virtual is not None else int(second)
            edge = tuple(sorted((first_key, second_key)))
            result[edge].append(polygon.index)
    return result


def component_sizes(face_count: int, edges: dict[tuple[Any, Any], list[int]]) -> list[int]:
    groups = DisjointSet(face_count)
    for faces in edges.values():
        first = faces[0]
        for face in faces[1:]:
            groups.union(first, face)
    return groups.sizes()


def uv_island_sizes(obj: Any, uv_name: str) -> list[int]:
    layer = obj.data.uv_layers.get(uv_name)
    if layer is None:
        raise ValueError(f"{obj.name} has no UV layer named {uv_name}.")
    groups = DisjointSet(len(obj.data.polygons))
    records: dict[tuple[int, int], list[tuple[int, dict[int, tuple[float, float]]]]] = defaultdict(list)
    for polygon in obj.data.polygons:
        loops = list(polygon.loop_indices)
        for offset, loop_index in enumerate(loops):
            next_loop = loops[(offset + 1) % len(loops)]
            first_vertex = int(obj.data.loops[loop_index].vertex_index)
            second_vertex = int(obj.data.loops[next_loop].vertex_index)
            first_uv = layer.data[loop_index].uv
            second_uv = layer.data[next_loop].uv
            records[tuple(sorted((first_vertex, second_vertex)))].append((
                polygon.index,
                {
                    first_vertex: (float(first_uv.x), float(first_uv.y)),
                    second_vertex: (float(second_uv.x), float(second_uv.y)),
                },
            ))
    for entries in records.values():
        if len(entries) < 2:
            continue
        first_face, first_uvs = entries[0]
        for face, uvs in entries[1:]:
            if all(
                abs(first_uvs[vertex][0] - uvs[vertex][0]) <= 1e-7
                and abs(first_uvs[vertex][1] - uvs[vertex][1]) <= 1e-7
                for vertex in first_uvs.keys() & uvs.keys()
            ):
                groups.union(first_face, face)
    return groups.sizes()


def distribution(sizes: list[int]) -> dict[str, Any]:
    total = sum(sizes)
    return {
        "count": len(sizes),
        "largest_faces": sizes[0] if sizes else 0,
        "largest_face_ratio": sizes[0] / max(1, total) if sizes else 0.0,
        "median_faces": sizes[len(sizes) // 2] if sizes else 0,
        "single_face_count": sum(size == 1 for size in sizes),
        "under_10_faces": sum(size < 10 for size in sizes),
        "under_100_faces": sum(size < 100 for size in sizes),
        "top_20_faces": sizes[:20],
    }


def main() -> int:
    args = arguments()
    clear_scene()
    import_model(args.input.resolve())
    meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    if len(meshes) != 1:
        raise ValueError(f"UV fragmentation probe expects one mesh; found {len(meshes)}.")
    obj = meshes[0]
    edges = mesh_edges(obj)
    topology = component_sizes(len(obj.data.polygons), edges)
    diagonal = object_diagonal(obj)
    virtual_results = {}
    for factor in (1e-7, 1e-6, 1e-5):
        tolerance = diagonal * factor
        virtual_edges = mesh_edges(obj, tolerance)
        sizes = component_sizes(len(obj.data.polygons), virtual_edges)
        virtual_results[f"{factor:.0e}"] = {
            "tolerance": tolerance,
            "components": distribution(sizes),
            "boundary_edges": sum(len(faces) == 1 for faces in virtual_edges.values()),
        }
    report = {
        "input": str(args.input.resolve()),
        "object": obj.name,
        "vertices": len(obj.data.vertices),
        "polygons": len(obj.data.polygons),
        "edges": len(edges),
        "boundary_edges": sum(len(faces) == 1 for faces in edges.values()),
        "nonmanifold_edges": sum(len(faces) != 2 for faces in edges.values()),
        "mesh_components": distribution(topology),
        "uv_islands": distribution(uv_island_sizes(obj, args.uv_name)),
        "virtual_weld": virtual_results,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("TMF_UV_FRAGMENTATION " + json.dumps(report), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
