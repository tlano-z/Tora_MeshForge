from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable


ProgressCallback = Callable[[dict[str, Any]], None]
LogCallback = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class InspectionRequest:
    input_path: Path
    blender_path: Path | None = None
    work_directory: Path | None = None
    report_path: Path | None = None
    texture_path: Path | None = None


@dataclass(slots=True)
class InspectionResult:
    report_path: Path
    job_directory: Path
    report: dict[str, Any]
    logs: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["report_path"] = str(self.report_path)
        data["job_directory"] = str(self.job_directory)
        return data


@dataclass(frozen=True, slots=True)
class RoundTripRequest:
    input_path: Path
    output_path: Path
    blender_path: Path | None = None
    work_directory: Path | None = None
    report_path: Path | None = None
    texture_path: Path | None = None


@dataclass(slots=True)
class ProcessingResult:
    output_path: Path
    report_path: Path
    job_directory: Path
    report: dict[str, Any]
    logs: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key in ("output_path", "report_path", "job_directory"):
            data[key] = str(data[key])
        return data


@dataclass(slots=True)
class TriangleSweepResult:
    output_directory: Path
    report_path: Path
    report: dict[str, Any]
    logs: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["output_directory"] = str(self.output_directory)
        data["report_path"] = str(self.report_path)
        return data


@dataclass(frozen=True, slots=True)
class FastOptimizeRequest:
    input_path: Path
    output_path: Path
    target_triangles: int
    blender_path: Path | None = None
    work_directory: Path | None = None
    report_path: Path | None = None
    texture_path: Path | None = None
    preserve_small_parts: bool = True


@dataclass(frozen=True, slots=True)
class RuntimeRebuildRequest:
    input_path: Path
    output_path: Path
    target_triangles: int
    blender_path: Path | None = None
    work_directory: Path | None = None
    report_path: Path | None = None
    texture_path: Path | None = None
    texture_resolution_mode: str = "auto"
    manual_texture_resolution: int | None = None
    maximum_texture_resolution: int = 4096
    uv_mode: str = "consolidated"
    uv_margin_pixels: int = 4
    preserve_small_parts: bool = True


@dataclass(frozen=True, slots=True)
class SurfaceRetopologyRequest:
    input_path: Path
    output_path: Path
    target_triangles: int
    blender_path: Path | None = None
    work_directory: Path | None = None
    report_path: Path | None = None
    texture_path: Path | None = None
    texture_resolution: int = 2048
    uv_margin_pixels: int = 4
    voxel_divisions: int = 768
    uv_regions: int = 192
    curvature_weight: float = 24.0
    adaptive_initial_regions: bool = True
    uv_target_regions: int = 1
    maximum_chart_faces: int = 0
    maximum_merge_trials: int = 0
    maximum_merge_batch_size: int = 128
    organize_uv_islands: bool = True
    bake_shape_normal: bool = True


@dataclass(frozen=True, slots=True)
class TriangleSweepRequest:
    input_path: Path
    output_directory: Path
    triangle_targets: tuple[int, ...] = (50_000, 25_000, 10_000, 5_000)
    blender_path: Path | None = None
    work_directory: Path | None = None
    report_path: Path | None = None
    texture_path: Path | None = None
    texture_resolution: int = 2048
    uv_margin_pixels: int = 4
    voxel_divisions: int = 768
    uv_regions: int = 192
    curvature_weight: float = 24.0
    adaptive_initial_regions: bool = True
    uv_target_regions: int = 1
    maximum_chart_faces: int = 0
    maximum_merge_trials: int = 0
    maximum_merge_batch_size: int = 128
    organize_uv_islands: bool = True
    comparison_samples: int = 25_000
    bake_shape_normal: bool = True
