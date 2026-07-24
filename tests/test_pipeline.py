import json
from pathlib import Path
from typing import Any

import pytest

from tora_meshforge.config import AppConfig
from tora_meshforge.models import SurfaceRetopologyRequest
from tora_meshforge.pipeline import Pipeline
from tora_meshforge.utils.subprocess_runner import ProcessResult


def test_roundtrip_output_must_be_fbx(tmp_path: Path) -> None:
    source = tmp_path / "source.fbx"
    source.write_bytes(b"fixture")
    with pytest.raises(ValueError, match=".fbx"):
        Pipeline._validate_roundtrip_output(source, tmp_path / "output.obj")


def test_roundtrip_cannot_overwrite_source(tmp_path: Path) -> None:
    source = (tmp_path / "source.fbx").resolve()
    source.write_bytes(b"fixture")
    with pytest.raises(ValueError, match="differ"):
        Pipeline._validate_roundtrip_output(source, source)


def test_fast_optimize_target_floor_is_documented() -> None:
    from tora_meshforge.models import FastOptimizeRequest

    request = FastOptimizeRequest(Path("source.fbx"), Path("output.fbx"), 1_000)
    assert request.target_triangles == 1_000


def test_runtime_rebuild_defaults_are_practical() -> None:
    from tora_meshforge.models import RuntimeRebuildRequest

    request = RuntimeRebuildRequest(Path("source.fbx"), Path("output.fbx"), 50_000)
    assert request.texture_resolution_mode == "auto"
    assert request.maximum_texture_resolution == 4096
    assert request.uv_mode == "consolidated"
    assert request.uv_margin_pixels == 4
    assert request.preserve_small_parts is True


def test_surface_retopology_defaults_search_for_the_safe_minimum() -> None:
    from tora_meshforge.models import SurfaceRetopologyRequest

    request = SurfaceRetopologyRequest(Path("source.fbx"), Path("output.fbx"), 50_000)
    assert request.texture_resolution == 2048
    assert request.uv_margin_pixels == 4
    assert request.voxel_divisions == 768
    assert request.uv_regions == 192
    assert request.curvature_weight == 24.0
    assert request.adaptive_initial_regions is True
    assert request.uv_target_regions == 1
    assert request.maximum_chart_faces == 0
    assert request.maximum_merge_trials == 0
    assert request.maximum_merge_batch_size == 128
    assert request.organize_uv_islands is True
    assert request.bake_shape_normal is True


def test_triangle_sweep_bakes_shape_normal_by_default() -> None:
    from tora_meshforge.models import TriangleSweepRequest

    request = TriangleSweepRequest(Path("source.fbx"), Path("output"))
    assert request.bake_shape_normal is True


def test_surface_retopology_generates_single_evaluation_and_all_previews(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.fbx"
    source.write_bytes(b"source")
    blender = tmp_path / "blender.exe"
    blender.write_bytes(b"blender")
    output = tmp_path / "result.fbx"
    preview_commands: list[tuple[str, ...]] = []

    def fake_run_process(command: Any, **_: Any) -> ProcessResult:
        args = tuple(str(item) for item in command)
        script = Path(args[args.index("--python") + 1]).name
        if script == "retopology_probe.py":
            Path(args[args.index("--output") + 1]).write_bytes(b"retopo")
            Path(args[args.index("--report") + 1]).write_text(json.dumps({
                "passed_basic_validation": True,
                "source": {"triangles": 100_000, "vertices": 50_000},
                "result": {
                    "triangles": 9_998,
                    "vertices": 5_001,
                    "components": 1,
                    "boundary_edges": 0,
                    "overused_edges": 0,
                    "degenerate_faces": 0,
                },
            }), encoding="utf-8")
        elif script == "retopology_uv_probe.py":
            Path(args[args.index("--output") + 1]).write_bytes(b"uv")
            Path(args[args.index("--report") + 1]).write_text(json.dumps({
                "passed": True,
                "segmentation": {
                    "requested_regions": 192,
                    "produced_regions": 42,
                    "constrained_merge": {"enabled": True, "search_complete": True},
                    "organization": {"enabled": True},
                },
                "uv_area": {"nondegenerate_ratio": 1.0},
                "uv_overlap": {"overlap_ratio": 0.0},
            }), encoding="utf-8")
        elif script == "retopology_bake_probe.py":
            target = Path(args[args.index("--output") + 1])
            target.write_bytes(b"baked fbx")
            bake_directory = Path(args[args.index("--bake-dir") + 1])
            bake_directory.mkdir(parents=True, exist_ok=True)
            basecolor = bake_directory / "basecolor.png"
            invalid = bake_directory / "invalid.png"
            normal = bake_directory / "normal.png"
            invalid_normal = bake_directory / "invalid-normal.png"
            for path in (basecolor, invalid, normal, invalid_normal):
                path.write_bytes(b"png")
            Path(args[args.index("--report") + 1]).write_text(json.dumps({
                "passed": True,
                "basecolor": str(basecolor),
                "invalid_mask": str(invalid),
                "invalid_pixel_ratio": 0.0,
                "invalid_pixels": 0,
                "uv_coverage_ratio": 0.8,
                "normal": str(normal),
                "shape_normal": {
                    "enabled": True,
                    "invalid_normal_mask": str(invalid_normal),
                    "normal_invalid_pixel_ratio": 0.0,
                    "normal_invalid_pixels": 0,
                    "normal_non_finite_values": 0,
                    "normal_decoded_length_mean": 1.0,
                    "tangents_valid": True,
                },
                "reload_validation": {
                    "passed": True,
                    "checks": {"reloaded": True},
                    "observed_material": {
                        "basecolor_connected": True,
                        "basecolor_srgb": True,
                        "basecolor_resolution_valid": True,
                        "normal_connected": True,
                        "normal_non_color": True,
                        "normal_resolution_valid": True,
                        "tangents_recalculable": True,
                    },
                },
            }), encoding="utf-8")
        elif script in {"render_uv_layout.py", "render_preview.py"}:
            Path(args[args.index("--output") + 1]).write_bytes(b"png")
            if script == "render_preview.py":
                preview_commands.append(args)
                assert args[args.index("--frame-reference") + 1] == str(source.resolve())
        return ProcessResult(args, 0, "", 0.1)

    pipeline = Pipeline(
        AppConfig(blender_path=blender, work_directory=tmp_path / "jobs").resolved(tmp_path)
    )
    monkeypatch.setattr("tora_meshforge.pipeline.run_process", fake_run_process)

    result = pipeline.surface_retopology(SurfaceRetopologyRequest(
        input_path=source,
        output_path=output,
        target_triangles=10_000,
        blender_path=blender,
        work_directory=tmp_path / "jobs",
    ))

    assert result.report["status"] == "success"
    assert result.report["validation"]["checks"]["result_previews_complete"] is True
    assert result.report["validation"]["checks"]["source_previews_complete"] is True
    assert result.report["validation"]["checks"]["final_evaluation_complete"] is True
    assert len(preview_commands) == 24
    assert Path(result.report["artifacts"]["final_evaluation"]).is_file()
    assert "source_previews" in result.report["artifacts"]
