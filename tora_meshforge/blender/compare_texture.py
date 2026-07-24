"""Compare source and baked-candidate Base Color in both surface directions."""
from __future__ import annotations

import argparse
from bisect import bisect_left
from dataclasses import dataclass
import json
import math
from pathlib import Path
import sys
from typing import Any

import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from roundtrip_scene import apply_texture_override, clear_scene, import_model  # noqa: E402


GOLDEN_A = 0.7548776662466927
GOLDEN_B = 0.5698402909980532
LARGE_ERROR_THRESHOLD = 0.15
SEVERE_ERROR_THRESHOLD = 0.30


@dataclass(slots=True)
class ColorSampler:
    pixels: np.ndarray | None
    constant: np.ndarray

    def sample(self, uv: Vector) -> np.ndarray:
        if self.pixels is None:
            return self.constant
        height, width = self.pixels.shape[:2]
        u = max(0.0, min(1.0, float(uv.x))) * (width - 1)
        v = max(0.0, min(1.0, float(uv.y))) * (height - 1)
        x0 = int(math.floor(u))
        y0 = int(math.floor(v))
        x1 = min(width - 1, x0 + 1)
        y1 = min(height - 1, y0 + 1)
        fx = u - x0
        fy = v - y0
        first = self.pixels[y0, x0, :3] * (1.0 - fx) + self.pixels[y0, x1, :3] * fx
        second = self.pixels[y1, x0, :3] * (1.0 - fx) + self.pixels[y1, x1, :3] * fx
        return first * (1.0 - fy) + second * fy


@dataclass(slots=True)
class TexturedTriangle:
    points: tuple[Vector, Vector, Vector]
    uvs: tuple[Vector, Vector, Vector]
    sampler: ColorSampler
    area: float


def arguments() -> argparse.Namespace:
    values = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--source-texture", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=25_000)
    parser.add_argument("candidates", nargs="+", help="LABEL=FBX_PATH")
    return parser.parse_args(values)


def image_pixels(image: Any, cache: dict[int, np.ndarray]) -> np.ndarray:
    key = int(image.as_pointer())
    if key not in cache:
        width, height = int(image.size[0]), int(image.size[1])
        if width <= 0 or height <= 0:
            raise ValueError(f"Image {image.name!r} has no pixels.")
        values = np.empty(width * height * 4, dtype=np.float32)
        image.pixels.foreach_get(values)
        cache[key] = values.reshape((height, width, 4)).copy()
    return cache[key]


def material_sampler(material: Any, cache: dict[int, np.ndarray], warnings: list[str]) -> ColorSampler:
    constant = np.asarray((0.8, 0.8, 0.8), dtype=np.float32)
    if material is None:
        return ColorSampler(None, constant)
    constant = np.asarray(tuple(material.diffuse_color)[:3], dtype=np.float32)
    if not material.use_nodes or material.node_tree is None:
        return ColorSampler(None, constant)
    principled = next((node for node in material.node_tree.nodes if node.type == "BSDF_PRINCIPLED"), None)
    if principled is not None:
        base = principled.inputs.get("Base Color")
        if base is not None:
            constant = np.asarray(tuple(base.default_value)[:3], dtype=np.float32)
            if base.is_linked:
                linked_node = base.links[0].from_node
                if linked_node.type == "TEX_IMAGE" and linked_node.image is not None:
                    return ColorSampler(image_pixels(linked_node.image, cache), constant)
    image_nodes = [
        node for node in material.node_tree.nodes
        if node.type == "TEX_IMAGE" and node.image is not None
    ]
    if image_nodes:
        warnings.append(
            f"Material {material.name!r} did not directly link an image to Base Color; "
            "the first image node was used for color QA."
        )
        return ColorSampler(image_pixels(image_nodes[0].image, cache), constant)
    return ColorSampler(None, constant)


def textured_surface() -> dict[str, Any]:
    vertices: list[Vector] = []
    polygons: list[tuple[int, int, int]] = []
    triangles: list[TexturedTriangle] = []
    cumulative_areas: list[float] = []
    total_area = 0.0
    cache: dict[int, np.ndarray] = {}
    warnings: list[str] = []
    for obj in bpy.context.scene.objects:
        if obj.type != "MESH":
            continue
        mesh = obj.data
        mesh.calc_loop_triangles()
        uv_layer = next(
            (layer for layer in mesh.uv_layers if layer.active_render),
            mesh.uv_layers.active if mesh.uv_layers else None,
        )
        if uv_layer is None:
            raise ValueError(f"Mesh {obj.name!r} has no UV layer for texture QA.")
        world_vertices = [obj.matrix_world @ vertex.co for vertex in mesh.vertices]
        offset = len(vertices)
        vertices.extend(point.copy() for point in world_vertices)
        samplers = [material_sampler(slot.material, cache, warnings) for slot in obj.material_slots]
        fallback = ColorSampler(None, np.asarray((0.8, 0.8, 0.8), dtype=np.float32))
        for triangle in mesh.loop_triangles:
            indices = tuple(int(value) for value in triangle.vertices)
            points = tuple(world_vertices[index].copy() for index in indices)
            a, b, c = points
            area = float(((b - a).cross(c - a)).length * 0.5)
            if area <= 1e-20:
                continue
            uvs = tuple(uv_layer.data[int(loop_index)].uv.copy() for loop_index in triangle.loops)
            material_index = int(mesh.polygons[triangle.polygon_index].material_index)
            sampler = samplers[material_index] if material_index < len(samplers) else fallback
            polygons.append(tuple(offset + index for index in indices))
            triangles.append(TexturedTriangle(points, uvs, sampler, area))
            total_area += area
            cumulative_areas.append(total_area)
    if not vertices or not triangles or total_area <= 0.0:
        raise ValueError("Texture comparison requires textured non-degenerate mesh triangles.")
    return {
        "tree": BVHTree.FromPolygons(vertices, polygons, all_triangles=True),
        "triangles": triangles,
        "cumulative_areas": cumulative_areas,
        "total_area": total_area,
        "warnings": sorted(set(warnings)),
    }


def barycentric(point: Vector, triangle: tuple[Vector, Vector, Vector]) -> tuple[float, float, float]:
    a, b, c = triangle
    first = b - a
    second = c - a
    relative = point - a
    d00 = first.dot(first)
    d01 = first.dot(second)
    d11 = second.dot(second)
    d20 = relative.dot(first)
    d21 = relative.dot(second)
    denominator = d00 * d11 - d01 * d01
    if abs(denominator) <= 1e-30:
        return 1.0, 0.0, 0.0
    v = (d11 * d20 - d01 * d21) / denominator
    w = (d00 * d21 - d01 * d20) / denominator
    u = 1.0 - v - w
    return u, v, w


def interpolate_uv(triangle: TexturedTriangle, weights: tuple[float, float, float]) -> Vector:
    return (
        triangle.uvs[0] * weights[0]
        + triangle.uvs[1] * weights[1]
        + triangle.uvs[2] * weights[2]
    )


def sampled_surface_colors(surface: dict[str, Any], count: int) -> list[tuple[Vector, np.ndarray]]:
    total_area = float(surface["total_area"])
    cumulative = surface["cumulative_areas"]
    triangles: list[TexturedTriangle] = surface["triangles"]
    samples: list[tuple[Vector, np.ndarray]] = []
    for index in range(max(1, int(count))):
        area_position = (index + 0.5) / max(1, int(count)) * total_area
        triangle_index = min(len(triangles) - 1, bisect_left(cumulative, area_position))
        triangle = triangles[triangle_index]
        first = ((index + 1) * GOLDEN_A) % 1.0
        second = ((index + 1) * GOLDEN_B) % 1.0
        root = math.sqrt(first)
        weights = (1.0 - root, root * (1.0 - second), root * second)
        point = (
            triangle.points[0] * weights[0]
            + triangle.points[1] * weights[1]
            + triangle.points[2] * weights[2]
        )
        color = triangle.sampler.sample(interpolate_uv(triangle, weights))
        samples.append((point, color))
    return samples


def directional_errors(
    sampled_surface: dict[str, Any],
    reference_surface: dict[str, Any],
    count: int,
) -> list[float]:
    errors: list[float] = []
    for point, sampled_color in sampled_surface_colors(sampled_surface, count):
        nearest = reference_surface["tree"].find_nearest(point)
        if nearest is None:
            continue
        location, _, triangle_index, _ = nearest
        reference_triangle: TexturedTriangle = reference_surface["triangles"][int(triangle_index)]
        reference_uv = interpolate_uv(
            reference_triangle,
            barycentric(location, reference_triangle.points),
        )
        reference_color = reference_triangle.sampler.sample(reference_uv)
        errors.append(float(np.sqrt(np.mean((sampled_color - reference_color) ** 2))))
    return errors


def error_statistics(errors: list[float]) -> dict[str, Any]:
    if not errors:
        raise ValueError("Texture comparison produced no valid surface samples.")
    ordered = sorted(errors)

    def percentile(fraction: float) -> float:
        return ordered[min(len(ordered) - 1, int(len(ordered) * fraction))]

    mean = sum(errors) / len(errors)
    p95 = percentile(0.95)
    p99 = percentile(0.99)
    p99_9 = percentile(0.999)
    maximum = ordered[-1]
    worst_count = max(1, int(math.ceil(len(ordered) * 0.001)))
    worst_0_1_mean = sum(ordered[-worst_count:]) / worst_count
    return {
        "sample_count": len(errors),
        "mean_rgb_rmse": mean,
        "p95_rgb_rmse": p95,
        "p99_rgb_rmse": p99,
        "p99_9_rgb_rmse": p99_9,
        "worst_0_1_mean_rgb_rmse": worst_0_1_mean,
        "max_rgb_rmse": maximum,
        "mean_rgb_error_percent": mean * 100.0,
        "p95_rgb_error_percent": p95 * 100.0,
        "p99_rgb_error_percent": p99 * 100.0,
        "p99_9_rgb_error_percent": p99_9 * 100.0,
        "worst_0_1_mean_rgb_error_percent": worst_0_1_mean * 100.0,
        "max_rgb_error_percent": maximum * 100.0,
        "large_error_threshold": LARGE_ERROR_THRESHOLD,
        "large_error_ratio": sum(value >= LARGE_ERROR_THRESHOLD for value in errors) / len(errors),
        "severe_error_threshold": SEVERE_ERROR_THRESHOLD,
        "severe_error_ratio": sum(value >= SEVERE_ERROR_THRESHOLD for value in errors) / len(errors),
    }


def compare_candidate(source: dict[str, Any], candidate: dict[str, Any], count: int) -> dict[str, Any]:
    source_to_candidate_errors = directional_errors(source, candidate, count)
    candidate_to_source_errors = directional_errors(candidate, source, count)
    combined = source_to_candidate_errors + candidate_to_source_errors
    source_stats = error_statistics(source_to_candidate_errors)
    candidate_stats = error_statistics(candidate_to_source_errors)
    return {
        "sampling_method": "deterministic_area_weighted_nearest_surface",
        "samples_per_direction": count,
        "direction_weighting": "equal",
        **error_statistics(combined),
        "local_error_percent": max(
            float(source_stats["worst_0_1_mean_rgb_error_percent"]),
            float(candidate_stats["worst_0_1_mean_rgb_error_percent"]),
        ),
        "source_to_candidate": source_stats,
        "candidate_to_source": candidate_stats,
    }


def main() -> int:
    args = arguments()
    clear_scene()
    import_model(args.source)
    if args.source_texture:
        apply_texture_override(args.source_texture.expanduser().resolve())
    source = textured_surface()
    results: dict[str, Any] = {}
    warnings = list(source["warnings"])
    for candidate_value in args.candidates:
        label, path = candidate_value.split("=", 1)
        clear_scene()
        import_model(Path(path))
        candidate = textured_surface()
        warnings.extend(candidate["warnings"])
        results[label] = compare_candidate(source, candidate, args.samples)
    report = {
        "source": str(args.source.resolve()),
        "requested_samples": args.samples,
        "sampling_method": "deterministic_area_weighted_nearest_surface",
        "direction_weighting": "equal",
        "color_space": "Blender linear scene-referred RGB",
        "limitations": [
            "Direct Base Color image links and constant material colors are evaluated; procedural shader networks are approximated."
        ],
        "warnings": sorted(set(warnings)),
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("TMF_TEXTURE_JSON " + json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
