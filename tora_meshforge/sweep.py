"""Pure helpers for triangle-count sweep configuration and summaries."""
from __future__ import annotations

from collections.abc import Iterable
from html import escape
import math
from pathlib import Path
import re
from typing import Any
from urllib.parse import quote


DEFAULT_TRIANGLE_SWEEP_TARGETS = (50_000, 25_000, 10_000, 5_000)
MINIMUM_TRIANGLE_TARGET = 1_000
MAXIMUM_TRIANGLE_TARGET = 10_000_000
MAXIMUM_SWEEP_TARGETS = 12
RECOMMENDATION_ROLE_LABELS = {
    "fidelity": "Fidelity",
    "balanced": "Balanced",
    "lightweight": "Lightweight",
}


def parse_triangle_targets(values: str | Iterable[int]) -> tuple[int, ...]:
    """Parse, validate, and de-duplicate triangle targets while preserving order."""
    if isinstance(values, str):
        # Accept both ``50000,25000`` and human-formatted ``50,000; 25,000``.
        normalized = re.sub(r"(?<=\d),(?=\d{3}(?:\D|$))", "", values)
        raw_values: Iterable[int | str] = [
            token for token in re.split(r"[,;\s]+", normalized.strip()) if token
        ]
    else:
        collected = list(values)
        if any(isinstance(value, str) for value in collected):
            return parse_triangle_targets(" ".join(str(value) for value in collected))
        raw_values = collected

    targets: list[int] = []
    seen: set[int] = set()
    for raw_value in raw_values:
        if isinstance(raw_value, int):
            target = raw_value
        else:
            token = str(raw_value).strip().lower().replace("_", "")
            multiplier = 1
            if token.endswith("k"):
                token = token[:-1]
                multiplier = 1_000
            elif token.endswith("m"):
                token = token[:-1]
                multiplier = 1_000_000
            try:
                target = int(float(token) * multiplier)
            except ValueError as exc:
                raise ValueError(f"Invalid triangle target: {raw_value!r}") from exc
        if not MINIMUM_TRIANGLE_TARGET <= target <= MAXIMUM_TRIANGLE_TARGET:
            raise ValueError(
                f"Triangle targets must be between {MINIMUM_TRIANGLE_TARGET:,} and "
                f"{MAXIMUM_TRIANGLE_TARGET:,}: {target:,}"
            )
        if target not in seen:
            seen.add(target)
            targets.append(target)
    if not targets:
        raise ValueError("At least one triangle target is required.")
    if len(targets) > MAXIMUM_SWEEP_TARGETS:
        raise ValueError(f"A sweep supports at most {MAXIMUM_SWEEP_TARGETS} triangle targets.")
    return tuple(targets)


def _measured_candidates(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    measured: list[dict[str, Any]] = []
    required = (
        "rms_percent_of_bbox_diagonal",
        "p95_percent_of_bbox_diagonal",
        "max_percent_of_bbox_diagonal",
    )
    for item in results:
        if item.get("status") != "success":
            continue
        distance = item.get("surface_distance")
        if not isinstance(distance, dict):
            continue
        try:
            metrics = [float(distance[key]) for key in required]
        except (KeyError, TypeError, ValueError):
            continue
        if all(math.isfinite(value) for value in metrics):
            measured.append(item)
    return measured


def _surface_metric(item: dict[str, Any], key: str) -> float:
    distance = item["surface_distance"]
    directional = distance.get("source_to_candidate", {})
    return float(directional.get(key, distance[key]))


def _texture_metric(item: dict[str, Any], key: str = "local_error_percent") -> float | None:
    texture = item.get("texture_quality")
    if not isinstance(texture, dict):
        return None
    value_source = texture.get(key)
    if value_source is None and key == "local_error_percent":
        # Compatibility with reports created before bidirectional local-error QA.
        value_source = texture.get("p99_rgb_error_percent")
    if value_source is None:
        return None
    value = float(value_source)
    return value if math.isfinite(value) else None


def _is_dominated(
    candidate: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    include_texture: bool,
) -> bool:
    candidate_values: tuple[float | int, ...] = (
        int(candidate["output_triangles"]),
        _surface_metric(candidate, "p95_percent_of_bbox_diagonal"),
        _surface_metric(candidate, "rms_percent_of_bbox_diagonal"),
    )
    if include_texture:
        candidate_values += (
            float(_texture_metric(candidate)),
            float(_texture_metric(candidate, "severe_error_ratio")),
        )
    for other in candidates:
        if other is candidate:
            continue
        other_values: tuple[float | int, ...] = (
            int(other["output_triangles"]),
            _surface_metric(other, "p95_percent_of_bbox_diagonal"),
            _surface_metric(other, "rms_percent_of_bbox_diagonal"),
        )
        if include_texture:
            other_values += (
                float(_texture_metric(other)),
                float(_texture_metric(other, "severe_error_ratio")),
            )
        if all(left <= right for left, right in zip(other_values, candidate_values, strict=True)) and any(
            left < right for left, right in zip(other_values, candidate_values, strict=True)
        ):
            return True
    return False


def _recommendation_record(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "roles": [],
        "labels": [],
        "selection": [],
        "target_triangles": int(item["target_triangles"]),
        "output_triangles": int(item["output_triangles"]),
        "output_path": item.get("output_path"),
        "output_directory": item.get("output_directory"),
        "uv_regions": int(item["uv_regions"]),
        "uv_overlap_ratio": float(item["uv_overlap_ratio"]),
        "invalid_basecolor_pixels": int(item["invalid_basecolor_pixels"]),
        "surface_distance": dict(item["surface_distance"]),
        "texture_quality": dict(item["texture_quality"]) if item.get("texture_quality") else None,
        "shape_normal": dict(item.get("shape_normal", {})),
        "runtime_readiness": dict(item.get("runtime_readiness", {})),
        "artifacts": dict(item.get("artifacts", {})),
    }


def recommend_sweep_candidates(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Select distinct fidelity, balance, and lightweight candidates from measured results."""
    measured = _measured_candidates(results)
    if not measured:
        reason = "Surface-distance measurements are unavailable for the successful candidates."
        return {
            "recommended_candidates": [],
            "unavailable_recommendation_roles": [
                {"role": role, "label": label, "reason": reason}
                for role, label in RECOMMENDATION_ROLE_LABELS.items()
            ],
        }
    include_texture = all(
        _texture_metric(item) is not None
        and _texture_metric(item, "severe_error_ratio") is not None
        for item in measured
    )

    def quality_key(item: dict[str, Any]) -> tuple[float, float, float, float, int]:
        distance = item["surface_distance"]
        return (
            _surface_metric(item, "p95_percent_of_bbox_diagonal"),
            float(_texture_metric(item)) if include_texture else 0.0,
            _surface_metric(item, "rms_percent_of_bbox_diagonal"),
            float(distance["max_percent_of_bbox_diagonal"]),
            -int(item["output_triangles"]),
        )

    fidelity = min(measured, key=quality_key)
    lightweight = min(
        measured,
        key=lambda item: (
            int(item["output_triangles"]),
            _surface_metric(item, "p95_percent_of_bbox_diagonal"),
            float(_texture_metric(item)) if include_texture else 0.0,
        ),
    )

    assignments: list[tuple[str, dict[str, Any], str, dict[str, float]]] = [
        (
            "fidelity",
            fidelity,
            (
                "Lowest source-to-candidate P95 surface distance; local Base Color error, RMS, and maximum distance are tie-breakers."
                if include_texture else
                "Lowest source-to-candidate P95 surface distance; RMS and maximum distance are tie-breakers."
            ),
            {},
        )
    ]
    unavailable: list[dict[str, str]] = []

    frontier = [
        item for item in measured
        if not _is_dominated(item, measured, include_texture=include_texture)
    ]
    middle = [
        item for item in frontier
        if item is not fidelity and item is not lightweight
    ]
    balanced: dict[str, Any] | None = None
    balance_details: dict[str, float] = {}
    if fidelity is lightweight:
        balance_reason = (
            "One candidate is both the lowest-error and lightest measured result, so no distinct balance point exists."
        )
    elif not middle:
        balance_reason = "No distinct non-dominated middle candidate is available."
    else:
        fidelity_triangles = float(fidelity["output_triangles"])
        lightweight_triangles = float(lightweight["output_triangles"])
        triangle_span = math.log(fidelity_triangles) - math.log(lightweight_triangles)
        quality_metric = "p95_percent_of_bbox_diagonal"
        fidelity_quality = _surface_metric(fidelity, quality_metric)
        quality_span = max(_surface_metric(item, quality_metric) for item in measured) - fidelity_quality
        if quality_span <= 1e-15:
            quality_metric = "rms_percent_of_bbox_diagonal"
            fidelity_quality = _surface_metric(fidelity, quality_metric)
            quality_span = max(_surface_metric(item, quality_metric) for item in measured) - fidelity_quality
        texture_quality = (
            min(float(_texture_metric(item)) for item in measured)
            if include_texture else 0.0
        )
        texture_span = (
            max(float(_texture_metric(item)) for item in measured) - texture_quality
            if include_texture else 0.0
        )
        if triangle_span <= 1e-15 or (quality_span <= 1e-15 and texture_span <= 1e-15):
            balance_reason = "The measured endpoints do not define a distinct triangle/quality trade-off."
        else:
            scored: list[tuple[float, float, float, dict[str, Any]]] = []
            for item in middle:
                triangle_saving = (
                    math.log(fidelity_triangles) - math.log(float(item["output_triangles"]))
                ) / triangle_span
                error_growth = (
                    (_surface_metric(item, quality_metric) - fidelity_quality) / quality_span
                    if quality_span > 1e-15 else 0.0
                )
                texture_growth = (
                    (float(_texture_metric(item)) - texture_quality) / texture_span
                    if include_texture and texture_span > 1e-15 else 0.0
                )
                triangle_saving = max(0.0, min(1.0, triangle_saving))
                error_growth = max(0.0, min(1.0, error_growth))
                texture_growth = max(0.0, min(1.0, texture_growth))
                quality_penalty = max(error_growth, texture_growth)
                score = triangle_saving - quality_penalty
                scored.append((score, triangle_saving, -quality_penalty, item))
            score, triangle_saving, negative_quality_penalty, balanced = max(
                scored,
                key=lambda value: (value[0], value[1], value[2]),
            )
            selected_surface_growth = (
                (_surface_metric(balanced, quality_metric) - fidelity_quality) / quality_span
                if quality_span > 1e-15 else 0.0
            )
            selected_texture_growth = (
                (float(_texture_metric(balanced)) - texture_quality) / texture_span
                if include_texture and texture_span > 1e-15 else 0.0
            )
            balance_details = {
                "tradeoff_score": score,
                "normalized_triangle_saving": triangle_saving,
                "normalized_surface_error_growth": max(0.0, min(1.0, selected_surface_growth)),
                "normalized_texture_error_growth": max(0.0, min(1.0, selected_texture_growth)),
                "normalized_quality_penalty": -negative_quality_penalty,
            }
            assignments.append((
                "balanced",
                balanced,
                (
                    "Knee point on the non-dominated triangle-count, surface-error, and local Base Color-error curve."
                    if include_texture else
                    "Knee point on the non-dominated triangle-count/surface-error curve."
                ),
                balance_details,
            ))

    if balanced is None:
        unavailable.append({
            "role": "balanced",
            "label": RECOMMENDATION_ROLE_LABELS["balanced"],
            "reason": balance_reason,
        })

    assignments.append((
        "lightweight",
        lightweight,
        (
            "Fewest output triangles among candidates with complete surface and Base Color measurements."
            if include_texture else
            "Fewest output triangles among candidates with complete surface-distance measurements."
        ),
        {},
    ))

    grouped: dict[int, dict[str, Any]] = {}
    ordered: list[dict[str, Any]] = []
    for role, item, basis, scores in assignments:
        identity = int(item["target_triangles"])
        record = grouped.get(identity)
        if record is None:
            record = _recommendation_record(item)
            grouped[identity] = record
            ordered.append(record)
        record["roles"].append(role)
        record["labels"].append(RECOMMENDATION_ROLE_LABELS[role])
        selection = {"role": role, "basis": basis}
        if scores:
            selection["scores"] = scores
        record["selection"].append(selection)

    return {
        "recommended_candidates": ordered,
        "unavailable_recommendation_roles": unavailable,
    }


def summarize_sweep_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Return factual extrema and objective-based final candidate recommendations."""
    successful = [item for item in results if item.get("status") == "success"]
    if not successful:
        summary = {
            "successful_candidates": 0,
            "lightest_passing_target": None,
            "fewest_uv_regions_target": None,
        }
        summary.update(recommend_sweep_candidates(results))
        return summary
    lightest = min(successful, key=lambda item: int(item["output_triangles"]))
    fewest_islands = min(
        successful,
        key=lambda item: (int(item["uv_regions"]), int(item["output_triangles"])),
    )
    summary = {
        "successful_candidates": len(successful),
        "lightest_passing_target": int(lightest["target_triangles"]),
        "fewest_uv_regions_target": int(fewest_islands["target_triangles"]),
        "fewest_uv_regions": int(fewest_islands["uv_regions"]),
    }
    summary.update(recommend_sweep_candidates(results))
    return summary


def _artifact_url(path_value: Any, output_directory: Path) -> str | None:
    if not path_value:
        return None
    path = Path(str(path_value)).expanduser().resolve()
    try:
        relative = path.relative_to(output_directory.resolve()).as_posix()
        return quote(relative, safe="/:")
    except ValueError:
        return path.as_uri()


def build_single_evaluation_html(report: dict[str, Any], output_directory: Path) -> str:
    """Build a portable source-versus-output review page for one rebuilt model."""
    artifacts = report.get("artifacts", {})
    source_artifacts = artifacts.get("source_previews", {})
    output_artifacts = dict(artifacts)
    bake = report.get("bake", {})
    bake_object = next(iter(bake.get("objects", [])), {})
    for key in ("basecolor", "invalid_mask", "normal", "invalid_normal_mask"):
        if bake_object.get(key):
            output_artifacts[key] = bake_object[key]

    def image_figures(values: dict[str, Any], fields: tuple[tuple[str, str], ...]) -> str:
        figures: list[str] = []
        for caption, key in fields:
            url = _artifact_url(values.get(key), output_directory)
            if url:
                figures.append(
                    '<figure><a href="{url}"><img src="{url}" alt="{caption}"></a>'
                    '<figcaption>{caption}</figcaption></figure>'.format(
                        url=escape(url, quote=True),
                        caption=escape(caption),
                    )
                )
            else:
                figures.append(
                    f'<figure class="missing"><div>Unavailable</div>'
                    f'<figcaption>{escape(caption)}</figcaption></figure>'
                )
        return "".join(figures)

    multi_view_fields = (
        ("Hero — Geometry", "preview_geometry"),
        ("Hero — Mesh", "preview_mesh"),
        ("Hero — Texture", "preview_texture"),
        ("Hero — Base Color + Normal", "preview_material"),
        ("Side — Geometry", "preview_side_geometry"),
        ("Side — Mesh", "preview_side_mesh"),
        ("Side — Texture", "preview_side_texture"),
        ("Side — Base Color + Normal", "preview_side_material"),
        ("Back — Geometry", "preview_back_geometry"),
        ("Back — Mesh", "preview_back_mesh"),
        ("Back — Texture", "preview_back_texture"),
        ("Back — Base Color + Normal", "preview_back_material"),
    )
    source_fields = (*multi_view_fields, ("SOURCE UV + Base Color", "uv_texture_layout"))
    output_fields = (
        *multi_view_fields,
        ("UV + Base Color", "uv_texture_layout"),
        ("UV only", "uv_layout"),
        ("Reconstructed Base Color", "basecolor"),
        ("Shape Normal", "normal"),
        ("Invalid Base Color projection", "invalid_mask"),
        ("Invalid Normal projection", "invalid_normal_mask"),
    )

    source = report.get("source", {})
    output = report.get("output", {})
    uv = report.get("uv", {})
    runtime = report.get("runtime_readiness", {})
    validation = report.get("validation", {})
    output_url = _artifact_url(output.get("path"), output_directory)
    output_link = (
        f'<a class="model-link" href="{escape(output_url, quote=True)}">Open output FBX</a>'
        if output_url else ""
    )
    source_triangles = int(source.get("triangles", 0) or 0)
    output_triangles = int(output.get("triangles", 0) or 0)
    uv_regions = int(uv.get("segmentation", {}).get("produced_regions", 0) or 0)
    overlap = float(uv.get("uv_overlap", {}).get("overlap_ratio", 0.0) or 0.0)
    normal_enabled = bool(bake.get("shape_normal", {}).get("enabled"))
    checks = validation.get("checks", {})
    passed_checks = sum(bool(value) for value in checks.values())
    status = str(report.get("status", "unknown")).upper()

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tora_MeshForge Single Target Evaluation</title>
<style>
:root {{ color-scheme: dark; font-family: Segoe UI, sans-serif; background: #15171a; color: #eef1f4; }}
body {{ margin: 0 auto; max-width: 1600px; padding: 28px; }}
h1 {{ margin-bottom: 6px; }} .intro {{ color: #b9c0c8; margin-top: 0; }}
.candidate {{ background: #20242a; border: 1px solid #383f48; border-radius: 12px; padding: 20px; margin: 20px 0; }}
.candidate-heading {{ display: flex; align-items: center; justify-content: space-between; gap: 20px; }}
.candidate h2 {{ margin: 4px 0; }} .role {{ color: #72c8ff; font-weight: 700; text-transform: uppercase; letter-spacing: .05em; }}
.metrics {{ display: flex; flex-wrap: wrap; gap: 10px; margin: 14px 0; }}
.metrics span {{ background: #16191d; border-radius: 7px; padding: 8px 11px; }}
.image-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }}
figure {{ background: #111316; border-radius: 8px; margin: 0; overflow: hidden; }}
figure img {{ display: block; width: 100%; height: auto; }} figcaption {{ padding: 8px 10px; color: #cbd1d7; }}
figure.missing div {{ min-height: 180px; display: grid; place-items: center; color: #7f8790; }}
a {{ color: #72c8ff; }} .model-link {{ white-space: nowrap; }}
@media (max-width: 900px) {{ .image-grid {{ grid-template-columns: 1fr; }} .candidate-heading {{ align-items: flex-start; flex-direction: column; }} }}
</style>
</head>
<body>
<h1>Tora_MeshForge Single Target Evaluation</h1>
<p class="intro">Compare SOURCE and OUTPUT under the same camera directions and preview modes. Automatic checks do not replace visual review of silhouettes, thin parts, contacting surfaces, textures, and UV editability.</p>
<section class="candidate source">
  <div class="candidate-heading"><div><span class="role">Source reference</span><h2>{source_triangles:,} triangles</h2></div></div>
  <div class="image-grid">{image_figures(source_artifacts, source_fields)}</div>
</section>
<section class="candidate">
  <div class="candidate-heading"><div><span class="role">Single target output</span><h2>{output_triangles:,} triangles</h2></div>{output_link}</div>
  <div class="metrics">
    <span><b>Status</b> {escape(status)}</span>
    <span><b>UV islands</b> {uv_regions:,}</span>
    <span><b>UV overlap</b> {overlap * 100.0:.5f}%</span>
    <span><b>Shape Normal</b> {"connected" if normal_enabled else "disabled"}</span>
    <span><b>Runtime Readiness</b> {escape(str(runtime.get('status', 'unknown')).upper())} ({int(runtime.get('summary', {}).get('passed', 0))}/{int(runtime.get('summary', {}).get('checks', 0))} checks)</span>
    <span><b>Processing validation</b> {passed_checks}/{len(checks)} checks</span>
  </div>
  <div class="image-grid">{image_figures(output_artifacts, output_fields)}</div>
</section>
</body>
</html>
"""


def build_sweep_evaluation_html(report: dict[str, Any], output_directory: Path) -> str:
    """Build a portable visual final-evaluation page for a completed sweep report."""
    analysis = report.get("analysis", {})
    recommendations = analysis.get("recommended_candidates", [])
    unavailable = analysis.get("unavailable_recommendation_roles", [])
    result_by_target = {
        int(item["target_triangles"]): item
        for item in report.get("results", [])
        if item.get("status") == "success"
    }

    def image_figures(artifacts: dict[str, Any], fields: tuple[tuple[str, str], ...]) -> str:
        figures: list[str] = []
        for caption, key in fields:
            url = _artifact_url(artifacts.get(key), output_directory)
            if url:
                figures.append(
                    '<figure><a href="{url}"><img src="{url}" alt="{caption}"></a>'
                    '<figcaption>{caption}</figcaption></figure>'.format(
                        url=escape(url, quote=True),
                        caption=escape(caption),
                    )
                )
            else:
                figures.append(
                    f'<figure class="missing"><div>Unavailable</div><figcaption>{escape(caption)}</figcaption></figure>'
                )
        return "".join(figures)

    multi_view_fields = (
        ("Hero — Geometry", "preview_geometry"),
        ("Hero — Mesh", "preview_mesh"),
        ("Hero — Texture", "preview_texture"),
        ("Hero — Base Color + Normal", "preview_material"),
        ("Side — Geometry", "preview_side_geometry"),
        ("Side — Mesh", "preview_side_mesh"),
        ("Side — Texture", "preview_side_texture"),
        ("Side — Base Color + Normal", "preview_side_material"),
        ("Back — Geometry", "preview_back_geometry"),
        ("Back — Mesh", "preview_back_mesh"),
        ("Back — Texture", "preview_back_texture"),
        ("Back — Base Color + Normal", "preview_back_material"),
    )
    source_artifacts = report.get("artifacts", {}).get("source_previews", {})
    source_triangles = report.get("comparison", {}).get("source_triangles")
    source_card = ""
    if source_artifacts:
        triangle_text = f"{int(source_triangles):,} triangles" if source_triangles is not None else "Source model"
        source_card = f"""
        <section class="candidate source">
          <div class="candidate-heading"><div><span class="role">Source reference</span><h2>{triangle_text}</h2></div></div>
          <p class="reason">All candidates use the same source bounds, camera directions, lighting, and preview modes.</p>
          <div class="image-grid">{image_figures(source_artifacts, (*multi_view_fields, ("SOURCE UV + Base Color", "uv_texture_layout")))}</div>
        </section>
        """

    cards: list[str] = []
    image_fields = (
        *multi_view_fields,
        ("UV + Base Color", "uv_texture_layout"),
        ("Shape Normal", "normal"),
        ("Invalid Normal projection", "invalid_normal_mask"),
    )
    for recommendation in recommendations:
        target = int(recommendation["target_triangles"])
        item = result_by_target.get(target, recommendation)
        distance = recommendation.get("surface_distance", {})
        source_distance = distance.get("source_to_candidate", distance)
        texture_quality = recommendation.get("texture_quality") or {}
        shape_normal = recommendation.get("shape_normal", {})
        runtime_readiness = recommendation.get("runtime_readiness", {})
        labels = " / ".join(str(value) for value in recommendation.get("labels", []))
        reasons = " ".join(
            f"{RECOMMENDATION_ROLE_LABELS.get(str(selection.get('role')), selection.get('role'))}: "
            f"{selection.get('basis', '')}"
            for selection in recommendation.get("selection", [])
        )
        artifacts = item.get("artifacts", {})
        output_url = _artifact_url(recommendation.get("output_path"), output_directory)
        output_link = (
            f'<a class="model-link" href="{escape(output_url, quote=True)}">Open output FBX</a>'
            if output_url else ""
        )
        cards.append(f"""
        <section class="candidate">
          <div class="candidate-heading">
            <div><span class="role">{escape(labels)}</span><h2>{target:,} target → {int(recommendation['output_triangles']):,} triangles</h2></div>
            {output_link}
          </div>
          <p class="reason">{escape(reasons)}</p>
          <div class="metrics">
            <span><b>Source → candidate P95</b> {float(source_distance['p95_percent_of_bbox_diagonal']):.5f}%</span>
            <span><b>Symmetric RMS</b> {float(distance['rms_percent_of_bbox_diagonal']):.5f}%</span>
            {f"<span><b>Local color error</b> {float(texture_quality.get('local_error_percent', texture_quality.get('p99_rgb_error_percent', 0.0))):.3f}%</span>" if texture_quality else ""}
            {f"<span><b>Texture P99</b> {float(texture_quality['p99_rgb_error_percent']):.3f}%</span>" if texture_quality and texture_quality.get('p99_rgb_error_percent') is not None else ""}
            {f"<span><b>Severe color errors</b> {float(texture_quality['severe_error_ratio']) * 100.0:.3f}%</span>" if texture_quality.get('severe_error_ratio') is not None else ""}
            <span><b>UV islands</b> {int(recommendation['uv_regions']):,}</span>
            <span><b>UV overlap</b> {float(recommendation['uv_overlap_ratio']) * 100.0:.5f}%</span>
            <span><b>Invalid pixels</b> {int(recommendation['invalid_basecolor_pixels']):,}</span>
            <span><b>Shape Normal</b> {"connected" if shape_normal.get("enabled") else "disabled"}</span>
            {f"<span><b>Invalid Normal pixels</b> {int(shape_normal.get('normal_invalid_pixels', 0)):,}</span>" if shape_normal.get('enabled') else ""}
            <span><b>Runtime Readiness</b> {escape(str(runtime_readiness.get('status', 'unknown')).upper())} ({int(runtime_readiness.get('summary', {}).get('passed', 0))}/{int(runtime_readiness.get('summary', {}).get('checks', 0))} checks)</span>
          </div>
          <div class="image-grid">{image_figures(artifacts, image_fields)}</div>
        </section>
        """)

    unavailable_html = "".join(
        f"<li><b>{escape(str(item.get('label', item.get('role', 'Candidate'))))}</b>: "
        f"{escape(str(item.get('reason', 'Unavailable')))}</li>"
        for item in unavailable
    )
    if unavailable_html:
        unavailable_html = f'<aside><h2>Unavailable roles</h2><ul>{unavailable_html}</ul></aside>'
    if not cards:
        cards.append(
            '<section class="candidate"><h2>No final candidates available</h2>'
            '<p>Surface-distance comparison must complete before objective-based candidates can be selected.</p></section>'
        )

    rows: list[str] = []
    for item in report.get("results", []):
        status = str(item.get("status", "unknown"))
        distance = item.get("surface_distance", {})
        if status == "success":
            source_distance = distance.get("source_to_candidate", distance)
            p95 = source_distance.get("p95_percent_of_bbox_diagonal")
            rms = distance.get("rms_percent_of_bbox_diagonal")
            texture = item.get("texture_quality", {})
            p95_text = f"{float(p95):.5f}%" if p95 is not None else "—"
            rms_text = f"{float(rms):.5f}%" if rms is not None else "—"
            local_texture = texture.get("local_error_percent", texture.get("p99_rgb_error_percent"))
            texture_text = f"{float(local_texture):.3f}%" if local_texture is not None else "—"
            output_url = _artifact_url(item.get("output_path"), output_directory)
            output_text = (
                f'<a href="{escape(output_url, quote=True)}">{int(item["output_triangles"]):,}</a>'
                if output_url else f"{int(item['output_triangles']):,}"
            )
            rows.append(
                "<tr>"
                f"<td>{int(item['target_triangles']):,}</td>"
                f"<td>{escape(status)}</td>"
                f"<td>{output_text}</td>"
                f"<td>{int(item['uv_regions']):,}</td>"
                f"<td>{p95_text}</td><td>{rms_text}</td><td>{texture_text}</td></tr>"
            )
        else:
            rows.append(
                "<tr>"
                f"<td>{int(item['target_triangles']):,}</td>"
                f"<td>{escape(status)}</td>"
                "<td>—</td><td>—</td><td>—</td><td>—</td><td>—</td></tr>"
            )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tora_MeshForge Final Evaluation</title>
<style>
:root {{ color-scheme: dark; font-family: Segoe UI, sans-serif; background: #15171a; color: #eef1f4; }}
body {{ margin: 0 auto; max-width: 1600px; padding: 28px; }}
h1 {{ margin-bottom: 6px; }} .intro {{ color: #b9c0c8; margin-top: 0; }}
.candidate {{ background: #20242a; border: 1px solid #383f48; border-radius: 12px; padding: 20px; margin: 20px 0; }}
.candidate-heading {{ display: flex; align-items: center; justify-content: space-between; gap: 20px; }}
.candidate h2 {{ margin: 4px 0; }} .role {{ color: #72c8ff; font-weight: 700; text-transform: uppercase; letter-spacing: .05em; }}
.reason {{ color: #c5cbd2; }} .metrics {{ display: flex; flex-wrap: wrap; gap: 10px; margin: 14px 0; }}
.metrics span {{ background: #16191d; border-radius: 7px; padding: 8px 11px; }}
.image-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }}
figure {{ background: #111316; border-radius: 8px; margin: 0; overflow: hidden; }}
figure img {{ display: block; width: 100%; height: auto; }} figcaption {{ padding: 8px 10px; color: #cbd1d7; }}
figure.missing div {{ min-height: 180px; display: grid; place-items: center; color: #7f8790; }}
a {{ color: #72c8ff; }} .model-link {{ white-space: nowrap; }}
aside {{ background: #2a2520; border: 1px solid #584a36; border-radius: 10px; padding: 12px 20px; }}
table {{ width: 100%; border-collapse: collapse; margin-top: 14px; }} th, td {{ text-align: right; padding: 9px; border-bottom: 1px solid #343a42; }}
th:nth-child(2), td:nth-child(2) {{ text-align: left; }}
@media (max-width: 900px) {{ .image-grid {{ grid-template-columns: 1fr; }} .candidate-heading {{ align-items: flex-start; flex-direction: column; }} }}
</style>
</head>
<body>
<h1>Tora_MeshForge Final Evaluation</h1>
<p class="intro">Candidates are selected from equal-direction, area-weighted surface distance and sampled Base Color error. Local color error is the worse directional mean over the highest-error 0.1% of samples, so small projection failures are not hidden by large clean areas. Fidelity minimizes missing-detail error, Balanced uses the non-dominated geometry/texture curve knee, and Lightweight uses the fewest validated measured triangles. Visual review remains required.</p>
{source_card}
{''.join(cards)}
{unavailable_html}
<section class="candidate"><h2>All sweep results</h2><table><thead><tr><th>Target</th><th>Status</th><th>Output triangles</th><th>UV islands</th><th>Source → candidate P95</th><th>Symmetric RMS</th><th>Local color error</th></tr></thead><tbody>{''.join(rows)}</tbody></table></section>
</body>
</html>
"""
