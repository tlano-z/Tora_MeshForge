from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class RuntimeReadinessThresholds:
    """Engine-neutral thresholds already enforced by Surface Retopology."""

    triangle_tolerance_ratio: float = 0.02
    minimum_nondegenerate_uv_ratio: float = 0.999
    maximum_uv_overlap_ratio: float = 0.001
    maximum_invalid_projection_ratio: float = 0.01
    minimum_normal_vector_length: float = 0.90
    maximum_normal_vector_length: float = 1.10


def _nested(data: dict[str, Any], *keys: str, default: Any = None) -> Any:
    value: Any = data
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            return default
        value = value[key]
    return value


def _artifact_exists(value: Any) -> bool:
    if not value:
        return False
    path = Path(str(value)).expanduser()
    return path.is_file() and path.stat().st_size > 0


def build_runtime_readiness(
    report: dict[str, Any],
    thresholds: RuntimeReadinessThresholds | None = None,
) -> dict[str, Any]:
    """Evaluate an exported Surface Retopology asset as a general runtime asset.

    This deliberately applies no engine or platform triangle/texture budget. The
    requested triangle target and selected texture resolution are the budget.
    """

    limits = thresholds or RuntimeReadinessThresholds()
    checks: list[dict[str, Any]] = []

    def add(
        check_id: str,
        category: str,
        passed: bool,
        actual: Any,
        expected: str,
        message: str,
    ) -> None:
        checks.append({
            "id": check_id,
            "category": category,
            "severity": "error",
            "passed": bool(passed),
            "actual": actual,
            "expected": expected,
            "message": message,
        })

    settings = report.get("settings", {})
    output = report.get("output", {})
    surface_result = _nested(report, "surface", "result", default={}) or {}
    uv = report.get("uv", {})
    bake = report.get("bake", {})
    bake_objects = bake.get("objects", []) if isinstance(bake, dict) else []
    bake_object = bake_objects[0] if bake_objects else {}
    shape_normal = bake.get("shape_normal", {}) if isinstance(bake, dict) else {}
    reload_validation = bake.get("reload_validation", {}) if isinstance(bake, dict) else {}
    observed_material = reload_validation.get("observed_material", {})

    target_triangles = int(settings.get("target_triangles", 0) or 0)
    output_triangles = int(output.get("triangles", 0) or 0)
    triangle_limit = int(target_triangles * (1.0 + limits.triangle_tolerance_ratio))
    add(
        "geometry.triangle_budget",
        "geometry",
        target_triangles > 0 and 0 < output_triangles <= triangle_limit,
        output_triangles,
        f"1..{triangle_limit:,} triangles for requested {target_triangles:,}",
        "The exported mesh stays within the user-requested triangle budget.",
    )

    components = int(surface_result.get("components", 0) or 0)
    boundary_edges = int(surface_result.get("boundary_edges", -1) or 0)
    overused_edges = int(surface_result.get("overused_edges", -1) or 0)
    degenerate_faces = int(surface_result.get("degenerate_faces", -1) or 0)
    add(
        "geometry.single_component",
        "geometry",
        components == 1,
        components,
        "1 connected component",
        "The rebuilt runtime surface is a single connected component.",
    )
    add(
        "geometry.closed_manifold",
        "geometry",
        boundary_edges == 0 and overused_edges == 0,
        {"boundary_edges": boundary_edges, "overused_edges": overused_edges},
        "0 boundary edges and 0 overused edges",
        "The rebuilt surface has no open or overused topology edges.",
    )
    add(
        "geometry.no_degenerate_faces",
        "geometry",
        degenerate_faces == 0,
        degenerate_faces,
        "0 degenerate faces",
        "The rebuilt surface contains no zero-area faces.",
    )

    nondegenerate_ratio = float(_nested(uv, "uv_area", "nondegenerate_ratio", default=0.0) or 0.0)
    overlap_ratio = float(_nested(uv, "uv_overlap", "overlap_ratio", default=1.0) or 0.0)
    add(
        "uv.nondegenerate",
        "uv",
        nondegenerate_ratio >= limits.minimum_nondegenerate_uv_ratio,
        nondegenerate_ratio,
        f">= {limits.minimum_nondegenerate_uv_ratio:.4f}",
        "Runtime UV polygons retain usable area.",
    )
    add(
        "uv.overlap",
        "uv",
        overlap_ratio <= limits.maximum_uv_overlap_ratio,
        overlap_ratio,
        f"<= {limits.maximum_uv_overlap_ratio:.4f}",
        "Runtime UV overlap remains below the validated limit.",
    )

    base_invalid_ratio = float(bake.get("invalid_pixel_ratio", 1.0) or 0.0)
    add(
        "basecolor.projection",
        "texture",
        base_invalid_ratio <= limits.maximum_invalid_projection_ratio,
        base_invalid_ratio,
        f"<= {limits.maximum_invalid_projection_ratio:.2%}",
        "Base Color has no excessive failed projections inside occupied UVs.",
    )
    add(
        "basecolor.artifacts",
        "texture",
        _artifact_exists(bake_object.get("basecolor"))
        and _artifact_exists(bake_object.get("invalid_mask")),
        {
            "basecolor": bake_object.get("basecolor"),
            "invalid_mask": bake_object.get("invalid_mask"),
        },
        "non-empty Base Color and invalid-projection mask files",
        "Base Color diagnostic artifacts exist beside the output.",
    )

    normal_enabled = bool(shape_normal.get("enabled"))
    add(
        "normal.policy",
        "normal",
        True,
        "enabled" if normal_enabled else "disabled",
        "optional",
        "Shape Normal is optional; when enabled all Normal checks become required.",
    )
    if normal_enabled:
        normal_invalid_ratio = float(shape_normal.get("normal_invalid_pixel_ratio", 1.0) or 0.0)
        normal_non_finite = int(shape_normal.get("normal_non_finite_values", -1))
        normal_length = float(shape_normal.get("normal_decoded_length_mean", 0.0) or 0.0)
        add(
            "normal.tangents",
            "normal",
            bool(shape_normal.get("tangents_valid")),
            bool(shape_normal.get("tangents_valid")),
            "true",
            "The runtime UV supports tangent-space Normal generation.",
        )
        add(
            "normal.projection",
            "normal",
            normal_invalid_ratio <= limits.maximum_invalid_projection_ratio,
            normal_invalid_ratio,
            f"<= {limits.maximum_invalid_projection_ratio:.2%}",
            "Shape Normal has no excessive failed projections inside occupied UVs.",
        )
        add(
            "normal.finite_vectors",
            "normal",
            normal_non_finite == 0,
            normal_non_finite,
            "0 non-finite values",
            "The Normal texture contains finite vector data.",
        )
        add(
            "normal.vector_length",
            "normal",
            limits.minimum_normal_vector_length <= normal_length <= limits.maximum_normal_vector_length,
            normal_length,
            (
                f"{limits.minimum_normal_vector_length:.2f}.."
                f"{limits.maximum_normal_vector_length:.2f} mean decoded length"
            ),
            "Decoded tangent-space Normal vectors have a valid mean length.",
        )
        add(
            "normal.artifacts",
            "normal",
            _artifact_exists(bake_object.get("normal"))
            and _artifact_exists(bake_object.get("invalid_normal_mask")),
            {
                "normal": bake_object.get("normal"),
                "invalid_mask": bake_object.get("invalid_normal_mask"),
            },
            "non-empty Normal and invalid-projection mask files",
            "Shape Normal diagnostic artifacts exist beside the output.",
        )

    add(
        "material.basecolor",
        "material",
        bool(observed_material.get("basecolor_connected"))
        and bool(observed_material.get("basecolor_srgb"))
        and bool(observed_material.get("basecolor_resolution_valid")),
        {
            "connected": observed_material.get("basecolor_connected"),
            "srgb": observed_material.get("basecolor_srgb"),
            "resolution_valid": observed_material.get("basecolor_resolution_valid"),
        },
        "connected sRGB image at the selected resolution",
        "The reloaded FBX material uses the reconstructed Base Color correctly.",
    )
    if normal_enabled:
        add(
            "material.normal",
            "material",
            bool(observed_material.get("normal_connected"))
            and bool(observed_material.get("normal_non_color"))
            and bool(observed_material.get("normal_resolution_valid"))
            and bool(observed_material.get("tangents_recalculable")),
            {
                "connected": observed_material.get("normal_connected"),
                "non_color": observed_material.get("normal_non_color"),
                "resolution_valid": observed_material.get("normal_resolution_valid"),
                "tangents_recalculable": observed_material.get("tangents_recalculable"),
            },
            "connected Non-Color image with recalculable tangents",
            "The reloaded FBX material uses the generated tangent-space Normal correctly.",
        )

    add(
        "export.reload",
        "export",
        bool(reload_validation.get("passed")),
        reload_validation.get("checks", {}),
        "all FBX reload checks pass",
        "The final FBX survives an independent Blender reload with its runtime data intact.",
    )
    output_path = output.get("path")
    add(
        "export.output_file",
        "export",
        _artifact_exists(output_path),
        output_path,
        "non-empty public FBX file",
        "The final user-facing FBX exists and is non-empty.",
    )

    failed = [item for item in checks if not item["passed"]]
    passed = len(failed) == 0
    return {
        "profile": "general_runtime",
        "scope": "engine_agnostic_static_asset",
        "status": "pass" if passed else "fail",
        "ready": passed,
        "platform_budget_applied": False,
        "summary": {
            "checks": len(checks),
            "passed": len(checks) - len(failed),
            "failed": len(failed),
        },
        "checks": checks,
        "failed_checks": [item["id"] for item in failed],
        "metrics": {
            "requested_triangles": target_triangles,
            "output_triangles": output_triangles,
            "texture_resolution": settings.get("texture_resolution"),
            "uv_islands": _nested(uv, "segmentation", "produced_regions"),
            "uv_overlap_ratio": overlap_ratio,
            "invalid_basecolor_ratio": base_invalid_ratio,
            "shape_normal_enabled": normal_enabled,
            "output_file_size_bytes": output.get("file_size_bytes"),
        },
        "manual_review_required": True,
        "manual_review": [
            {
                "id": "visual_fidelity",
                "reason": "Confirm silhouette and local appearance against the matched source previews.",
            },
            {
                "id": "thin_and_contacting_parts",
                "reason": "Inspect fingers, hair tips, cords, and surfaces that touched before retopology.",
            },
            {
                "id": "uv_editability",
                "reason": "Confirm that the produced island count and layout are practical for the intended edits.",
            },
        ],
    }
