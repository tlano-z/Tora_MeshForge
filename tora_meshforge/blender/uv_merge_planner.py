"""Pure planning helpers for adaptive UV-region merging."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def adaptive_region_samples(
    face_count: int,
    requested_regions: int,
    *,
    minimum_regions: int = 16,
    reference_faces: int = 50_000,
) -> list[int]:
    """Return low-to-high initial region counts scaled to mesh density."""
    maximum = max(minimum_regions, min(requested_regions, max(1, face_count)))
    scaled = maximum * max(1, face_count) / max(1, reference_faces)
    lower_bound = max(minimum_regions, int(scaled * 0.4))
    preferred = (16, 24, 32, 48, 64, 96, 128, 160, 192, 256, 384, 512, 768, 1024)
    samples = [value for value in preferred if lower_bound <= value <= maximum]
    if maximum not in samples:
        samples.append(maximum)
    return sorted(set(samples))


def select_disjoint_candidates(
    candidates: Sequence[Mapping[str, Any]],
    maximum_count: int,
) -> list[dict[str, Any]]:
    """Select the highest-ranked region pairs that can run in one batch."""
    if maximum_count < 1:
        return []
    selected: list[dict[str, Any]] = []
    used_regions: set[int] = set()
    for candidate in candidates:
        first = int(candidate["first"])
        second = int(candidate["second"])
        if first in used_regions or second in used_regions:
            continue
        selected.append(dict(candidate))
        used_regions.update((first, second))
        if len(selected) >= maximum_count:
            break
    return selected
