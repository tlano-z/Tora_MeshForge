from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tora_meshforge.gui.app import prepare_windows_dll_search

prepare_windows_dll_search()

from PySide6.QtWidgets import QApplication

from tora_meshforge.gui.main_window import MainWindow


def test_main_ui_exposes_two_one_click_workflows_and_hides_manual_operations() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()

    assert window.quality_sweep_button.text() == "Run Quality Sweep"
    assert window.single_target_button.text() == "Run Single Target Build"
    assert window.quality_sweep_button.isVisibleTo(window) is True
    assert window.single_target_button.isVisibleTo(window) is True
    assert window.manual_group.isVisible() is False
    assert window.findings_group.isVisible() is False

    window.manual_button.setChecked(True)
    assert window.manual_group.isVisibleTo(window) is True
    window.findings_button.setChecked(True)
    assert window.findings_group.isVisibleTo(window) is True
    window.close()
    app.processEvents()


def test_language_switches_tooltips_and_monitor_without_adding_step_prose() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()

    japanese_index = window.guidance_language_combo.findData("ja")
    window.guidance_language_combo.setCurrentIndex(japanese_index)
    window._update_guidance()
    assert "複数の三角形数" in window.quality_sweep_button.toolTip()
    assert "指定した1つの三角形数" in window.target_spin.toolTip()
    assert window.workflow_value_label.text() == "未実行"

    english_index = window.guidance_language_combo.findData("en")
    window.guidance_language_combo.setCurrentIndex(english_index)
    window._update_guidance()
    assert "all triangle candidates" in window.quality_sweep_button.toolTip()
    assert "one target" in window.target_spin.toolTip()
    assert window.workflow_value_label.text() == "Not started"
    window.close()
    app.processEvents()


def test_monitor_tracks_inspection_processing_and_review() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    japanese_index = window.guidance_language_combo.findData("ja")
    window.guidance_language_combo.setCurrentIndex(japanese_index)

    window._prepare_workflow_monitor(
        "quality_search", "triangle_sweep", (15 * 60, 45 * 60)
    )
    assert window.monitor_inspection_label.text() == "実行中"
    assert window.monitor_processing_label.text() == "待機"
    assert "15 min" in window.estimate_total_label.text()
    assert "残り目安" in window.timing_label.text()

    window._update_progress({"progress": 0.75, "message": "candidate 2"})
    assert window.monitor_inspection_label.text() == "完了"
    assert window.monitor_processing_label.text() == "実行中"
    assert window.stage_label.text() == "candidate 2"
    window.close()
    app.processEvents()


def test_result_paths_are_shown_as_links_next_to_monitor(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    output = tmp_path / "result.fbx"
    output.write_bytes(b"fbx")
    evaluation = tmp_path / "result.evaluation.html"
    evaluation.write_text("<html></html>", encoding="utf-8")

    window._show_process_result({
        "operation": "surface_retopology",
        "source": {"triangles": 100_000},
        "output": {"triangles": 10_000, "path": str(output)},
        "validation": {"checks": {}},
        "material": {"normal_map": True},
        "artifacts": {"final_evaluation": str(evaluation)},
        "warnings": [],
    })

    links = window.artifact_links_label.text()
    assert "Result HTML:" in links
    assert evaluation.resolve().as_uri() in links
    assert "Model: result.fbx" in links
    assert "Folder:" in links
    assert str(tmp_path.resolve()) in links
    assert tmp_path.resolve().as_uri() in links
    assert output.resolve().as_uri() not in links
    window.close()
    app.processEvents()


def test_workflow_rows_show_multi_minute_estimates() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    japanese_index = window.guidance_language_combo.findData("ja")
    window.guidance_language_combo.setCurrentIndex(japanese_index)
    window.target_spin.setValue(50_000)
    window._update_estimate_previews()

    assert "目安" in window.quality_estimate_label.text()
    assert "min" in window.quality_estimate_label.text()
    assert "目安" in window.single_estimate_label.text()
    assert "min" in window.single_estimate_label.text()
    window.close()
    app.processEvents()


def test_cancel_button_is_one_shot_while_workflow_stops() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()

    class _Pipeline:
        def __init__(self) -> None:
            self.cancel_calls = 0

        def cancel(self) -> None:
            self.cancel_calls += 1

    pipeline = _Pipeline()
    window.pipeline = pipeline  # type: ignore[assignment]
    window.cancel_button.setEnabled(True)

    window._cancel()

    assert pipeline.cancel_calls == 1
    assert window.cancel_button.isEnabled() is False
    assert window.stage_label.text() in {"キャンセルしています…", "Cancelling…"}
    window.close()
    app.processEvents()
