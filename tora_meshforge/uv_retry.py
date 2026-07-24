"""Helpers for retrying final UV validation with later adaptive region candidates."""
from __future__ import annotations

from typing import Any, Sequence


def final_uv_retry_candidates(report: dict[str, Any]) -> tuple[int, ...]:
    """Return untried adaptive region counts after a failed final UV validation."""
    if report.get("passed") is True:
        return ()
    sampling = report.get("segmentation", {}).get("initial_region_sampling", {})
    if sampling.get("enabled") is not True:
        return ()
    selected = sampling.get("selected_regions")
    candidates = sampling.get("candidates", [])
    try:
        selected_value = int(selected)
        normalized = [int(value) for value in candidates]
        selected_index = normalized.index(selected_value)
    except (TypeError, ValueError):
        return ()
    return tuple(value for value in normalized[selected_index + 1 :] if value > selected_value)


def command_with_fixed_uv_regions(command: Sequence[Any], regions: int) -> list[Any]:
    """Return a UV command that uses one fixed region count instead of adaptive sampling."""
    result = [value for value in command if str(value) != "--adaptive-initial-regions"]
    try:
        index = next(index for index, value in enumerate(result) if str(value) == "--regions")
    except StopIteration as exc:
        raise ValueError("UV command has no --regions argument.") from exc
    if index + 1 >= len(result):
        raise ValueError("UV command has no value after --regions.")
    result[index + 1] = str(regions)
    return result


def uv_attempt_summary(report: dict[str, Any], requested_regions: int) -> dict[str, Any]:
    """Extract compact final-validation evidence for one UV attempt."""
    segmentation = report.get("segmentation", {})
    return {
        "requested_regions": requested_regions,
        "produced_regions": segmentation.get("produced_regions"),
        "nondegenerate_ratio": report.get("uv_area", {}).get("nondegenerate_ratio"),
        "overlap_ratio": report.get("uv_overlap", {}).get("overlap_ratio"),
        "passed": bool(report.get("passed")),
    }
