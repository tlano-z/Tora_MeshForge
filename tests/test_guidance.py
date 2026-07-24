from tora_meshforge.gui.guidance import (
    normalize_language,
    review_checklist,
    text,
    workflow_name,
    workflow_tooltip,
)


def test_guidance_defaults_to_japanese_and_switches_to_english() -> None:
    assert normalize_language(None) == "ja"
    assert "モデルファイル" in text("ja", "invalid_input")
    assert "model file" in text("en", "invalid_input")


def test_two_normal_workflows_have_bilingual_compact_text() -> None:
    for workflow in ("quality_search", "direct_retopology"):
        assert workflow_name("ja", workflow)
        assert workflow_name("en", workflow)
        assert workflow_tooltip("ja", workflow)
        assert workflow_tooltip("en", workflow)


def test_post_run_checklists_are_compact_and_cover_primary_operations() -> None:
    for operation in (
        "inspection",
        "static_fbx_round_trip",
        "fast_optimize",
        "runtime_rebuild",
        "surface_retopology",
        "triangle_sweep",
    ):
        assert len(review_checklist("ja", operation)) < 80
        assert len(review_checklist("en", operation)) < 80


def test_monitor_states_are_localized() -> None:
    assert text("ja", "status_running") == "実行中"
    assert text("en", "status_done") == "Done"
