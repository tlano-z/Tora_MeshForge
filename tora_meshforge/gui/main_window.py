from __future__ import annotations

from html import escape as html_escape
from pathlib import Path
import time
import traceback
from typing import Any

from PySide6.QtCore import QObject, QSettings, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from tora_meshforge import __version__
from tora_meshforge.config import AppConfig
from tora_meshforge.estimation import (
    estimate_surface_retopology_seconds,
    estimate_triangle_sweep_seconds,
)
from tora_meshforge.gui.guidance import (
    review_checklist,
    text as guidance_text,
    workflow_name,
    workflow_tooltip,
)
from tora_meshforge.models import (
    FastOptimizeRequest,
    InspectionRequest,
    RoundTripRequest,
    RuntimeRebuildRequest,
    SurfaceRetopologyRequest,
    TriangleSweepRequest,
)
from tora_meshforge.pipeline import Pipeline
from tora_meshforge.sweep import DEFAULT_TRIANGLE_SWEEP_TARGETS, parse_triangle_targets
from tora_meshforge.utils.cancellation import CancelledError, CancellationToken


class InspectionWorker(QObject):
    progress = Signal(dict)
    log = Signal(str)
    succeeded = Signal(dict)
    cancelled = Signal()
    failed = Signal(str)
    finished = Signal()

    def __init__(self, pipeline: Pipeline, request: InspectionRequest) -> None:
        super().__init__()
        self.pipeline = pipeline
        self.request = request
        self.cancellation_token: CancellationToken = pipeline.prepare_operation()

    @Slot()
    def run(self) -> None:
        try:
            result = self.pipeline.inspect(
                self.request,
                on_log=self.log.emit,
                on_progress=self.progress.emit,
                _cancellation_token=self.cancellation_token,
            )
            self.succeeded.emit(result.report)
        except CancelledError:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()}")
        finally:
            self.finished.emit()


class RoundTripWorker(QObject):
    progress = Signal(dict)
    log = Signal(str)
    succeeded = Signal(dict)
    cancelled = Signal()
    failed = Signal(str)
    finished = Signal()

    def __init__(self, pipeline: Pipeline, request: RoundTripRequest) -> None:
        super().__init__()
        self.pipeline = pipeline
        self.request = request
        self.cancellation_token: CancellationToken = pipeline.prepare_operation()

    @Slot()
    def run(self) -> None:
        try:
            result = self.pipeline.run(
                self.request,
                on_log=self.log.emit,
                on_progress=self.progress.emit,
                _cancellation_token=self.cancellation_token,
            )
            self.succeeded.emit(result.report)
        except CancelledError:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()}")
        finally:
            self.finished.emit()


class FastOptimizeWorker(QObject):
    progress = Signal(dict)
    log = Signal(str)
    succeeded = Signal(dict)
    cancelled = Signal()
    failed = Signal(str)
    finished = Signal()

    def __init__(self, pipeline: Pipeline, request: FastOptimizeRequest) -> None:
        super().__init__()
        self.pipeline = pipeline
        self.request = request
        self.cancellation_token: CancellationToken = pipeline.prepare_operation()

    @Slot()
    def run(self) -> None:
        try:
            result = self.pipeline.optimize(
                self.request,
                on_log=self.log.emit,
                on_progress=self.progress.emit,
                _cancellation_token=self.cancellation_token,
            )
            self.succeeded.emit(result.report)
        except CancelledError:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()}")
        finally:
            self.finished.emit()


class RuntimeRebuildWorker(QObject):
    progress = Signal(dict)
    log = Signal(str)
    succeeded = Signal(dict)
    cancelled = Signal()
    failed = Signal(str)
    finished = Signal()

    def __init__(self, pipeline: Pipeline, request: RuntimeRebuildRequest) -> None:
        super().__init__()
        self.pipeline = pipeline
        self.request = request
        self.cancellation_token: CancellationToken = pipeline.prepare_operation()

    @Slot()
    def run(self) -> None:
        try:
            result = self.pipeline.runtime_rebuild(
                self.request,
                on_log=self.log.emit,
                on_progress=self.progress.emit,
                _cancellation_token=self.cancellation_token,
            )
            self.succeeded.emit(result.report)
        except CancelledError:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()}")
        finally:
            self.finished.emit()


class SurfaceRetopologyWorker(QObject):
    progress = Signal(dict)
    log = Signal(str)
    succeeded = Signal(dict)
    cancelled = Signal()
    failed = Signal(str)
    finished = Signal()

    def __init__(self, pipeline: Pipeline, request: SurfaceRetopologyRequest) -> None:
        super().__init__()
        self.pipeline = pipeline
        self.request = request
        self.cancellation_token: CancellationToken = pipeline.prepare_operation()

    @Slot()
    def run(self) -> None:
        try:
            result = self.pipeline.surface_retopology(
                self.request,
                on_log=self.log.emit,
                on_progress=self.progress.emit,
                _cancellation_token=self.cancellation_token,
            )
            self.succeeded.emit(result.report)
        except CancelledError:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()}")
        finally:
            self.finished.emit()


class TriangleSweepWorker(QObject):
    progress = Signal(dict)
    log = Signal(str)
    succeeded = Signal(dict)
    cancelled = Signal()
    failed = Signal(str)
    finished = Signal()

    def __init__(self, pipeline: Pipeline, request: TriangleSweepRequest) -> None:
        super().__init__()
        self.pipeline = pipeline
        self.request = request
        self.cancellation_token: CancellationToken = pipeline.prepare_operation()

    @Slot()
    def run(self) -> None:
        try:
            result = self.pipeline.triangle_sweep(
                self.request,
                on_log=self.log.emit,
                on_progress=self.progress.emit,
                _cancellation_token=self.cancellation_token,
            )
            self.succeeded.emit(result.report)
        except CancelledError:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()}")
        finally:
            self.finished.emit()


class GuidedWorkflowWorker(QObject):
    progress = Signal(dict)
    log = Signal(str)
    succeeded = Signal(dict)
    cancelled = Signal()
    failed = Signal(str)
    finished = Signal()

    def __init__(
        self,
        pipeline: Pipeline,
        workflow: str,
        inspection_request: InspectionRequest,
        operation: str,
        processing_request: (
            FastOptimizeRequest
            | RuntimeRebuildRequest
            | SurfaceRetopologyRequest
            | TriangleSweepRequest
        ),
        phase_labels: tuple[str, str],
    ) -> None:
        super().__init__()
        self.pipeline = pipeline
        self.workflow = workflow
        self.inspection_request = inspection_request
        self.operation = operation
        self.processing_request = processing_request
        self.phase_labels = phase_labels
        self.cancellation_token: CancellationToken = pipeline.prepare_operation()

    def _phase_progress(self, phase: int, event: dict[str, Any]) -> None:
        mapped = dict(event)
        value = max(0.0, min(1.0, float(event.get("progress", 0.0))))
        mapped["progress"] = (phase + value) / 2.0
        message = str(event.get("message", event.get("stage", "Working")))
        mapped["message"] = f"{self.phase_labels[phase]} — {message}"
        self.progress.emit(mapped)

    @Slot()
    def run(self) -> None:
        try:
            self.log.emit(f"=== {self.phase_labels[0]} ===")
            inspection = self.pipeline.inspect(
                self.inspection_request,
                on_log=self.log.emit,
                on_progress=lambda event: self._phase_progress(0, event),
                _cancellation_token=self.cancellation_token,
            )
            self.log.emit(f"=== {self.phase_labels[1]} ===")
            if self.operation == "fast_optimize":
                result = self.pipeline.optimize(
                    self.processing_request,
                    on_log=self.log.emit,
                    on_progress=lambda event: self._phase_progress(1, event),
                    _cancellation_token=self.cancellation_token,
                )
            elif self.operation == "runtime_rebuild":
                result = self.pipeline.runtime_rebuild(
                    self.processing_request,
                    on_log=self.log.emit,
                    on_progress=lambda event: self._phase_progress(1, event),
                    _cancellation_token=self.cancellation_token,
                )
            elif self.operation == "surface_retopology":
                result = self.pipeline.surface_retopology(
                    self.processing_request,
                    on_log=self.log.emit,
                    on_progress=lambda event: self._phase_progress(1, event),
                    _cancellation_token=self.cancellation_token,
                )
            elif self.operation == "triangle_sweep":
                result = self.pipeline.triangle_sweep(
                    self.processing_request,
                    on_log=self.log.emit,
                    on_progress=lambda event: self._phase_progress(1, event),
                    _cancellation_token=self.cancellation_token,
                )
            else:
                raise ValueError(f"Unsupported guided workflow operation: {self.operation}")
            self.succeeded.emit(
                {
                    "workflow": self.workflow,
                    "operation": self.operation,
                    "inspection": inspection.report,
                    "processing": result.report,
                }
            )
        except CancelledError:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()}")
        finally:
            self.finished.emit()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"Tora_MeshForge {__version__}")
        self.resize(1040, 900)
        self.settings = QSettings()
        self.pipeline: Pipeline | None = None
        self.thread: QThread | None = None
        self.worker: QObject | None = None
        self.last_review_operation: str | None = None
        self.last_completed_workflow: str | None = None
        self.active_guided_workflow: str | None = None
        self.monitor_workflow: str | None = None
        self.monitor_operation: str | None = None
        self.monitor_states = {
            "inspection": "waiting",
            "processing": "waiting",
            "review": "waiting",
        }
        self.workflow_estimate_seconds: tuple[int, int] | None = None
        self.workflow_started_at: float | None = None
        self.workflow_finished_elapsed: int | None = None
        self.current_progress = 0.0
        self.elapsed_timer = QTimer(self)
        self.elapsed_timer.setInterval(1_000)
        self.elapsed_timer.timeout.connect(self._update_time_status)
        self._build_ui()
        self._restore_settings()

    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        input_group = QGroupBox("Model and output")
        grid = QGridLayout(input_group)
        self.input_edit = QLineEdit()
        self.report_edit = QLineEdit()
        self.output_edit = QLineEdit()
        self.process_report_edit = QLineEdit()
        self.texture_edit = QLineEdit()
        self.blender_edit = QLineEdit()
        self.work_edit = QLineEdit(str((Path.cwd() / "work").resolve()))
        basic_fields = [
            ("Input model", self.input_edit, self._browse_input),
            ("Texture override (optional)", self.texture_edit, self._browse_texture),
            ("Output FBX", self.output_edit, self._browse_output),
        ]
        for row, (label, edit, callback) in enumerate(basic_fields):
            grid.addWidget(QLabel(label), row, 0)
            grid.addWidget(edit, row, 1)
            button = QPushButton("Browse…")
            button.clicked.connect(callback)
            grid.addWidget(button, row, 2)
        root.addWidget(input_group)

        self.guidance_language_combo = QComboBox()
        self.guidance_language_combo.addItem("日本語", "ja")
        self.guidance_language_combo.addItem("English", "en")
        self.guidance_language_combo.currentIndexChanged.connect(self._update_guidance)

        workflow_group = QGroupBox("Workflows")
        workflow_layout = QGridLayout(workflow_group)
        self.target_preset = QComboBox()
        self.target_preset.addItem("Practical — 50,000", 50_000)
        self.target_preset.addItem("Upper ceiling — 100,000", 100_000)
        self.target_preset.addItem("Lightweight — 10,000", 10_000)
        self.target_preset.addItem("Custom", None)
        self.target_preset.currentIndexChanged.connect(self._apply_target_preset)
        self.target_spin = QSpinBox()
        self.target_spin.setRange(1_000, 10_000_000)
        self.target_spin.setSingleStep(5_000)
        self.target_spin.setValue(50_000)
        self.target_spin.setGroupSeparatorShown(True)
        self.target_spin.setToolTip(
            "Used by Single Target Build. Quality Sweep uses its candidate list."
        )
        self.target_spin.valueChanged.connect(self._sync_target_preset)
        self.preserve_small_parts_check = QCheckBox("Protect small mesh objects")
        self.preserve_small_parts_check.setChecked(True)
        self.shape_normal_check = QCheckBox("Bake shape-difference Normal")
        self.shape_normal_check.setChecked(True)
        self.shape_normal_check.setToolTip(
            "For Surface Retopology and Triangle Sweep, bake detail lost from the dense source "
            "into a tangent-space Normal map and connect it to the output material."
        )
        self.texture_resolution_combo = QComboBox()
        self.texture_resolution_combo.addItem("Auto — source-aware, max 4096", ("auto", None))
        self.texture_resolution_combo.addItem("Match source — max 4096", ("match-source", None))
        for resolution in (512, 1024, 2048, 4096):
            self.texture_resolution_combo.addItem(f"Manual — {resolution} × {resolution}", ("manual", resolution))
        self.uv_mode_combo = QComboBox()
        self.uv_mode_combo.addItem("Consolidated + boundary weld — recommended", "consolidated")
        self.uv_mode_combo.addItem("Angle Based — safest fallback", "angle")
        self.uv_mode_combo.addItem("Smart Project — may fragment open meshes", "smart")
        self.uv_mode_combo.setToolTip(
            "Consolidated welds only validated near-coincident open boundaries in the output mesh; "
            "the source file is never modified."
        )
        self.uv_margin_spin = QSpinBox()
        self.uv_margin_spin.setRange(0, 128)
        self.uv_margin_spin.setValue(4)
        self.uv_margin_spin.setSuffix(" px")
        self.sweep_targets_edit = QLineEdit(
            ", ".join(str(value) for value in DEFAULT_TRIANGLE_SWEEP_TARGETS)
        )
        self.sweep_targets_edit.setPlaceholderText("50000, 25000, 10000, 5000")
        self.sweep_targets_edit.setToolTip(
            "Comma-, semicolon-, or space-separated triangle targets. Suffixes such as 50k are accepted."
        )
        sweep_target_row = QWidget()
        sweep_target_layout = QHBoxLayout(sweep_target_row)
        sweep_target_layout.setContentsMargins(0, 0, 0, 0)
        sweep_target_layout.addWidget(self.sweep_targets_edit, 1)
        self.sweep_preset_button = QPushButton("Preset: 50k / 25k / 10k / 5k")
        self.sweep_preset_button.clicked.connect(self._apply_sweep_preset)
        sweep_target_layout.addWidget(self.sweep_preset_button)

        target_row = QWidget()
        target_layout = QHBoxLayout(target_row)
        target_layout.setContentsMargins(0, 0, 0, 0)
        target_layout.addWidget(self.target_preset)
        target_layout.addWidget(self.target_spin)
        self.quality_sweep_button = QPushButton("Run Quality Sweep")
        self.quality_sweep_button.setDefault(True)
        self.quality_sweep_button.clicked.connect(self._start_quality_sweep_workflow)
        self.single_target_button = QPushButton("Run Single Target Build")
        self.single_target_button.clicked.connect(self._start_single_target_workflow)
        self.quality_estimate_label = QLabel()
        self.single_estimate_label = QLabel()
        workflow_layout.addWidget(QLabel("Guidance language"), 0, 2)
        workflow_layout.addWidget(self.guidance_language_combo, 0, 4)
        workflow_layout.addWidget(QLabel("Quality Sweep"), 1, 0)
        workflow_layout.addWidget(sweep_target_row, 1, 1, 1, 2)
        workflow_layout.addWidget(self.quality_estimate_label, 1, 3)
        workflow_layout.addWidget(self.quality_sweep_button, 1, 4)
        workflow_layout.addWidget(QLabel("Single Target Build"), 2, 0)
        workflow_layout.addWidget(target_row, 2, 1, 1, 2)
        workflow_layout.addWidget(self.single_estimate_label, 2, 3)
        workflow_layout.addWidget(self.single_target_button, 2, 4)
        workflow_layout.setColumnStretch(1, 1)
        root.addWidget(workflow_group)

        shared_group = QGroupBox("Shared output settings")
        shared_layout = QFormLayout(shared_group)
        shared_layout.addRow("Surface detail", self.shape_normal_check)
        shared_layout.addRow("Rebuild texture", self.texture_resolution_combo)
        shared_layout.addRow("UV island margin", self.uv_margin_spin)
        root.addWidget(shared_group)

        monitor_group = QGroupBox("Workflow monitor")
        monitor_layout = QGridLayout(monitor_group)
        self.workflow_value_label = QLabel()
        self.monitor_inspection_label = QLabel()
        self.monitor_processing_title = QLabel()
        self.monitor_processing_label = QLabel()
        self.monitor_review_label = QLabel()
        self.review_label = QLabel()
        self.review_label.setWordWrap(True)
        self.estimate_total_label = QLabel("—")
        self.timing_label = QLabel()
        self.artifact_links_label = QLabel("—")
        self.artifact_links_label.setWordWrap(True)
        self.artifact_links_label.setOpenExternalLinks(True)
        self.artifact_links_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction
        )
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1000)
        self.stage_label = QLabel("Ready")
        self.stage_label.setWordWrap(True)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel)
        monitor_layout.addWidget(QLabel("Workflow"), 0, 0)
        monitor_layout.addWidget(self.workflow_value_label, 0, 1, 1, 3)
        monitor_layout.addWidget(QLabel("1. Inspect source"), 1, 0)
        monitor_layout.addWidget(self.monitor_inspection_label, 1, 1)
        monitor_layout.addWidget(self.monitor_processing_title, 1, 2)
        monitor_layout.addWidget(self.monitor_processing_label, 1, 3)
        monitor_layout.addWidget(QLabel("3. Review"), 2, 0)
        monitor_layout.addWidget(self.monitor_review_label, 2, 1)
        monitor_layout.addWidget(self.review_label, 2, 2, 1, 2)
        monitor_layout.addWidget(QLabel("Estimated total"), 3, 0)
        monitor_layout.addWidget(self.estimate_total_label, 3, 1)
        monitor_layout.addWidget(self.timing_label, 3, 2, 1, 2)
        monitor_layout.addWidget(self.progress_bar, 4, 0, 1, 2)
        monitor_layout.addWidget(self.stage_label, 4, 2)
        monitor_layout.addWidget(self.cancel_button, 4, 3)
        monitor_layout.addWidget(QLabel("Results"), 5, 0)
        monitor_layout.addWidget(self.artifact_links_label, 5, 1, 1, 3)
        root.addWidget(monitor_group)

        self.target_spin.valueChanged.connect(self._update_estimate_previews)
        self.sweep_targets_edit.textChanged.connect(self._update_estimate_previews)
        self.texture_resolution_combo.currentIndexChanged.connect(
            self._update_estimate_previews
        )

        toggles = QHBoxLayout()
        self.manual_button = QPushButton("Show manual operations")
        self.manual_button.setCheckable(True)
        self.manual_button.toggled.connect(self._toggle_manual)
        toggles.addWidget(self.manual_button)
        self.advanced_button = QPushButton("Show advanced paths")
        self.advanced_button.setCheckable(True)
        self.advanced_button.toggled.connect(self._toggle_advanced)
        toggles.addWidget(self.advanced_button)
        self.findings_button = QPushButton("Show inspection findings")
        self.findings_button.setCheckable(True)
        self.findings_button.toggled.connect(self._toggle_findings)
        toggles.addWidget(self.findings_button)
        toggles.addStretch(1)
        root.addLayout(toggles)

        self.advanced_group = QGroupBox("Advanced paths — normally filled automatically")
        advanced_grid = QGridLayout(self.advanced_group)
        advanced_fields = [
            ("Inspection report JSON", self.report_edit, self._browse_report),
            ("Process report JSON", self.process_report_edit, self._browse_process_report),
            ("Blender executable", self.blender_edit, self._browse_blender),
            ("Work directory", self.work_edit, self._browse_work),
        ]
        for row, (label, edit, callback) in enumerate(advanced_fields):
            advanced_grid.addWidget(QLabel(label), row, 0)
            advanced_grid.addWidget(edit, row, 1)
            button = QPushButton("Browse…")
            button.clicked.connect(callback)
            advanced_grid.addWidget(button, row, 2)
        self.advanced_group.setVisible(False)
        root.addWidget(self.advanced_group)

        self.manual_group = QGroupBox("Manual operations — inspect individual results")
        controls = QGridLayout(self.manual_group)
        controls.addWidget(QLabel("Fast Optimize part policy"), 0, 0)
        controls.addWidget(self.preserve_small_parts_check, 0, 1, 1, 2)
        controls.addWidget(QLabel("Runtime Rebuild UV"), 1, 0)
        controls.addWidget(self.uv_mode_combo, 1, 1, 1, 2)
        self.inspect_button = QPushButton("Inspect")
        self.inspect_button.clicked.connect(self._start_inspection)
        self.process_button = QPushButton("Static FBX Round Trip")
        self.process_button.clicked.connect(self._start_roundtrip)
        self.optimize_button = QPushButton("Fast Optimize")
        self.optimize_button.clicked.connect(self._start_fast_optimize)
        self.rebuild_button = QPushButton("Runtime Rebuild")
        self.rebuild_button.clicked.connect(self._start_runtime_rebuild)
        self.surface_retopology_button = QPushButton("Surface Retopology")
        self.surface_retopology_button.setToolTip(
            "Recommended for dense fragmented static models. Rebuilds a continuous surface, creates region UVs, "
            "merges islands toward one until quality limits are reached, and transfers Base Color plus shape Normal; "
            "topology changes substantially and UV search can take several minutes."
        )
        self.surface_retopology_button.clicked.connect(self._start_surface_retopology)
        self.triangle_sweep_button = QPushButton("Triangle Sweep")
        self.triangle_sweep_button.setToolTip(
            "Runs Surface Retopology for every triangle candidate, measures surface, Base Color, and UV quality, "
            "and creates matched SOURCE/candidate hero, side, and back previews including Base Color + Normal."
        )
        self.triangle_sweep_button.clicked.connect(self._start_triangle_sweep)
        controls.addWidget(self.inspect_button, 2, 0)
        controls.addWidget(self.process_button, 2, 1)
        controls.addWidget(self.optimize_button, 2, 2)
        controls.addWidget(self.rebuild_button, 3, 0)
        controls.addWidget(self.surface_retopology_button, 3, 1)
        controls.addWidget(self.triangle_sweep_button, 3, 2)
        self.manual_group.setVisible(False)
        root.addWidget(self.manual_group)

        self.findings_group = QGroupBox("Inspection findings")
        form = QFormLayout(self.findings_group)
        self.result_labels: dict[str, QLabel] = {}
        for key, label in [
            ("objects", "Objects / meshes"),
            ("geometry", "Vertices / triangles"),
            ("materials", "Materials / textures"),
            ("texture", "Maximum texture size"),
            ("features", "Rig / animation / shape keys"),
            ("device", "Bake devices"),
            ("target", "Recommended triangle target"),
            ("resolution", "Recommended texture resolution"),
            ("estimate", "Estimated inspection time"),
            ("warnings", "Warnings"),
            ("sweep_candidates", "Sweep final candidates"),
            ("roundtrip", "Processing result"),
        ]:
            value = QLabel("—")
            value.setWordWrap(True)
            if key == "sweep_candidates":
                value.setOpenExternalLinks(True)
            self.result_labels[key] = value
            form.addRow(label, value)
        self.findings_group.setVisible(False)
        root.addWidget(self.findings_group)
        self.log_edit = QPlainTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setPlaceholderText("Blender output and inspection diagnostics")
        self.log_edit.setMinimumHeight(150)
        root.addWidget(self.log_edit, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(central)
        self.setCentralWidget(scroll)

    def _restore_settings(self) -> None:
        for name, widget in [("input", self.input_edit), ("texture", self.texture_edit), ("report", self.report_edit), ("output", self.output_edit), ("process_report", self.process_report_edit), ("blender", self.blender_edit), ("work", self.work_edit)]:
            value = self.settings.value(name, "")
            if value:
                widget.setText(str(value))
        saved_target = int(self.settings.value("target_triangles", 50_000))
        self.target_spin.setValue(saved_target)
        matching = self.target_preset.findData(saved_target)
        self.target_preset.setCurrentIndex(matching if matching >= 0 else self.target_preset.count() - 1)
        self.preserve_small_parts_check.setChecked(
            str(self.settings.value("preserve_small_parts", "true")).lower() == "true"
        )
        self.shape_normal_check.setChecked(
            str(self.settings.value("bake_shape_normal", "true")).lower() == "true"
        )
        resolution_index = int(self.settings.value("runtime_texture_index", 0))
        self.texture_resolution_combo.setCurrentIndex(max(0, min(resolution_index, self.texture_resolution_combo.count() - 1)))
        uv_mode = str(self.settings.value("runtime_uv_mode", "consolidated"))
        uv_index = self.uv_mode_combo.findData(uv_mode)
        self.uv_mode_combo.setCurrentIndex(max(0, uv_index))
        self.uv_margin_spin.setValue(int(self.settings.value("runtime_uv_margin", 4)))
        self.sweep_targets_edit.setText(str(self.settings.value(
            "triangle_sweep_targets",
            ", ".join(str(value) for value in DEFAULT_TRIANGLE_SWEEP_TARGETS),
        )))
        language = str(self.settings.value("guidance_language", "ja"))
        language_index = self.guidance_language_combo.findData(language)
        self.guidance_language_combo.setCurrentIndex(max(0, language_index))
        self._update_guidance()

    def _save_settings(self) -> None:
        for name, widget in [("input", self.input_edit), ("texture", self.texture_edit), ("report", self.report_edit), ("output", self.output_edit), ("process_report", self.process_report_edit), ("blender", self.blender_edit), ("work", self.work_edit)]:
            self.settings.setValue(name, widget.text())
        self.settings.setValue("target_triangles", self.target_spin.value())
        self.settings.setValue("preserve_small_parts", self.preserve_small_parts_check.isChecked())
        self.settings.setValue("bake_shape_normal", self.shape_normal_check.isChecked())
        self.settings.setValue("runtime_texture_index", self.texture_resolution_combo.currentIndex())
        self.settings.setValue("runtime_uv_mode", self.uv_mode_combo.currentData())
        self.settings.setValue("runtime_uv_margin", self.uv_margin_spin.value())
        self.settings.setValue("triangle_sweep_targets", self.sweep_targets_edit.text())
        self.settings.setValue("guidance_language", self._guidance_language())

    def _guidance_language(self) -> str:
        return str(self.guidance_language_combo.currentData() or "ja")

    def _text(self, key: str, **values: Any) -> str:
        return guidance_text(self._guidance_language(), key, **values)

    @Slot(int)
    def _update_guidance(self, _index: int = -1) -> None:
        language = self._guidance_language()
        self.quality_sweep_button.setToolTip(workflow_tooltip(language, "quality_search"))
        self.single_target_button.setToolTip(
            workflow_tooltip(language, "direct_retopology")
        )
        self.sweep_targets_edit.setToolTip(
            workflow_tooltip(language, "quality_search")
        )
        self.target_spin.setToolTip(
            workflow_tooltip(language, "direct_retopology")
        )
        self._update_estimate_previews()
        self._render_monitor()
        self._update_time_status()
        self._save_settings()

    def _set_review_guidance(self, operation: str | None, workflow: str | None = None) -> None:
        self.last_review_operation = operation
        self.last_completed_workflow = workflow
        self._render_monitor()

    def _render_monitor(self) -> None:
        language = self._guidance_language()
        if self.monitor_workflow in ("quality_search", "direct_retopology"):
            workflow_label = workflow_name(language, self.monitor_workflow)
        elif self.monitor_workflow == "manual":
            workflow_label = self._text("monitor_manual")
        else:
            workflow_label = self._text("monitor_not_started")
        self.workflow_value_label.setText(workflow_label)
        processing_key = {
            "triangle_sweep": "monitor_sweep",
            "surface_retopology": "monitor_single",
        }.get(self.monitor_operation, "monitor_manual_process")
        self.monitor_processing_title.setText(f"2. {self._text(processing_key)}")
        for phase, label in (
            ("inspection", self.monitor_inspection_label),
            ("processing", self.monitor_processing_label),
            ("review", self.monitor_review_label),
        ):
            state = self.monitor_states[phase]
            label.setText(self._text(f"status_{state}"))
            color = {
                "waiting": "palette(mid)",
                "running": "#2f80ed",
                "done": "#27864a",
                "failed": "#c0392b",
                "cancelled": "#a65f00",
            }[state]
            label.setStyleSheet(f"QLabel {{ color: {color}; font-weight: 600; }}")
        self.review_label.setText(
            review_checklist(language, self.last_review_operation)
        )

    def _surface_resolution(self) -> int:
        resolution_mode, manual_resolution = self.texture_resolution_combo.currentData()
        return (
            int(manual_resolution)
            if resolution_mode == "manual" and manual_resolution
            else 2048
        )

    @staticmethod
    def _format_duration(seconds: int) -> str:
        value = max(0, int(seconds))
        if value < 60:
            return f"{value} sec"
        minutes = max(1, round(value / 60))
        if minutes < 60:
            return f"{minutes} min"
        hours, remaining_minutes = divmod(minutes, 60)
        return f"{hours} h {remaining_minutes} min" if remaining_minutes else f"{hours} h"

    def _format_duration_range(self, estimate: tuple[int, int]) -> str:
        return self._text(
            "duration_range",
            minimum=self._format_duration(estimate[0]),
            maximum=self._format_duration(estimate[1]),
        )

    @Slot()
    def _update_estimate_previews(self, *_args: Any) -> None:
        resolution = self._surface_resolution()
        single_estimate = estimate_surface_retopology_seconds(
            self.target_spin.value(), resolution
        )
        self.single_estimate_label.setText(
            self._text(
                "duration_approx",
                duration=self._format_duration_range(single_estimate),
            )
        )
        try:
            targets = parse_triangle_targets(self.sweep_targets_edit.text())
            sweep_estimate = estimate_triangle_sweep_seconds(targets, resolution)
            sweep_text = self._format_duration_range(sweep_estimate)
        except ValueError:
            sweep_text = "—"
        self.quality_estimate_label.setText(
            self._text("duration_approx", duration=sweep_text)
        )

    @Slot()
    def _update_time_status(self) -> None:
        if self.workflow_estimate_seconds is None:
            self.timing_label.setText(self._text("duration_not_running"))
            return
        if self.workflow_started_at is None:
            if self.workflow_finished_elapsed is None:
                self.timing_label.setText(self._text("duration_not_running"))
                return
            self.timing_label.setText(
                self._text(
                    "duration_finished",
                    elapsed=self._format_duration(self.workflow_finished_elapsed),
                    duration=self._format_duration_range(self.workflow_estimate_seconds),
                )
            )
            return
        elapsed = max(0, int(time.monotonic() - self.workflow_started_at))
        minimum, maximum = self.workflow_estimate_seconds
        if elapsed >= maximum:
            self.timing_label.setText(
                self._text(
                    "duration_elapsed_over",
                    elapsed=self._format_duration(elapsed),
                )
            )
            return
        remaining = (max(0, minimum - elapsed), max(0, maximum - elapsed))
        self.timing_label.setText(
            self._text(
                "duration_elapsed_remaining",
                elapsed=self._format_duration(elapsed),
                remaining=self._format_duration_range(remaining),
            )
        )

    def _prepare_workflow_monitor(
        self,
        workflow: str,
        operation: str,
        estimate: tuple[int, int],
    ) -> None:
        self.active_guided_workflow = workflow
        self.monitor_workflow = workflow
        self.monitor_operation = operation
        self.monitor_states = {
            "inspection": "running",
            "processing": "waiting",
            "review": "waiting",
        }
        self.last_review_operation = None
        self.last_completed_workflow = None
        self.workflow_estimate_seconds = estimate
        self.workflow_started_at = time.monotonic()
        self.workflow_finished_elapsed = None
        self.current_progress = 0.0
        self.estimate_total_label.setText(self._format_duration_range(estimate))
        self.artifact_links_label.setText("—")
        self.elapsed_timer.start()
        self._render_monitor()
        self._update_time_status()

    def _prepare_manual_monitor(self, worker: QObject) -> None:
        self.active_guided_workflow = None
        self.monitor_workflow = "manual"
        worker_operations = {
            InspectionWorker: "inspection",
            RoundTripWorker: "static_fbx_round_trip",
            FastOptimizeWorker: "fast_optimize",
            RuntimeRebuildWorker: "runtime_rebuild",
            SurfaceRetopologyWorker: "surface_retopology",
            TriangleSweepWorker: "triangle_sweep",
        }
        self.monitor_operation = worker_operations.get(type(worker), "manual")
        inspection_only = isinstance(worker, InspectionWorker)
        self.monitor_states = {
            "inspection": "running" if inspection_only else "waiting",
            "processing": "waiting" if inspection_only else "running",
            "review": "waiting",
        }
        self.last_review_operation = None
        self.last_completed_workflow = None
        self.workflow_estimate_seconds = None
        self.workflow_started_at = None
        self.workflow_finished_elapsed = None
        self.current_progress = 0.0
        self.estimate_total_label.setText("—")
        self.artifact_links_label.setText("—")
        self.elapsed_timer.stop()
        self._render_monitor()
        self._update_time_status()

    @staticmethod
    def _path_link(path_value: Any, label: str | None = None) -> str:
        path = Path(str(path_value)).expanduser().resolve()
        uri = path.as_uri()
        display = label or str(path)
        return (
            f'<a href="{html_escape(uri, quote=True)}">'
            f'{html_escape(display)}</a>'
        )

    def _model_location_links(self, path_value: Any, prefix: str = "") -> list[str]:
        path = Path(str(path_value)).expanduser().resolve()
        label_prefix = f"{html_escape(prefix)} " if prefix else ""
        return [
            f"{label_prefix}Model: {html_escape(path.name)}",
            f"{label_prefix}Folder: {self._path_link(path.parent)}",
        ]

    @Slot(bool)
    def _toggle_advanced(self, visible: bool) -> None:
        self.advanced_group.setVisible(visible)
        self.advanced_button.setText("Hide advanced paths" if visible else "Show advanced paths")

    @Slot(bool)
    def _toggle_manual(self, visible: bool) -> None:
        self.manual_group.setVisible(visible)
        self.manual_button.setText(
            "Hide manual operations" if visible else "Show manual operations"
        )

    @Slot(bool)
    def _toggle_findings(self, visible: bool) -> None:
        self.findings_group.setVisible(visible)
        self.findings_button.setText(
            "Hide inspection findings" if visible else "Show inspection findings"
        )

    @Slot(int)
    def _apply_target_preset(self, index: int) -> None:
        value = self.target_preset.itemData(index)
        if value is not None:
            self.target_spin.setValue(int(value))

    @Slot(int)
    def _sync_target_preset(self, value: int) -> None:
        matching = self.target_preset.findData(value)
        custom_index = self.target_preset.count() - 1
        destination = matching if matching >= 0 else custom_index
        if destination >= 0 and destination != self.target_preset.currentIndex():
            self.target_preset.setCurrentIndex(destination)

    @Slot()
    def _apply_sweep_preset(self) -> None:
        self.sweep_targets_edit.setText(
            ", ".join(str(value) for value in DEFAULT_TRIANGLE_SWEEP_TARGETS)
        )

    def _browse_input(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select model", self.input_edit.text(), "3D Models (*.fbx *.glb *.gltf *.obj)")
        if path:
            self.input_edit.setText(path)
            self.report_edit.setText(str(Path(path).with_suffix(".inspection.json")))
            self.output_edit.setText(str(Path(path).with_suffix(".output.fbx")))
            self.process_report_edit.setText(str(Path(path).with_suffix(".process.report.json")))

    def _browse_report(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save inspection report", self.report_edit.text(), "JSON (*.json)")
        if path:
            self.report_edit.setText(path)

    def _browse_output(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save output FBX", self.output_edit.text(), "FBX (*.fbx)")
        if path:
            self.output_edit.setText(path)

    def _browse_process_report(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save process report", self.process_report_edit.text(), "JSON (*.json)")
        if path:
            self.process_report_edit.setText(path)

    def _browse_texture(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select texture override",
            self.texture_edit.text(),
            "Images (*.png *.jpg *.jpeg *.tif *.tiff *.tga *.bmp *.exr *.webp)",
        )
        if path:
            self.texture_edit.setText(path)

    def _browse_blender(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select Blender", self.blender_edit.text(), "Blender (blender.exe)")
        if path:
            self.blender_edit.setText(path)

    def _browse_work(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select work directory", self.work_edit.text())
        if path:
            self.work_edit.setText(path)

    def _validated_input_path(self) -> Path | None:
        input_path = Path(self.input_edit.text().strip())
        if not input_path.is_file():
            QMessageBox.warning(
                self,
                self._text("invalid_input_title"),
                self._text("invalid_input"),
            )
            return None
        return input_path

    def _validated_output_path(self, *, sweep: bool = False) -> Path | None:
        output_text = self.output_edit.text().strip()
        if not output_text:
            QMessageBox.warning(
                self,
                self._text("invalid_output_title"),
                self._text("invalid_sweep_output" if sweep else "invalid_output"),
            )
            return None
        return Path(output_text)

    @Slot()
    def _start_quality_sweep_workflow(self) -> None:
        self._start_guided_workflow("quality_search")

    @Slot()
    def _start_single_target_workflow(self) -> None:
        self._start_guided_workflow("direct_retopology")

    def _start_guided_workflow(self, workflow: str) -> None:
        if workflow not in ("quality_search", "direct_retopology"):
            raise ValueError(f"Unsupported normal workflow: {workflow}")
        input_path = self._validated_input_path()
        if input_path is None:
            return
        output_path = self._validated_output_path(sweep=workflow == "quality_search")
        if output_path is None:
            return
        sweep_targets = DEFAULT_TRIANGLE_SWEEP_TARGETS
        if workflow == "quality_search":
            try:
                sweep_targets = parse_triangle_targets(self.sweep_targets_edit.text())
            except ValueError as exc:
                QMessageBox.warning(
                    self,
                    self._text("invalid_sweep_title"),
                    str(exc),
                )
                return

        self._save_settings()
        work = Path(self.work_edit.text().strip() or "work")
        blender_text = self.blender_edit.text().strip()
        report_text = self.report_edit.text().strip()
        process_report_text = self.process_report_edit.text().strip()
        texture_text = self.texture_edit.text().strip()
        blender_path = Path(blender_text) if blender_text else None
        texture_path = Path(texture_text) if texture_text else None
        process_report_path = Path(process_report_text) if process_report_text else None
        surface_resolution = self._surface_resolution()
        self.pipeline = Pipeline(AppConfig(work_directory=work).resolved(Path.cwd()))
        inspection_request = InspectionRequest(
            input_path=input_path,
            blender_path=blender_path,
            work_directory=work,
            report_path=Path(report_text) if report_text else None,
            texture_path=texture_path,
        )

        if workflow == "quality_search":
            operation = "triangle_sweep"
            workflow_estimate = estimate_triangle_sweep_seconds(
                sweep_targets, surface_resolution
            )
            processing_request: SurfaceRetopologyRequest | TriangleSweepRequest = TriangleSweepRequest(
                input_path=input_path,
                output_directory=output_path.parent / f"{output_path.stem}_sweep",
                triangle_targets=sweep_targets,
                blender_path=blender_path,
                work_directory=work,
                report_path=process_report_path,
                texture_path=texture_path,
                texture_resolution=surface_resolution,
                uv_margin_pixels=self.uv_margin_spin.value(),
                bake_shape_normal=self.shape_normal_check.isChecked(),
            )
        else:
            operation = "surface_retopology"
            workflow_estimate = estimate_surface_retopology_seconds(
                self.target_spin.value(), surface_resolution
            )
            processing_request = SurfaceRetopologyRequest(
                input_path=input_path,
                output_path=output_path,
                target_triangles=self.target_spin.value(),
                blender_path=blender_path,
                work_directory=work,
                report_path=process_report_path,
                texture_path=texture_path,
                texture_resolution=surface_resolution,
                uv_margin_pixels=self.uv_margin_spin.value(),
                bake_shape_normal=self.shape_normal_check.isChecked(),
            )
        self._prepare_workflow_monitor(workflow, operation, workflow_estimate)
        self._start_worker(
            GuidedWorkflowWorker(
                self.pipeline,
                workflow,
                inspection_request,
                operation,
                processing_request,
                (
                    self._text("workflow_inspection_phase"),
                    self._text("workflow_processing_phase"),
                ),
            ),
            self._show_guided_workflow_result,
        )

    def _start_inspection(self) -> None:
        input_path = self._validated_input_path()
        if input_path is None:
            return
        self._save_settings()
        work = Path(self.work_edit.text().strip() or "work")
        blender_text = self.blender_edit.text().strip()
        report_text = self.report_edit.text().strip()
        texture_text = self.texture_edit.text().strip()
        config = AppConfig(work_directory=work).resolved(Path.cwd())
        self.pipeline = Pipeline(config)
        request = InspectionRequest(
            input_path,
            Path(blender_text) if blender_text else None,
            work,
            Path(report_text) if report_text else None,
            Path(texture_text) if texture_text else None,
        )
        self._start_worker(InspectionWorker(self.pipeline, request), self._show_result)

    def _start_roundtrip(self) -> None:
        input_path = self._validated_input_path()
        if input_path is None:
            return
        output_path = self._validated_output_path()
        if output_path is None:
            return
        self._save_settings()
        work = Path(self.work_edit.text().strip() or "work")
        blender_text = self.blender_edit.text().strip()
        report_text = self.process_report_edit.text().strip()
        texture_text = self.texture_edit.text().strip()
        self.pipeline = Pipeline(AppConfig(work_directory=work).resolved(Path.cwd()))
        request = RoundTripRequest(
            input_path,
            output_path,
            Path(blender_text) if blender_text else None,
            work,
            Path(report_text) if report_text else None,
            Path(texture_text) if texture_text else None,
        )
        self._start_worker(RoundTripWorker(self.pipeline, request), self._show_process_result)

    def _start_fast_optimize(self) -> None:
        input_path = self._validated_input_path()
        if input_path is None:
            return
        output_path = self._validated_output_path()
        if output_path is None:
            return
        self._save_settings()
        work = Path(self.work_edit.text().strip() or "work")
        blender_text = self.blender_edit.text().strip()
        report_text = self.process_report_edit.text().strip()
        texture_text = self.texture_edit.text().strip()
        self.pipeline = Pipeline(AppConfig(work_directory=work).resolved(Path.cwd()))
        request = FastOptimizeRequest(
            input_path,
            output_path,
            self.target_spin.value(),
            Path(blender_text) if blender_text else None,
            work,
            Path(report_text) if report_text else None,
            Path(texture_text) if texture_text else None,
            self.preserve_small_parts_check.isChecked(),
        )
        self._start_worker(FastOptimizeWorker(self.pipeline, request), self._show_process_result)

    def _start_runtime_rebuild(self) -> None:
        input_path = self._validated_input_path()
        if input_path is None:
            return
        output_path = self._validated_output_path()
        if output_path is None:
            return
        self._save_settings()
        work = Path(self.work_edit.text().strip() or "work")
        blender_text = self.blender_edit.text().strip()
        report_text = self.process_report_edit.text().strip()
        texture_text = self.texture_edit.text().strip()
        resolution_mode, manual_resolution = self.texture_resolution_combo.currentData()
        self.pipeline = Pipeline(AppConfig(work_directory=work).resolved(Path.cwd()))
        request = RuntimeRebuildRequest(
            input_path,
            output_path,
            self.target_spin.value(),
            Path(blender_text) if blender_text else None,
            work,
            Path(report_text) if report_text else None,
            Path(texture_text) if texture_text else None,
            resolution_mode,
            manual_resolution,
            4096,
            str(self.uv_mode_combo.currentData()),
            self.uv_margin_spin.value(),
            self.preserve_small_parts_check.isChecked(),
        )
        self._start_worker(RuntimeRebuildWorker(self.pipeline, request), self._show_process_result)

    def _start_surface_retopology(self) -> None:
        input_path = self._validated_input_path()
        if input_path is None:
            return
        output_path = self._validated_output_path()
        if output_path is None:
            return
        self._save_settings()
        work = Path(self.work_edit.text().strip() or "work")
        blender_text = self.blender_edit.text().strip()
        report_text = self.process_report_edit.text().strip()
        texture_text = self.texture_edit.text().strip()
        resolution_mode, manual_resolution = self.texture_resolution_combo.currentData()
        resolution = int(manual_resolution) if resolution_mode == "manual" and manual_resolution else 2048
        self.pipeline = Pipeline(AppConfig(work_directory=work).resolved(Path.cwd()))
        request = SurfaceRetopologyRequest(
            input_path=input_path,
            output_path=output_path,
            target_triangles=self.target_spin.value(),
            blender_path=Path(blender_text) if blender_text else None,
            work_directory=work,
            report_path=Path(report_text) if report_text else None,
            texture_path=Path(texture_text) if texture_text else None,
            texture_resolution=resolution,
            uv_margin_pixels=self.uv_margin_spin.value(),
            bake_shape_normal=self.shape_normal_check.isChecked(),
        )
        self._start_worker(SurfaceRetopologyWorker(self.pipeline, request), self._show_process_result)

    def _start_triangle_sweep(self) -> None:
        input_path = self._validated_input_path()
        if input_path is None:
            return
        output_path = self._validated_output_path(sweep=True)
        if output_path is None:
            return
        try:
            targets = parse_triangle_targets(self.sweep_targets_edit.text())
        except ValueError as exc:
            QMessageBox.warning(self, self._text("invalid_sweep_title"), str(exc))
            return
        self._save_settings()
        work = Path(self.work_edit.text().strip() or "work")
        blender_text = self.blender_edit.text().strip()
        report_text = self.process_report_edit.text().strip()
        texture_text = self.texture_edit.text().strip()
        resolution_mode, manual_resolution = self.texture_resolution_combo.currentData()
        resolution = int(manual_resolution) if resolution_mode == "manual" and manual_resolution else 2048
        output_directory = output_path.parent / f"{output_path.stem}_sweep"
        self.pipeline = Pipeline(AppConfig(work_directory=work).resolved(Path.cwd()))
        request = TriangleSweepRequest(
            input_path=input_path,
            output_directory=output_directory,
            triangle_targets=targets,
            blender_path=Path(blender_text) if blender_text else None,
            work_directory=work,
            report_path=Path(report_text) if report_text else None,
            texture_path=Path(texture_text) if texture_text else None,
            texture_resolution=resolution,
            uv_margin_pixels=self.uv_margin_spin.value(),
            bake_shape_normal=self.shape_normal_check.isChecked(),
        )
        self._start_worker(TriangleSweepWorker(self.pipeline, request), self._show_sweep_result)

    def _start_worker(self, worker: QObject, success_slot: Any) -> None:
        if not isinstance(worker, GuidedWorkflowWorker):
            self._prepare_manual_monitor(worker)
        self.thread = QThread(self)
        self.worker = worker
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self._update_progress)
        self.worker.log.connect(self.log_edit.appendPlainText)
        self.worker.succeeded.connect(success_slot)
        self.worker.cancelled.connect(self._show_cancelled)
        self.worker.failed.connect(self._show_error)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.finished.connect(self._operation_finished)
        self.log_edit.clear()
        self.progress_bar.setValue(0)
        self.inspect_button.setEnabled(False)
        self.process_button.setEnabled(False)
        self.optimize_button.setEnabled(False)
        self.rebuild_button.setEnabled(False)
        self.surface_retopology_button.setEnabled(False)
        self.triangle_sweep_button.setEnabled(False)
        self.quality_sweep_button.setEnabled(False)
        self.single_target_button.setEnabled(False)
        self.guidance_language_combo.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.thread.start()

    @Slot(dict)
    def _update_progress(self, event: dict[str, Any]) -> None:
        progress = max(0.0, min(1.0, float(event.get("progress", 0.0))))
        self.current_progress = progress
        self.progress_bar.setValue(int(progress * 1000))
        stage = str(event.get("stage", ""))
        message = str(event.get("message", stage or "Working"))
        if "uv" in stage.lower() and "complete" not in stage.lower():
            message = f"{message} — {self._text('long_phase_uv')}"
        self.stage_label.setText(message)
        if self.active_guided_workflow:
            if progress < 0.5:
                self.monitor_states["inspection"] = "running"
            else:
                self.monitor_states["inspection"] = "done"
                self.monitor_states["processing"] = "running"
            self._render_monitor()

    @Slot(dict)
    def _show_guided_workflow_result(self, result: dict[str, Any]) -> None:
        self._show_result(result["inspection"])
        operation = str(result["operation"])
        if operation == "triangle_sweep":
            self._show_sweep_result(result["processing"])
        else:
            self._show_process_result(result["processing"])
        self.monitor_states = {
            "inspection": "done",
            "processing": "done",
            "review": "done",
        }
        self.progress_bar.setValue(1000)
        completed_workflow = str(result["workflow"])
        self.stage_label.setText(
            self._text(
                "workflow_completed",
                workflow=workflow_name(self._guidance_language(), completed_workflow),
            )
        )
        self._set_review_guidance(operation, completed_workflow)

    @Slot(dict)
    def _show_result(self, report: dict[str, Any]) -> None:
        geometry = report["geometry"]
        textures = report["textures"]
        features = report["features"]
        devices = report["devices"]
        recommendation = report["recommendation"]
        render_devices = ", ".join(f"{item['name']} ({item['type']})" for item in devices.get("render_devices", [])) or "CPU only"
        values = {
            "objects": f"{geometry['objects']:,} / {geometry['meshes']:,}",
            "geometry": f"{geometry['vertices']:,} / {geometry['triangles']:,}",
            "materials": f"{geometry['materials']:,} / {textures['count']:,}",
            "texture": f"{textures['maximum_dimension']:,} px",
            "features": f"{features['armature']} / {features['animation']} / {features['shape_keys']}",
            "device": render_devices,
            "target": (
                f"{recommendation['target_triangles']:,} practical / "
                f"{recommendation['maximum_runtime_triangles']:,} ceiling / "
                f"{recommendation['lightweight_target_triangles']:,} lightweight"
            ),
            "resolution": f"{recommendation['texture_resolution']} × {recommendation['texture_resolution']} — {recommendation['texture_reason']}",
            "estimate": f"{recommendation['estimate_minimum_seconds']}–{recommendation['estimate_maximum_seconds']} seconds",
            "warnings": "\n".join(report.get("warnings", [])) or "None",
        }
        for key, value in values.items():
            self.result_labels[key].setText(value)
        self.result_labels["sweep_candidates"].setText("—")
        if self.monitor_workflow == "manual":
            self.monitor_states["inspection"] = "done"
            self.monitor_states["review"] = "done"
        self._set_review_guidance("inspection")

    @Slot(dict)
    def _show_process_result(self, report: dict[str, Any]) -> None:
        source = report["source"]
        output = report["output"]
        validation = report["validation"]
        operation = report.get("operation")
        labels = {
            "fast_optimize": "Fast Optimize",
            "runtime_rebuild": "Runtime Rebuild",
            "surface_retopology": "Surface Retopology",
            "static_fbx_round_trip": "Static round trip",
        }
        label = labels.get(operation, str(operation))
        normal_text = (
            "; shape Normal connected"
            if report.get("material", {}).get("normal_map")
            else ""
        )
        readiness = report.get("runtime_readiness", {})
        readiness_summary = readiness.get("summary", {})
        readiness_text = (
            f"; General Runtime {str(readiness.get('status')).upper()} "
            f"({int(readiness_summary.get('passed', 0))}/"
            f"{int(readiness_summary.get('checks', 0))} checks)"
            if readiness else ""
        )
        self.result_labels["roundtrip"].setText(
            f"PASS — {label}: {source['triangles']:,} → {output['triangles']:,} triangles; "
            f"{len(validation['checks'])} validation checks{normal_text}{readiness_text}; {output['path']}"
        )
        self.result_labels["sweep_candidates"].setText("—")
        self.result_labels["warnings"].setText("\n".join(report.get("warnings", [])) or "None")
        result_links: list[str] = []
        evaluation_path = report.get("artifacts", {}).get("final_evaluation")
        if evaluation_path:
            result_links.append("Result HTML: " + self._path_link(evaluation_path))
        if output.get("path"):
            result_links.extend(self._model_location_links(output["path"]))
        self.artifact_links_label.setText("<br>".join(result_links) or "—")
        if self.monitor_workflow == "manual":
            self.monitor_states["processing"] = "done"
            self.monitor_states["review"] = "done"
        self._set_review_guidance(str(operation))

    @Slot(dict)
    def _show_sweep_result(self, report: dict[str, Any]) -> None:
        successful = [item for item in report["results"] if item.get("status") == "success"]
        recommendation_rows: list[str] = []
        analysis = report.get("analysis", {})
        for item in analysis.get("recommended_candidates", []):
            distance = item.get("surface_distance", {})
            source_distance = distance.get("source_to_candidate", distance)
            texture_quality = item.get("texture_quality") or {}
            labels = " / ".join(str(value) for value in item.get("labels", [])) or "Candidate"
            local_color = texture_quality.get(
                "local_error_percent", texture_quality.get("p99_rgb_error_percent")
            )
            texture_text = (
                f"; local color error {float(local_color):.3f}%"
                if local_color is not None else ""
            )
            shape_normal = item.get("shape_normal", {})
            normal_text = (
                f"; Normal invalid {int(shape_normal.get('normal_invalid_pixels', 0)):,} px"
                if shape_normal.get("enabled") else "; Normal disabled"
            )
            readiness = item.get("runtime_readiness", {})
            readiness_text = (
                f"; Runtime {str(readiness.get('status', 'unknown')).upper()}"
            )
            recommendation_rows.append(
                f"<b>{html_escape(labels)}</b> — "
                f"{int(item['target_triangles']):,} → {int(item['output_triangles']):,} triangles; "
                f"source→candidate P95 {float(source_distance['p95_percent_of_bbox_diagonal']):.5f}%, "
                f"symmetric RMS {float(distance['rms_percent_of_bbox_diagonal']):.5f}%{texture_text}; "
                f"{int(item['uv_regions']):,} UV islands; "
                f"overlap {float(item['uv_overlap_ratio']) * 100.0:.5f}%; "
                f"invalid pixels {int(item['invalid_basecolor_pixels']):,}{normal_text}{readiness_text}"
            )
        for item in analysis.get("unavailable_recommendation_roles", []):
            recommendation_rows.append(
                f"<b>{html_escape(str(item.get('label', item.get('role', 'Candidate'))))}</b> — "
                f"unavailable: {html_escape(str(item.get('reason', 'No distinct candidate.')))}"
            )
        evaluation_path = report.get("artifacts", {}).get("final_evaluation")
        if evaluation_path:
            evaluation_uri = Path(str(evaluation_path)).expanduser().resolve().as_uri()
            recommendation_rows.append(
                f'<a href="{html_escape(evaluation_uri, quote=True)}">Open visual final evaluation</a>'
            )
        result_links: list[str] = []
        if evaluation_path:
            result_links.append(
                "Comparison HTML: " + self._path_link(evaluation_path)
            )
        link_candidates = analysis.get("recommended_candidates", []) or successful
        for item in link_candidates:
            output_path = item.get("output_path")
            if not output_path:
                continue
            labels = " / ".join(str(value) for value in item.get("labels", []))
            prefix = labels or f"{int(item['target_triangles']):,} target"
            result_links.extend(self._model_location_links(output_path, prefix))
        if not result_links and report.get("output_directory"):
            result_links.append(
                "Output directory: " + self._path_link(report["output_directory"])
            )
        self.artifact_links_label.setText("<br>".join(result_links) or "—")
        self.result_labels["sweep_candidates"].setText(
            "<br>".join(recommendation_rows) if recommendation_rows
            else "No objective-based final candidates are available in this report."
        )
        rows = []
        for item in successful:
            distance = item.get("surface_distance", {})
            source_distance = distance.get("source_to_candidate", distance)
            texture_quality = item.get("texture_quality") or {}
            rms = distance.get("rms_percent_of_bbox_diagonal")
            p95 = source_distance.get("p95_percent_of_bbox_diagonal")
            distance_text = (
                f", symmetric RMS {float(rms):.5f}%, source→candidate P95 {float(p95):.5f}%"
                if rms is not None and p95 is not None
                else ""
            )
            local_color = texture_quality.get(
                "local_error_percent", texture_quality.get("p99_rgb_error_percent")
            )
            texture_text = (
                f", local color error {float(local_color):.3f}%"
                if local_color is not None else ""
            )
            shape_normal = item.get("shape_normal", {})
            normal_text = (
                f", Normal invalid {int(shape_normal.get('normal_invalid_pixels', 0)):,} px"
                if shape_normal.get("enabled") else ", Normal disabled"
            )
            readiness = item.get("runtime_readiness", {})
            readiness_text = f", Runtime {str(readiness.get('status', 'unknown')).upper()}"
            rows.append(
                f"{item['target_triangles']:,} → {item['output_triangles']:,} triangles, "
                f"{item['uv_regions']} UV islands, overlap "
                f"{float(item['uv_overlap_ratio']) * 100.0:.5f}%, "
                f"invalid pixels {item['invalid_basecolor_pixels']}{normal_text}{readiness_text}{distance_text}{texture_text}"
            )
        for item in report["results"]:
            if item.get("status") == "success":
                continue
            error = item.get("error", {})
            rows.append(
                f"{item['target_triangles']:,} → FAILED: "
                f"{error.get('type', 'Error')}: {error.get('message', 'Unknown error')}"
            )
        status = "PASS" if report["validation"]["passed"] else "PARTIAL"
        self.result_labels["roundtrip"].setText(
            f"{status} — Triangle Sweep: {len(successful)}/{len(report['results'])} candidates; "
            f"{report['output_directory']}\n" + "\n".join(rows)
        )
        self.result_labels["warnings"].setText("\n".join(report.get("warnings", [])) or "None")
        if self.monitor_workflow == "manual":
            self.monitor_states["processing"] = "done"
            self.monitor_states["review"] = "done"
        self._set_review_guidance("triangle_sweep")

    @Slot(str)
    def _show_error(self, message: str) -> None:
        self.log_edit.appendPlainText(message)
        for phase in ("inspection", "processing"):
            if self.monitor_states[phase] == "running":
                self.monitor_states[phase] = "failed"
        self.stage_label.setText(message.splitlines()[0])
        self._render_monitor()
        QMessageBox.critical(self, self._text("operation_failed_title"), message.splitlines()[0])

    @Slot()
    def _show_cancelled(self) -> None:
        message = self._text("operation_cancelled")
        for phase in ("inspection", "processing"):
            if self.monitor_states[phase] == "running":
                self.monitor_states[phase] = "cancelled"
        self.stage_label.setText(message)
        self.log_edit.appendPlainText(message)
        self._render_monitor()

    def _cancel(self) -> None:
        if self.pipeline:
            self.cancel_button.setEnabled(False)
            self.pipeline.cancel()
            self.stage_label.setText(self._text("cancelling"))

    def _operation_finished(self) -> None:
        self.inspect_button.setEnabled(True)
        self.process_button.setEnabled(True)
        self.optimize_button.setEnabled(True)
        self.rebuild_button.setEnabled(True)
        self.surface_retopology_button.setEnabled(True)
        self.triangle_sweep_button.setEnabled(True)
        self.quality_sweep_button.setEnabled(True)
        self.single_target_button.setEnabled(True)
        self.guidance_language_combo.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.active_guided_workflow = None
        self.elapsed_timer.stop()
        if self.workflow_started_at is not None:
            self.workflow_finished_elapsed = max(
                0, int(time.monotonic() - self.workflow_started_at)
            )
            self.workflow_started_at = None
        self._update_time_status()
        self.thread = None
        self.worker = None

    def closeEvent(self, event: Any) -> None:
        self._save_settings()
        if self.pipeline and self.thread and self.thread.isRunning():
            self.pipeline.cancel()
            self.thread.quit()
            self.thread.wait(3000)
        super().closeEvent(event)
