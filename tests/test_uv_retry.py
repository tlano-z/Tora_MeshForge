from __future__ import annotations

from tora_meshforge.uv_retry import (
    command_with_fixed_uv_regions,
    final_uv_retry_candidates,
    uv_attempt_summary,
)


def test_final_uv_retry_candidates_continue_after_selected_sample() -> None:
    report = {
        "passed": False,
        "segmentation": {
            "initial_region_sampling": {
                "enabled": True,
                "selected_regions": 16,
                "candidates": [16, 24, 32, 48],
            }
        },
    }

    assert final_uv_retry_candidates(report) == (24, 32, 48)


def test_final_uv_retry_candidates_ignore_success_and_fixed_runs() -> None:
    assert final_uv_retry_candidates({"passed": True}) == ()
    assert final_uv_retry_candidates({"passed": False, "segmentation": {}}) == ()


def test_command_with_fixed_uv_regions_removes_adaptive_flag() -> None:
    command = ["blender", "--", "--regions", "192", "--adaptive-initial-regions", "--merge-regions"]

    assert command_with_fixed_uv_regions(command, 24) == [
        "blender", "--", "--regions", "24", "--merge-regions",
    ]


def test_uv_attempt_summary_records_final_metrics() -> None:
    report = {
        "passed": False,
        "segmentation": {"produced_regions": 18},
        "uv_area": {"nondegenerate_ratio": 1.0},
        "uv_overlap": {"overlap_ratio": 0.001005},
    }

    assert uv_attempt_summary(report, 16) == {
        "requested_regions": 16,
        "produced_regions": 18,
        "nondegenerate_ratio": 1.0,
        "overlap_ratio": 0.001005,
        "passed": False,
    }
