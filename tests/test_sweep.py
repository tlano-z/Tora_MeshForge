from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tora_meshforge.cli import build_parser
from tora_meshforge.config import AppConfig
from tora_meshforge.models import ProcessingResult, TriangleSweepRequest
from tora_meshforge.pipeline import Pipeline
from tora_meshforge.sweep import (
    DEFAULT_TRIANGLE_SWEEP_TARGETS,
    build_single_evaluation_html,
    build_sweep_evaluation_html,
    parse_triangle_targets,
    recommend_sweep_candidates,
    summarize_sweep_results,
)
from tora_meshforge.utils.cancellation import CancelledError
from tora_meshforge.utils.subprocess_runner import ProcessResult


def test_parse_triangle_targets_accepts_presets_suffixes_and_grouping() -> None:
    assert parse_triangle_targets("50000, 25000; 10k 5k") == DEFAULT_TRIANGLE_SWEEP_TARGETS
    assert parse_triangle_targets("50,000; 25,000; 10,000; 5,000") == DEFAULT_TRIANGLE_SWEEP_TARGETS
    assert parse_triangle_targets(["50k,25k", "10k", "5k"]) == DEFAULT_TRIANGLE_SWEEP_TARGETS


def test_parse_triangle_targets_preserves_order_and_removes_duplicates() -> None:
    assert parse_triangle_targets([10_000, 50_000, 10_000, 5_000]) == (10_000, 50_000, 5_000)


def test_parse_triangle_targets_rejects_invalid_values() -> None:
    with pytest.raises(ValueError, match="between"):
        parse_triangle_targets("500")
    with pytest.raises(ValueError, match="Invalid"):
        parse_triangle_targets("not-a-number")


def test_sweep_cli_uses_the_current_targets_by_default(tmp_path: Path) -> None:
    args = build_parser().parse_args([
        "sweep",
        "--input", str(tmp_path / "source.fbx"),
        "--output-directory", str(tmp_path / "sweep"),
    ])
    assert parse_triangle_targets(args.triangle_targets) == DEFAULT_TRIANGLE_SWEEP_TARGETS


def test_summarize_sweep_results_reports_factual_extrema() -> None:
    def candidate(target: int, regions: int, rms: float, p95: float) -> dict[str, Any]:
        return {
            "status": "success",
            "target_triangles": target,
            "output_triangles": target,
            "uv_regions": regions,
            "uv_overlap_ratio": 0.0005,
            "invalid_basecolor_pixels": 0,
            "surface_distance": {
                "rms_percent_of_bbox_diagonal": rms,
                "p95_percent_of_bbox_diagonal": p95,
                "max_percent_of_bbox_diagonal": p95 * 2.0,
            },
        }

    summary = summarize_sweep_results([
        candidate(50_000, 85, 0.02, 0.05),
        candidate(10_000, 44, 0.04, 0.08),
        candidate(5_000, 69, 0.10, 0.20),
    ])
    assert summary["successful_candidates"] == 3
    assert summary["lightest_passing_target"] == 5_000
    assert summary["fewest_uv_regions_target"] == 10_000
    assert summary["fewest_uv_regions"] == 44
    assert {
        role: item["target_triangles"]
        for item in summary["recommended_candidates"]
        for role in item["roles"]
    } == {
        "fidelity": 50_000,
        "balanced": 10_000,
        "lightweight": 5_000,
    }
    assert summary["unavailable_recommendation_roles"] == []


def test_recommendations_do_not_duplicate_candidates_when_only_two_are_measured() -> None:
    results = []
    for target, rms, p95 in ((3_000, 0.15, 0.26), (2_000, 0.19, 0.35)):
        results.append({
            "status": "success",
            "target_triangles": target,
            "output_triangles": target,
            "uv_regions": target // 100,
            "uv_overlap_ratio": 0.0,
            "invalid_basecolor_pixels": 0,
            "surface_distance": {
                "rms_percent_of_bbox_diagonal": rms,
                "p95_percent_of_bbox_diagonal": p95,
                "max_percent_of_bbox_diagonal": p95 * 2.0,
            },
        })

    recommendations = recommend_sweep_candidates(results)

    assert [item["target_triangles"] for item in recommendations["recommended_candidates"]] == [3_000, 2_000]
    assert recommendations["unavailable_recommendation_roles"][0]["role"] == "balanced"


def test_balanced_recommendation_accounts_for_local_texture_error() -> None:
    results = []
    for target, p95, local_color_error in (
        (50_000, 0.05, 2.0),
        (25_000, 0.06, 3.0),
        (10_000, 0.08, 20.0),
        (5_000, 0.20, 30.0),
    ):
        results.append({
            "status": "success",
            "target_triangles": target,
            "output_triangles": target,
            "uv_regions": 40,
            "uv_overlap_ratio": 0.0005,
            "invalid_basecolor_pixels": 0,
            "surface_distance": {
                "rms_percent_of_bbox_diagonal": p95 / 2.0,
                "p95_percent_of_bbox_diagonal": p95,
                "max_percent_of_bbox_diagonal": p95 * 2.0,
                "source_to_candidate": {
                    "rms_percent_of_bbox_diagonal": p95 / 2.0,
                    "p95_percent_of_bbox_diagonal": p95,
                },
            },
            "texture_quality": {
                "local_error_percent": local_color_error,
                "p99_rgb_error_percent": local_color_error / 2.0,
                "severe_error_ratio": local_color_error / 100.0,
            },
        })

    recommendations = recommend_sweep_candidates(results)
    assignments = {
        role: item["target_triangles"]
        for item in recommendations["recommended_candidates"]
        for role in item["roles"]
    }

    assert assignments == {"fidelity": 50_000, "balanced": 25_000, "lightweight": 5_000}


def test_final_evaluation_html_contains_candidate_visuals(tmp_path: Path) -> None:
    artifacts = {
        "preview_geometry": str(tmp_path / "preview-geometry.png"),
        "preview_mesh": str(tmp_path / "preview-mesh.png"),
        "preview_texture": str(tmp_path / "preview-texture.png"),
        "uv_texture_layout": str(tmp_path / "result.uv-layout-texture.png"),
    }
    item = {
        "status": "success",
        "target_triangles": 5_000,
        "output_triangles": 4_998,
        "output_path": str(tmp_path / "result.fbx"),
        "output_directory": str(tmp_path),
        "uv_regions": 42,
        "uv_overlap_ratio": 0.0005,
        "invalid_basecolor_pixels": 0,
        "surface_distance": {
            "rms_percent_of_bbox_diagonal": 0.08,
            "p95_percent_of_bbox_diagonal": 0.15,
            "max_percent_of_bbox_diagonal": 0.4,
        },
        "artifacts": artifacts,
    }
    source_uv_texture = tmp_path / "source-uv-layout-texture.png"
    report = {
        "results": [item],
        "analysis": recommend_sweep_candidates([item]),
        "artifacts": {"source_previews": {
            "preview_geometry": str(tmp_path / "source-preview-geometry.png"),
            "uv_texture_layout": str(source_uv_texture),
        }},
    }

    page = build_sweep_evaluation_html(report, tmp_path)

    assert "Fidelity / Lightweight" in page
    assert "preview-geometry.png" in page
    assert "result.uv-layout-texture.png" in page
    assert "UV + Base Color" in page
    assert "source-uv-layout-texture.png" in page
    assert "SOURCE UV + Base Color" in page


def test_single_evaluation_html_compares_source_output_and_uv(tmp_path: Path) -> None:
    output = tmp_path / "result.fbx"
    output.write_bytes(b"fbx")
    report = {
        "status": "success",
        "source": {"triangles": 100_000},
        "output": {"triangles": 9_998, "path": str(output)},
        "uv": {
            "segmentation": {"produced_regions": 42},
            "uv_overlap": {"overlap_ratio": 0.0005},
        },
        "bake": {
            "shape_normal": {"enabled": True},
            "objects": [{
                "basecolor": str(tmp_path / "basecolor.png"),
                "normal": str(tmp_path / "normal.png"),
            }],
        },
        "runtime_readiness": {
            "status": "pass",
            "summary": {"passed": 15, "checks": 15},
        },
        "validation": {"checks": {"surface_rebuilt": True}},
        "artifacts": {
            "preview_geometry": str(tmp_path / "result.preview-geometry.png"),
            "preview_texture": str(tmp_path / "result.preview-texture.png"),
            "uv_layout": str(tmp_path / "result.uv-layout.png"),
            "uv_texture_layout": str(tmp_path / "result.uv-layout-texture.png"),
            "source_previews": {
                "preview_geometry": str(tmp_path / "result.source-preview-geometry.png"),
                "uv_texture_layout": str(tmp_path / "result.source-uv-layout-texture.png"),
            },
        },
    }

    page = build_single_evaluation_html(report, tmp_path)

    assert "Single Target Evaluation" in page
    assert "Source reference" in page
    assert "Single target output" in page
    assert "100,000 triangles" in page
    assert "9,998 triangles" in page
    assert "result.source-preview-geometry.png" in page
    assert "result.preview-texture.png" in page
    assert "SOURCE UV + Base Color" in page
    assert "UV + Base Color" in page
    assert 'href="result.fbx"' in page


def test_triangle_sweep_orchestrates_candidates_comparison_and_previews(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.fbx"
    source.write_bytes(b"fixture")
    blender = tmp_path / "blender.exe"
    blender.write_bytes(b"fixture")
    pipeline = Pipeline(AppConfig(blender_path=blender, work_directory=tmp_path / "jobs").resolved(tmp_path))

    def fake_surface_retopology(
        request: Any,
        *,
        on_log: Any = None,
        on_progress: Any = None,
        _cancellation_token: Any = None,
        _generate_evaluation: bool = True,
    ) -> ProcessingResult:
        assert _cancellation_token is pipeline._cancellation
        assert _generate_evaluation is False
        assert request.bake_shape_normal is True
        request.output_path.parent.mkdir(parents=True, exist_ok=True)
        request.output_path.write_bytes(b"fbx")
        uv_layout = request.output_path.with_name("result.uv-layout.png")
        uv_texture_layout = request.output_path.with_name("result.uv-layout-texture.png")
        uv_layout.write_bytes(b"png")
        uv_texture_layout.write_bytes(b"png")
        normal = request.output_path.with_name("normal.png")
        invalid_normal = request.output_path.with_name("invalid-normal.png")
        normal.write_bytes(b"png")
        invalid_normal.write_bytes(b"png")
        report = {
            "output": {"triangles": request.target_triangles, "vertices": request.target_triangles // 2},
            "uv": {
                "segmentation": {
                    "requested_regions": 32,
                    "produced_regions": request.target_triangles // 1_000,
                    "initial_region_sampling": {"selected_regions": 24},
                },
                "uv_area": {"nondegenerate_ratio": 1.0},
                "uv_overlap": {"overlap_ratio": 0.0005},
            },
            "bake": {
                "uv_coverage_ratio": 0.6,
                "invalid_pixels": 0,
                "shape_normal": {
                    "enabled": True,
                    "normal_invalid_pixels": 0,
                    "normal_invalid_pixel_ratio": 0.0,
                },
                "objects": [{
                    "normal": str(normal),
                    "invalid_normal_mask": str(invalid_normal),
                }],
            },
            "timings": {"surface_seconds": 1.0},
            "warnings": [],
            "runtime_readiness": {
                "profile": "general_runtime",
                "status": "pass",
                "ready": True,
                "summary": {"checks": 15, "passed": 15, "failed": 0},
                "failed_checks": [],
            },
            "artifacts": {
                "uv_layout": str(uv_layout),
                "uv_texture_layout": str(uv_texture_layout),
            },
        }
        request.report_path.write_text(json.dumps(report), encoding="utf-8")
        if on_progress:
            on_progress({"stage": "complete", "progress": 1.0, "message": "complete"})
        if on_log:
            on_log("complete")
        return ProcessingResult(request.output_path, request.report_path, request.output_path.parent, report)

    def fake_run_process(command: Any, **_: Any) -> ProcessResult:
        args = tuple(str(item) for item in command)
        if any("compare_surface.py" in item for item in args):
            output = Path(args[args.index("--output") + 1])
            candidates = [item for item in args if "=" in item and not item.startswith("--")]
            distances = {
                "50000": (0.02, 0.05, 0.12),
                "10000": (0.04, 0.08, 0.20),
                "5000": (0.10, 0.20, 0.50),
            }
            output.write_text(json.dumps({
                "source": str(source),
                "source_triangles": 100_000,
                "source_bounds": {"minimum": [-1, -1, -1], "maximum": [1, 1, 1]},
                "results": {
                    item.split("=", 1)[0]: {
                        "rms_percent_of_bbox_diagonal": distances[item.split("=", 1)[0]][0],
                        "p95_percent_of_bbox_diagonal": distances[item.split("=", 1)[0]][1],
                        "max_percent_of_bbox_diagonal": distances[item.split("=", 1)[0]][2],
                        "source_to_candidate": {
                            "rms_percent_of_bbox_diagonal": distances[item.split("=", 1)[0]][0],
                            "p95_percent_of_bbox_diagonal": distances[item.split("=", 1)[0]][1],
                            "max_percent_of_bbox_diagonal": distances[item.split("=", 1)[0]][2],
                        },
                    }
                    for item in candidates
                },
            }), encoding="utf-8")
        if any("compare_texture.py" in item for item in args):
            output = Path(args[args.index("--output") + 1])
            candidates = [item for item in args if "=" in item and not item.startswith("--")]
            texture_errors = {"50000": (2.0, 0.001), "10000": (4.0, 0.002), "5000": (10.0, 0.01)}
            output.write_text(json.dumps({
                "source": str(source),
                "results": {
                    item.split("=", 1)[0]: {
                        "local_error_percent": texture_errors[item.split("=", 1)[0]][0],
                        "p99_rgb_error_percent": texture_errors[item.split("=", 1)[0]][0],
                        "severe_error_ratio": texture_errors[item.split("=", 1)[0]][1],
                    }
                    for item in candidates
                },
            }), encoding="utf-8")
        if any("render_preview.py" in item for item in args):
            Path(args[args.index("--output") + 1]).write_bytes(b"png")
        if any("render_uv_layout.py" in item for item in args):
            assert "--active-uv" in args
            assert args[args.index("--max-polygons") + 1] == "25000"
            Path(args[args.index("--output") + 1]).write_bytes(b"png")
        return ProcessResult(args, 0, "", 0.1)

    monkeypatch.setattr(pipeline, "surface_retopology", fake_surface_retopology)
    monkeypatch.setattr("tora_meshforge.pipeline.run_process", fake_run_process)
    result = pipeline.triangle_sweep(TriangleSweepRequest(
        input_path=source,
        output_directory=tmp_path / "sweep",
        triangle_targets=(50_000, 10_000, 5_000),
        blender_path=blender,
        work_directory=tmp_path / "jobs",
    ))

    assert result.report["status"] == "success"
    assert result.report["analysis"]["lightest_passing_target"] == 5_000
    assert result.report["analysis"]["fewest_uv_regions_target"] == 5_000
    assert result.report["validation"]["previews_complete"] is True
    assert result.report["validation"]["source_previews_complete"] is True
    assert Path(
        result.report["artifacts"]["source_previews"]["uv_texture_layout"]
    ).is_file()
    assert result.report["validation"]["texture_comparison_complete"] is True
    assert result.report["validation"]["runtime_readiness_complete"] is True
    assert result.report["runtime_readiness"]["all_candidates_ready"] is True
    assert result.report["validation"]["final_evaluation_complete"] is True
    assert Path(result.report["artifacts"]["final_evaluation"]).is_file()
    assert {
        role: item["target_triangles"]
        for item in result.report["analysis"]["recommended_candidates"]
        for role in item["roles"]
    } == {"fidelity": 50_000, "balanced": 10_000, "lightweight": 5_000}
    assert all("surface_distance" in item for item in result.report["results"])
    assert all("texture_quality" in item for item in result.report["results"])
    assert all(
        {
            "preview_geometry", "preview_mesh", "preview_texture", "preview_material",
            "preview_side_geometry", "preview_side_mesh", "preview_side_texture", "preview_side_material",
            "preview_back_geometry", "preview_back_mesh", "preview_back_texture", "preview_back_material",
            "normal", "invalid_normal_mask",
        }.issubset(item["artifacts"])
        for item in result.report["results"]
    )


def test_triangle_sweep_cancel_stops_before_the_next_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.fbx"
    source.write_bytes(b"fixture")
    blender = tmp_path / "blender.exe"
    blender.write_bytes(b"fixture")
    pipeline = Pipeline(
        AppConfig(blender_path=blender, work_directory=tmp_path / "jobs").resolved(tmp_path)
    )
    calls: list[int] = []

    def cancel_first_candidate(
        request: Any,
        **kwargs: Any,
    ) -> ProcessingResult:
        calls.append(request.target_triangles)
        token = kwargs["_cancellation_token"]
        assert token is pipeline._cancellation
        pipeline.cancel()
        token.raise_if_cancelled()
        raise AssertionError("Cancellation should have raised.")

    monkeypatch.setattr(pipeline, "surface_retopology", cancel_first_candidate)

    with pytest.raises(CancelledError):
        pipeline.triangle_sweep(TriangleSweepRequest(
            input_path=source,
            output_directory=tmp_path / "sweep",
            triangle_targets=(50_000, 25_000, 10_000),
            blender_path=blender,
            work_directory=tmp_path / "jobs",
        ))

    assert calls == [50_000]
