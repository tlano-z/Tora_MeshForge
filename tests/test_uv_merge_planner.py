from tora_meshforge.blender.uv_merge_planner import adaptive_region_samples, select_disjoint_candidates


def test_adaptive_region_samples_scale_down_with_face_count() -> None:
    assert adaptive_region_samples(50_000, 192) == [96, 128, 160, 192]
    assert adaptive_region_samples(25_000, 192) == [48, 64, 96, 128, 160, 192]
    assert adaptive_region_samples(10_000, 192)[:4] == [16, 24, 32, 48]
    assert adaptive_region_samples(5_000, 192)[:4] == [16, 24, 32, 48]


def test_adaptive_region_samples_never_exceed_mesh_faces() -> None:
    assert adaptive_region_samples(12, 192, minimum_regions=2) == [12]


def test_select_disjoint_candidates_preserves_rank_and_avoids_conflicts() -> None:
    candidates = [
        {"first": 1, "second": 2, "score": 9.0},
        {"first": 2, "second": 3, "score": 8.0},
        {"first": 4, "second": 5, "score": 7.0},
        {"first": 6, "second": 7, "score": 6.0},
    ]

    selected = select_disjoint_candidates(candidates, maximum_count=2)

    assert [(item["first"], item["second"]) for item in selected] == [(1, 2), (4, 5)]


def test_select_disjoint_candidates_can_disable_a_batch() -> None:
    assert select_disjoint_candidates([{"first": 1, "second": 2}], maximum_count=0) == []
