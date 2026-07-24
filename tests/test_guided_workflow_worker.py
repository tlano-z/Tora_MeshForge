from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from tora_meshforge.gui.app import prepare_windows_dll_search

prepare_windows_dll_search()

from tora_meshforge.gui.main_window import GuidedWorkflowWorker
from tora_meshforge.models import InspectionRequest, SurfaceRetopologyRequest
from tora_meshforge.utils.cancellation import CancellationToken


class _FakePipeline:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.token = CancellationToken()
        self.cancel_after_inspection = False

    def prepare_operation(self) -> CancellationToken:
        self.token = CancellationToken()
        return self.token

    def inspect(
        self,
        _request: Any,
        *,
        on_log: Any,
        on_progress: Any,
        _cancellation_token: CancellationToken,
    ) -> Any:
        assert _cancellation_token is self.token
        _cancellation_token.raise_if_cancelled()
        self.calls.append("inspect")
        on_log("inspection log")
        on_progress({"progress": 1.0, "message": "inspection complete"})
        if self.cancel_after_inspection:
            _cancellation_token.cancel()
        return SimpleNamespace(report={"operation": "inspection"})

    def surface_retopology(
        self,
        _request: Any,
        *,
        on_log: Any,
        on_progress: Any,
        _cancellation_token: CancellationToken,
    ) -> Any:
        assert _cancellation_token is self.token
        _cancellation_token.raise_if_cancelled()
        self.calls.append("surface_retopology")
        on_log("retopology log")
        on_progress({"progress": 1.0, "message": "retopology complete"})
        return SimpleNamespace(report={"operation": "surface_retopology"})


def test_guided_worker_runs_inspection_before_single_target_build() -> None:
    pipeline = _FakePipeline()
    worker = GuidedWorkflowWorker(
        pipeline,  # type: ignore[arg-type]
        "direct_retopology",
        InspectionRequest(Path("source.fbx")),
        "surface_retopology",
        SurfaceRetopologyRequest(Path("source.fbx"), Path("output.fbx"), 50_000),
        ("Inspection", "Build and evaluate"),
    )
    results: list[dict[str, Any]] = []
    progress: list[float] = []
    worker.succeeded.connect(results.append)
    worker.progress.connect(lambda event: progress.append(float(event["progress"])))

    worker.run()

    assert pipeline.calls == ["inspect", "surface_retopology"]
    assert results[0]["workflow"] == "direct_retopology"
    assert results[0]["processing"]["operation"] == "surface_retopology"
    assert progress == [0.5, 1.0]


def test_guided_cancel_after_inspection_does_not_start_processing() -> None:
    pipeline = _FakePipeline()
    pipeline.cancel_after_inspection = True
    worker = GuidedWorkflowWorker(
        pipeline,  # type: ignore[arg-type]
        "direct_retopology",
        InspectionRequest(Path("source.fbx")),
        "surface_retopology",
        SurfaceRetopologyRequest(Path("source.fbx"), Path("output.fbx"), 50_000),
        ("Inspection", "Build and evaluate"),
    )
    cancellations: list[bool] = []
    failures: list[str] = []
    worker.cancelled.connect(lambda: cancellations.append(True))
    worker.failed.connect(failures.append)

    worker.run()

    assert pipeline.calls == ["inspect"]
    assert cancellations == [True]
    assert failures == []
