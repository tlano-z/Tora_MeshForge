from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any


ALLOWED_TEXTURE_RESOLUTIONS = (512, 1024, 2048, 4096, 8192)


@dataclass(frozen=True, slots=True)
class Recommendation:
    target_triangles: int
    maximum_runtime_triangles: int
    lightweight_target_triangles: int
    texture_resolution: int
    texture_reason: str
    estimate_minimum_seconds: int
    estimate_maximum_seconds: int
    temporary_disk_bytes: int


def _round_target(value: float) -> int:
    if value <= 10_000:
        return max(1_000, int(round(value / 1_000) * 1_000))
    return max(10_000, int(round(value / 5_000) * 5_000))


def recommend_target_triangles(source_triangles: int) -> int:
    """Recommend a practical runtime target, not a conservative quality target."""
    if source_triangles <= 50_000:
        return source_triangles
    return 50_000


def recommend_texture_resolution(
    maximum_source_dimension: int,
    material_count: int,
    texture_count: int,
    maximum_allowed: int = 8192,
) -> tuple[int, str]:
    source = max(512, maximum_source_dimension)
    candidate = next((item for item in ALLOWED_TEXTURE_RESOLUTIONS if item >= source), 8192)
    if material_count >= 4 or texture_count >= 4:
        candidate = min(8192, candidate * 2)
        reason = "Multiple source material or texture sets may need additional atlas area."
    elif maximum_source_dimension > 0:
        reason = "The recommendation matches the nearest supported source texture size."
    else:
        candidate = 2048
        reason = "No readable source texture size was found; 2048 is a conservative default."
    allowed = [item for item in ALLOWED_TEXTURE_RESOLUTIONS if item <= maximum_allowed]
    selected = min(candidate, max(allowed, default=512))
    if selected < candidate:
        reason += " It was capped by the configured maximum resolution."
    return selected, reason


def estimate_inspection_seconds(triangles: int, file_size_bytes: int, object_count: int) -> tuple[int, int]:
    size_mb = file_size_bytes / (1024 * 1024)
    base = 4.0 + size_mb * 0.08 + triangles / 350_000 + object_count * 0.15
    return max(2, math.ceil(base * 0.45)), max(5, math.ceil(base * 1.5))


def estimate_surface_retopology_seconds(
    target_triangles: int,
    texture_resolution: int = 2048,
) -> tuple[int, int]:
    """Return a deliberately broad wall-clock estimate for one rebuilt output.

    UV search dominates this workflow and varies strongly with model topology. The
    calibration therefore favors an honest range over false precision.
    """
    target = max(1_000, int(target_triangles))
    resolution = max(512, int(texture_resolution))
    central = 150.0 + target / 100.0
    texture_scale = 1.0 + max(0.0, (resolution / 2048.0) ** 2 - 1.0) * 0.15
    central *= texture_scale
    return max(120, math.floor(central * 0.55)), max(300, math.ceil(central * 1.8))


def estimate_triangle_sweep_seconds(
    triangle_targets: tuple[int, ...],
    texture_resolution: int = 2048,
) -> tuple[int, int]:
    """Return an estimated wall-clock range for candidates plus final comparison."""
    targets = tuple(int(value) for value in triangle_targets)
    if not targets:
        raise ValueError("Triangle Sweep requires at least one target for estimation.")
    single_ranges = [
        estimate_surface_retopology_seconds(target, texture_resolution)
        for target in targets
    ]
    comparison_minimum = 120 + len(targets) * 15
    comparison_maximum = 300 + len(targets) * 60
    return (
        sum(item[0] for item in single_ranges) + comparison_minimum,
        sum(item[1] for item in single_ranges) + comparison_maximum,
    )


def build_recommendation(report: dict[str, Any], maximum_texture_resolution: int = 8192) -> Recommendation:
    geometry = report.get("geometry", {})
    textures = report.get("textures", {})
    source = report.get("source", {})
    target = recommend_target_triangles(int(geometry.get("triangles", 0)))
    resolution, reason = recommend_texture_resolution(
        int(textures.get("maximum_dimension", 0)),
        int(geometry.get("materials", 0)),
        int(textures.get("count", 0)),
        maximum_texture_resolution,
    )
    minimum, maximum = estimate_inspection_seconds(
        int(geometry.get("triangles", 0)),
        int(source.get("file_size_bytes", 0)),
        int(geometry.get("objects", 0)),
    )
    temporary = int(int(source.get("file_size_bytes", 0)) * 3 + resolution * resolution * 4)
    return Recommendation(
        target_triangles=target,
        maximum_runtime_triangles=min(int(geometry.get("triangles", 0)), 100_000),
        lightweight_target_triangles=min(int(geometry.get("triangles", 0)), 10_000),
        texture_resolution=resolution,
        texture_reason=reason,
        estimate_minimum_seconds=minimum,
        estimate_maximum_seconds=maximum,
        temporary_disk_bytes=temporary,
    )
