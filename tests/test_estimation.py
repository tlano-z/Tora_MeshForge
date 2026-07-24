from tora_meshforge.estimation import (
    build_recommendation,
    estimate_surface_retopology_seconds,
    estimate_triangle_sweep_seconds,
    recommend_target_triangles,
    recommend_texture_resolution,
)


def test_mesh_above_practical_target_is_capped() -> None:
    assert recommend_target_triangles(80_000) == 50_000


def test_already_light_mesh_is_not_increased() -> None:
    assert recommend_target_triangles(8_000) == 8_000


def test_dense_mesh_gets_bounded_target() -> None:
    assert recommend_target_triangles(8_000_000) == 50_000


def test_texture_recommendation_honors_cap() -> None:
    selected, reason = recommend_texture_resolution(4096, 8, 8, 4096)
    assert selected == 4096
    assert "capped" in reason


def test_full_recommendation_has_ordered_estimate() -> None:
    report = {
        "source": {"file_size_bytes": 100_000_000},
        "geometry": {"triangles": 3_000_000, "objects": 3, "materials": 2},
        "textures": {"maximum_dimension": 2048, "count": 1},
    }
    result = build_recommendation(report)
    assert result.target_triangles == 50_000
    assert result.maximum_runtime_triangles == 100_000
    assert result.lightweight_target_triangles == 10_000
    assert result.estimate_minimum_seconds <= result.estimate_maximum_seconds
    assert result.temporary_disk_bytes > 300_000_000


def test_workflow_estimates_warn_that_uv_search_can_take_minutes() -> None:
    single_minimum, single_maximum = estimate_surface_retopology_seconds(50_000)
    sweep_minimum, sweep_maximum = estimate_triangle_sweep_seconds(
        (50_000, 25_000, 10_000, 5_000)
    )

    assert single_minimum >= 120
    assert single_maximum >= 10 * 60
    assert sweep_minimum > single_minimum
    assert sweep_maximum > single_maximum


def test_higher_texture_resolution_increases_workflow_estimate() -> None:
    estimate_2k = estimate_surface_retopology_seconds(10_000, 2048)
    estimate_4k = estimate_surface_retopology_seconds(10_000, 4096)

    assert estimate_4k[0] > estimate_2k[0]
    assert estimate_4k[1] > estimate_2k[1]
