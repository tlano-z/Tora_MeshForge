from __future__ import annotations

from pathlib import Path
from typing import Any

from tora_meshforge.runtime_validation import build_runtime_readiness


def passing_report(tmp_path: Path, *, normal_enabled: bool = True) -> dict[str, Any]:
    output = tmp_path / "result.fbx"
    basecolor = tmp_path / "basecolor.png"
    invalid_basecolor = tmp_path / "invalid-basecolor.png"
    for path in (output, basecolor, invalid_basecolor):
        path.write_bytes(b"artifact")
    bake_object: dict[str, Any] = {
        "basecolor": str(basecolor),
        "invalid_mask": str(invalid_basecolor),
    }
    shape_normal: dict[str, Any] = {"enabled": False}
    if normal_enabled:
        normal = tmp_path / "normal.png"
        invalid_normal = tmp_path / "invalid-normal.png"
        normal.write_bytes(b"artifact")
        invalid_normal.write_bytes(b"artifact")
        bake_object.update({
            "normal": str(normal),
            "invalid_normal_mask": str(invalid_normal),
        })
        shape_normal = {
            "enabled": True,
            "tangents_valid": True,
            "normal_invalid_pixel_ratio": 0.0,
            "normal_non_finite_values": 0,
            "normal_decoded_length_mean": 1.0,
        }
    observed_material = {
        "basecolor_connected": True,
        "basecolor_srgb": True,
        "basecolor_resolution_valid": True,
        "normal_connected": normal_enabled,
        "normal_non_color": normal_enabled,
        "normal_resolution_valid": normal_enabled,
        "vertex_normals_finite": True,
        "tangents_recalculable": True,
    }
    return {
        "operation": "surface_retopology",
        "settings": {"target_triangles": 10_000, "texture_resolution": 2048},
        "output": {
            "path": str(output),
            "triangles": 10_000,
            "file_size_bytes": output.stat().st_size,
        },
        "surface": {"result": {
            "components": 1,
            "boundary_edges": 0,
            "overused_edges": 0,
            "degenerate_faces": 0,
        }},
        "uv": {
            "uv_area": {"nondegenerate_ratio": 1.0},
            "uv_overlap": {"overlap_ratio": 0.0005},
            "segmentation": {"produced_regions": 48},
        },
        "bake": {
            "invalid_pixel_ratio": 0.0,
            "objects": [bake_object],
            "shape_normal": shape_normal,
            "reload_validation": {
                "passed": True,
                "checks": {"single_target_mesh": True},
                "observed_material": observed_material,
            },
        },
        "material": {"normal_map": normal_enabled},
    }


def test_general_runtime_passes_complete_surface_output(tmp_path: Path) -> None:
    result = build_runtime_readiness(passing_report(tmp_path))
    assert result["profile"] == "general_runtime"
    assert result["status"] == "pass"
    assert result["ready"] is True
    assert result["summary"]["failed"] == 0
    assert result["manual_review_required"] is True
    assert result["platform_budget_applied"] is False


def test_general_runtime_reports_cross_layer_failures(tmp_path: Path) -> None:
    report = passing_report(tmp_path)
    report["output"]["triangles"] = 12_000
    report["uv"]["uv_overlap"]["overlap_ratio"] = 0.02
    report["bake"]["reload_validation"]["observed_material"]["normal_non_color"] = False
    result = build_runtime_readiness(report)
    assert result["status"] == "fail"
    assert result["ready"] is False
    assert {
        "geometry.triangle_budget",
        "uv.overlap",
        "material.normal",
    }.issubset(result["failed_checks"])


def test_general_runtime_accepts_optional_normal_disabled(tmp_path: Path) -> None:
    result = build_runtime_readiness(passing_report(tmp_path, normal_enabled=False))
    assert result["ready"] is True
    assert result["metrics"]["shape_normal_enabled"] is False
    assert not any(item["id"] == "material.normal" for item in result["checks"])
