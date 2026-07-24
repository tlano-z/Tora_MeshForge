from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
from typing import Any

from tora_meshforge.config import AppConfig
from tora_meshforge.estimation import build_recommendation
from tora_meshforge.models import (
    InspectionRequest,
    InspectionResult,
    FastOptimizeRequest,
    LogCallback,
    ProcessingResult,
    ProgressCallback,
    RoundTripRequest,
    RuntimeRebuildRequest,
    SurfaceRetopologyRequest,
    TriangleSweepRequest,
    TriangleSweepResult,
)
from tora_meshforge.runtime_validation import build_runtime_readiness
from tora_meshforge.sweep import (
    build_single_evaluation_html,
    build_sweep_evaluation_html,
    parse_triangle_targets,
    summarize_sweep_results,
)
from tora_meshforge.uv_retry import (
    command_with_fixed_uv_regions,
    final_uv_retry_candidates,
    uv_attempt_summary,
)
from tora_meshforge.utils.cancellation import CancelledError, CancellationToken, ProcessController
from tora_meshforge.utils.executable_discovery import discover_blender
from tora_meshforge.utils.file_layout import JobLayout
from tora_meshforge.utils.subprocess_runner import ProcessExecutionError, run_process


SUPPORTED_INPUTS = {".fbx", ".glb", ".gltf", ".obj"}


class Pipeline:
    def __init__(self, config: AppConfig | None = None) -> None:
        self.config = config or AppConfig().resolved(Path.cwd())
        self._cancellation = CancellationToken()
        self._controller = ProcessController()

    def cancel(self) -> None:
        self._cancellation.cancel()
        self._controller.terminate()

    def prepare_operation(self) -> CancellationToken:
        """Create the token shared by every stage of one user-requested operation."""
        self._cancellation = CancellationToken()
        return self._cancellation

    def _activate_cancellation(
        self,
        cancellation_token: CancellationToken | None,
    ) -> CancellationToken:
        self._cancellation = cancellation_token or CancellationToken()
        self._cancellation.raise_if_cancelled()
        return self._cancellation

    def inspect(
        self,
        request: InspectionRequest,
        *,
        on_log: LogCallback | None = None,
        on_progress: ProgressCallback | None = None,
        _cancellation_token: CancellationToken | None = None,
    ) -> InspectionResult:
        self._activate_cancellation(_cancellation_token)
        input_path = request.input_path.expanduser().resolve()
        self._validate_input(input_path)
        texture_path = request.texture_path.expanduser().resolve() if request.texture_path else None
        if texture_path is not None:
            self._validate_texture(texture_path)
        blender = discover_blender(request.blender_path, self.config.blender_path)
        if blender is None:
            raise FileNotFoundError("Blender executable was not found. Select blender.exe or set BLENDER_PATH.")
        work = (request.work_directory or self.config.work_directory).expanduser().resolve()
        layout = JobLayout.create(work, input_path)
        destination = request.report_path.expanduser().resolve() if request.report_path else None
        logs: list[str] = []

        def event(kind: str, payload: dict[str, Any]) -> None:
            record = {"time": datetime.now(timezone.utc).isoformat(), "kind": kind, **payload}
            with layout.event_log.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")

        def log(message: str) -> None:
            logs.append(message)
            event("log", {"level": "info", "message": message})
            if on_log:
                on_log(message)

        def progress(update: dict[str, Any]) -> None:
            event("progress", update)
            if on_progress:
                on_progress(update)

        progress({"stage": "prepare", "progress": 0.02, "message": "Preparing isolated job directory"})
        layout.copy_source(input_path)
        script = Path(__file__).with_name("blender") / "inspect_scene.py"
        command = [
            blender,
            "--background",
            "--factory-startup",
            "--disable-autoexec",
            "--python",
            script,
            "--",
            "--input",
            input_path,
            "--report",
            layout.report,
            "--original-path",
            input_path,
        ]
        if texture_path is not None:
            command.extend(["--texture-override", texture_path])
        log(f"Using Blender: {blender}")
        try:
            process_result = run_process(
                command,
                cwd=layout.root,
                timeout_seconds=self.config.inspection_timeout_seconds,
                cancellation=self._cancellation,
                controller=self._controller,
                on_log=log,
                on_progress=progress,
            )
        except CancelledError:
            cancelled_report = {
                "application": "Tora_MeshForge",
                "schema_version": "1.0",
                "status": "cancelled",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "source": {"path": str(input_path), "format": input_path.suffix.lower().lstrip("."), "file_size_bytes": input_path.stat().st_size},
                "geometry": {"objects": 0, "meshes": 0, "vertices": 0, "triangles": 0, "materials": 0, "bounding_box": {}},
                "textures": {"count": 0, "maximum_dimension": 0, "images": [], "missing_files": []},
                "features": {},
                "devices": {},
                "recommendation": {},
                "warnings": [],
                "errors": [],
            }
            self._write_json(layout.report, cancelled_report)
            if destination:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(layout.report, destination)
            progress({"stage": "cancelled", "progress": 0.0, "message": "Inspection cancelled"})
            raise
        with layout.report.open("r", encoding="utf-8") as handle:
            report: dict[str, Any] = json.load(handle)
        recommendation = build_recommendation(report, self.config.maximum_texture_resolution)
        report["application"] = "Tora_MeshForge"
        report["schema_version"] = "1.0"
        report["status"] = "success"
        report["created_at"] = datetime.now(timezone.utc).isoformat()
        report["timings"] = {"inspection_seconds": round(process_result.elapsed_seconds, 3)}
        report["recommendation"] = asdict(recommendation)
        report.setdefault("warnings", []).extend(self._build_warnings(report))
        self._write_json(layout.report, report)
        if destination:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(layout.report, destination)
        progress({"stage": "complete", "progress": 1.0, "message": "Inspection complete"})
        return InspectionResult(destination or layout.report, layout.root, report, logs)

    def run(
        self,
        request: RoundTripRequest,
        *,
        on_log: LogCallback | None = None,
        on_progress: ProgressCallback | None = None,
        _cancellation_token: CancellationToken | None = None,
    ) -> ProcessingResult:
        """Perform Milestone 3 static FBX round trip and reload validation."""
        self._activate_cancellation(_cancellation_token)
        input_path = request.input_path.expanduser().resolve()
        output_path = request.output_path.expanduser().resolve()
        self._validate_input(input_path)
        self._validate_roundtrip_output(input_path, output_path)
        texture_path = request.texture_path.expanduser().resolve() if request.texture_path else None
        if texture_path is not None:
            self._validate_texture(texture_path)
        blender = discover_blender(request.blender_path, self.config.blender_path)
        if blender is None:
            raise FileNotFoundError("Blender executable was not found. Select blender.exe or set BLENDER_PATH.")
        work = (request.work_directory or self.config.work_directory).expanduser().resolve()
        layout = JobLayout.create(work, input_path)
        report_destination = (
            request.report_path.expanduser().resolve()
            if request.report_path
            else output_path.with_suffix(".report.json")
        )
        if report_destination == output_path:
            raise ValueError("Report path must differ from the output FBX path.")
        logs: list[str] = []

        def event(kind: str, payload: dict[str, Any]) -> None:
            record = {"time": datetime.now(timezone.utc).isoformat(), "kind": kind, **payload}
            with layout.event_log.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")

        def log(message: str) -> None:
            logs.append(message)
            event("log", {"level": "info", "message": message})
            if on_log:
                on_log(message)

        def progress(update: dict[str, Any]) -> None:
            event("progress", update)
            if on_progress:
                on_progress(update)

        progress({"stage": "prepare", "progress": 0.02, "message": "Preparing static round-trip job"})
        layout.copy_source(input_path)
        command = [
            blender,
            "--background",
            "--factory-startup",
            "--disable-autoexec",
            "--python",
            Path(__file__).with_name("blender") / "roundtrip_scene.py",
            "--",
            "--input",
            input_path,
            "--output",
            layout.output_model,
            "--report",
            layout.process_report,
        ]
        if texture_path is not None:
            command.extend(["--texture-override", texture_path])
        log(f"Using Blender: {blender}")
        try:
            process_result = run_process(
                command,
                cwd=layout.root,
                timeout_seconds=self.config.inspection_timeout_seconds,
                cancellation=self._cancellation,
                controller=self._controller,
                on_log=log,
                on_progress=progress,
            )
        except CancelledError:
            cancelled_report = {
                "application": "Tora_MeshForge",
                "schema_version": "1.0",
                "operation": "static_fbx_round_trip",
                "status": "cancelled",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "input": str(input_path),
                "requested_output": str(output_path),
                "warnings": [],
                "errors": [],
            }
            self._write_json(layout.process_report, cancelled_report)
            report_destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(layout.process_report, report_destination)
            progress({"stage": "cancelled", "progress": 0.0, "message": "Static round trip cancelled"})
            raise
        except (ProcessExecutionError, TimeoutError) as exc:
            if layout.process_report.is_file():
                with layout.process_report.open("r", encoding="utf-8") as handle:
                    failure_report = json.load(handle)
            else:
                failure_report = {
                    "operation": "static_fbx_round_trip",
                    "warnings": [],
                    "errors": [{"type": type(exc).__name__, "message": str(exc)}],
                }
            failure_report.update({
                "application": "Tora_MeshForge",
                "schema_version": "1.0",
                "status": "failure",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "input": {"path": str(input_path), "file_size_bytes": input_path.stat().st_size},
                "requested_output": str(output_path),
            })
            self._write_json(layout.process_report, failure_report)
            report_destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(layout.process_report, report_destination)
            raise
        with layout.process_report.open("r", encoding="utf-8") as handle:
            report: dict[str, Any] = json.load(handle)
        if not report.get("validation", {}).get("passed"):
            raise RuntimeError("Blender returned without a passing round-trip validation report.")
        report.update({
            "application": "Tora_MeshForge",
            "schema_version": "1.0",
            "status": "success",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "input": {"path": str(input_path), "file_size_bytes": input_path.stat().st_size},
            "timings": {"round_trip_seconds": round(process_result.elapsed_seconds, 3)},
        })
        report["output"]["job_path"] = report["output"]["path"]
        report["output"]["path"] = str(output_path)
        self._write_json(layout.process_report, report)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        report_destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(layout.output_model, output_path)
        shutil.copy2(layout.process_report, report_destination)
        progress({"stage": "complete", "progress": 1.0, "message": "Static FBX round trip complete"})
        return ProcessingResult(output_path, report_destination, layout.root, report, logs)

    def optimize(
        self,
        request: FastOptimizeRequest,
        *,
        on_log: LogCallback | None = None,
        on_progress: ProgressCallback | None = None,
        _cancellation_token: CancellationToken | None = None,
    ) -> ProcessingResult:
        """Run the Blender Decimate Fast Optimize backend."""
        self._activate_cancellation(_cancellation_token)
        input_path = request.input_path.expanduser().resolve()
        output_path = request.output_path.expanduser().resolve()
        self._validate_input(input_path)
        self._validate_roundtrip_output(input_path, output_path)
        if request.target_triangles < 1_000:
            raise ValueError("Target triangles must be at least 1,000.")
        texture_path = request.texture_path.expanduser().resolve() if request.texture_path else None
        if texture_path is not None:
            self._validate_texture(texture_path)
        blender = discover_blender(request.blender_path, self.config.blender_path)
        if blender is None:
            raise FileNotFoundError("Blender executable was not found. Select blender.exe or set BLENDER_PATH.")
        work = (request.work_directory or self.config.work_directory).expanduser().resolve()
        layout = JobLayout.create(work, input_path)
        report_destination = (
            request.report_path.expanduser().resolve()
            if request.report_path
            else output_path.with_suffix(".report.json")
        )
        if report_destination == output_path:
            raise ValueError("Report path must differ from the output FBX path.")
        logs: list[str] = []

        def event(kind: str, payload: dict[str, Any]) -> None:
            record = {"time": datetime.now(timezone.utc).isoformat(), "kind": kind, **payload}
            with layout.event_log.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")

        def log(message: str) -> None:
            logs.append(message)
            event("log", {"level": "info", "message": message})
            if on_log:
                on_log(message)

        def progress(update: dict[str, Any]) -> None:
            event("progress", update)
            if on_progress:
                on_progress(update)

        progress({"stage": "prepare", "progress": 0.02, "message": "Preparing Fast Optimize job"})
        layout.copy_source(input_path)
        command = [
            blender,
            "--background",
            "--factory-startup",
            "--disable-autoexec",
            "--python",
            Path(__file__).with_name("blender") / "optimize_scene.py",
            "--",
            "--input",
            input_path,
            "--output",
            layout.output_model,
            "--report",
            layout.process_report,
            "--target-triangles",
            str(request.target_triangles),
        ]
        if texture_path is not None:
            command.extend(["--texture-override", texture_path])
        if request.preserve_small_parts:
            command.append("--preserve-small-parts")
        log(f"Using Blender: {blender}")
        try:
            process_result = run_process(
                command,
                cwd=layout.root,
                timeout_seconds=self.config.inspection_timeout_seconds,
                cancellation=self._cancellation,
                controller=self._controller,
                on_log=log,
                on_progress=progress,
            )
        except CancelledError:
            cancelled_report = {
                "application": "Tora_MeshForge",
                "schema_version": "1.0",
                "operation": "fast_optimize",
                "status": "cancelled",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "input": str(input_path),
                "requested_output": str(output_path),
                "target_triangles": request.target_triangles,
                "warnings": [],
                "errors": [],
            }
            self._write_json(layout.process_report, cancelled_report)
            report_destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(layout.process_report, report_destination)
            progress({"stage": "cancelled", "progress": 0.0, "message": "Fast Optimize cancelled"})
            raise
        except (ProcessExecutionError, TimeoutError) as exc:
            if layout.process_report.is_file():
                with layout.process_report.open("r", encoding="utf-8") as handle:
                    failure_report = json.load(handle)
            else:
                failure_report = {
                    "operation": "fast_optimize",
                    "warnings": [],
                    "errors": [{"type": type(exc).__name__, "message": str(exc)}],
                }
            failure_report.update({
                "application": "Tora_MeshForge",
                "schema_version": "1.0",
                "status": "failure",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "input": {"path": str(input_path), "file_size_bytes": input_path.stat().st_size},
                "requested_output": str(output_path),
                "target_triangles": request.target_triangles,
            })
            self._write_json(layout.process_report, failure_report)
            report_destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(layout.process_report, report_destination)
            raise
        with layout.process_report.open("r", encoding="utf-8") as handle:
            report: dict[str, Any] = json.load(handle)
        if not report.get("validation", {}).get("passed"):
            raise RuntimeError("Blender returned without a passing Fast Optimize validation report.")
        report.update({
            "application": "Tora_MeshForge",
            "schema_version": "1.0",
            "status": "success",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "input": {"path": str(input_path), "file_size_bytes": input_path.stat().st_size},
            "timings": {"fast_optimize_seconds": round(process_result.elapsed_seconds, 3)},
        })
        report["output"]["job_path"] = report["output"]["path"]
        report["output"]["path"] = str(output_path)
        self._write_json(layout.process_report, report)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        report_destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(layout.output_model, output_path)
        shutil.copy2(layout.process_report, report_destination)
        progress({"stage": "complete", "progress": 1.0, "message": "Fast Optimize complete"})
        return ProcessingResult(output_path, report_destination, layout.root, report, logs)

    def runtime_rebuild(
        self,
        request: RuntimeRebuildRequest,
        *,
        on_log: LogCallback | None = None,
        on_progress: ProgressCallback | None = None,
        _cancellation_token: CancellationToken | None = None,
    ) -> ProcessingResult:
        """Build new runtime UVs and reconstruct Base Color on a reduced mesh."""
        self._activate_cancellation(_cancellation_token)
        input_path = request.input_path.expanduser().resolve()
        output_path = request.output_path.expanduser().resolve()
        self._validate_input(input_path)
        self._validate_roundtrip_output(input_path, output_path)
        if request.target_triangles < 1_000:
            raise ValueError("Target triangles must be at least 1,000.")
        allowed_resolutions = {512, 1024, 2048, 4096, 8192}
        if request.texture_resolution_mode not in {"auto", "match-source", "manual"}:
            raise ValueError("Unknown texture resolution mode.")
        if request.manual_texture_resolution is not None and request.manual_texture_resolution not in allowed_resolutions:
            raise ValueError("Manual texture resolution must be 512, 1024, 2048, 4096, or 8192.")
        if request.texture_resolution_mode == "manual" and request.manual_texture_resolution is None:
            raise ValueError("Manual texture resolution mode requires a resolution.")
        if request.maximum_texture_resolution not in allowed_resolutions:
            raise ValueError("Maximum texture resolution must be 512, 1024, 2048, 4096, or 8192.")
        if request.uv_mode not in {"consolidated", "smart", "angle"}:
            raise ValueError("Unknown UV mode.")
        if not 0 <= request.uv_margin_pixels <= 128:
            raise ValueError("UV margin must be between 0 and 128 pixels.")
        texture_path = request.texture_path.expanduser().resolve() if request.texture_path else None
        if texture_path is not None:
            self._validate_texture(texture_path)
        blender = discover_blender(request.blender_path, self.config.blender_path)
        if blender is None:
            raise FileNotFoundError("Blender executable was not found. Select blender.exe or set BLENDER_PATH.")
        work = (request.work_directory or self.config.work_directory).expanduser().resolve()
        layout = JobLayout.create(work, input_path)
        report_destination = (
            request.report_path.expanduser().resolve()
            if request.report_path
            else output_path.with_suffix(".report.json")
        )
        if report_destination == output_path:
            raise ValueError("Report path must differ from the output FBX path.")
        bake_directory = layout.output / "textures"
        bake_directory.mkdir(parents=True, exist_ok=True)
        logs: list[str] = []

        def event(kind: str, payload: dict[str, Any]) -> None:
            record = {"time": datetime.now(timezone.utc).isoformat(), "kind": kind, **payload}
            with layout.event_log.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")

        def log(message: str) -> None:
            logs.append(message)
            event("log", {"level": "info", "message": message})
            if on_log:
                on_log(message)

        def progress(update: dict[str, Any]) -> None:
            event("progress", update)
            if on_progress:
                on_progress(update)

        progress({"stage": "prepare", "progress": 0.02, "message": "Preparing Runtime Rebuild job"})
        layout.copy_source(input_path)
        maximum_resolution = min(request.maximum_texture_resolution, self.config.maximum_texture_resolution)
        command = [
            blender,
            "--background",
            "--factory-startup",
            "--disable-autoexec",
            "--python",
            Path(__file__).with_name("blender") / "runtime_rebuild_scene.py",
            "--",
            "--input",
            input_path,
            "--output",
            layout.output_model,
            "--report",
            layout.process_report,
            "--bake-dir",
            bake_directory,
            "--target-triangles",
            str(request.target_triangles),
            "--texture-resolution-mode",
            request.texture_resolution_mode,
            "--maximum-texture-resolution",
            str(maximum_resolution),
            "--uv-mode",
            request.uv_mode,
            "--uv-margin-pixels",
            str(request.uv_margin_pixels),
        ]
        if request.manual_texture_resolution is not None:
            command.extend(["--manual-texture-resolution", str(request.manual_texture_resolution)])
        if texture_path is not None:
            command.extend(["--texture-override", texture_path])
        if request.preserve_small_parts:
            command.append("--preserve-small-parts")
        log(f"Using Blender: {blender}")
        try:
            process_result = run_process(
                command,
                cwd=layout.root,
                timeout_seconds=self.config.inspection_timeout_seconds,
                cancellation=self._cancellation,
                controller=self._controller,
                on_log=log,
                on_progress=progress,
            )
        except CancelledError:
            cancelled_report = {
                "application": "Tora_MeshForge",
                "schema_version": "1.0",
                "operation": "runtime_rebuild",
                "status": "cancelled",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "input": str(input_path),
                "requested_output": str(output_path),
                "target_triangles": request.target_triangles,
                "warnings": [],
                "errors": [],
            }
            self._write_json(layout.process_report, cancelled_report)
            report_destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(layout.process_report, report_destination)
            progress({"stage": "cancelled", "progress": 0.0, "message": "Runtime Rebuild cancelled"})
            raise
        except (ProcessExecutionError, TimeoutError) as exc:
            if layout.process_report.is_file():
                with layout.process_report.open("r", encoding="utf-8") as handle:
                    failure_report = json.load(handle)
            else:
                failure_report = {
                    "operation": "runtime_rebuild",
                    "warnings": [],
                    "errors": [{"type": type(exc).__name__, "message": str(exc)}],
                }
            failure_report.update({
                "application": "Tora_MeshForge",
                "schema_version": "1.0",
                "status": "failure",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "input": {"path": str(input_path), "file_size_bytes": input_path.stat().st_size},
                "requested_output": str(output_path),
                "target_triangles": request.target_triangles,
            })
            self._write_json(layout.process_report, failure_report)
            report_destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(layout.process_report, report_destination)
            raise
        with layout.process_report.open("r", encoding="utf-8") as handle:
            report: dict[str, Any] = json.load(handle)
        if not report.get("validation", {}).get("passed"):
            raise RuntimeError("Blender returned without a passing Runtime Rebuild validation report.")
        report.update({
            "application": "Tora_MeshForge",
            "schema_version": "1.0",
            "status": "success",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "input": {"path": str(input_path), "file_size_bytes": input_path.stat().st_size},
            "timings": {"runtime_rebuild_seconds": round(process_result.elapsed_seconds, 3)},
        })
        report["output"]["job_path"] = report["output"]["path"]
        report["output"]["path"] = str(output_path)
        public_texture_directory = output_path.parent / f"{output_path.stem}_textures"
        public_texture_directory.mkdir(parents=True, exist_ok=True)
        for item in report.get("bake", {}).get("objects", []):
            for field in ("basecolor", "invalid_mask"):
                source_artifact = Path(item[field])
                destination = public_texture_directory / source_artifact.name
                shutil.copy2(source_artifact, destination)
                item["job_" + field] = str(source_artifact)
                item[field] = str(destination.resolve())
        self._write_json(layout.process_report, report)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        report_destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(layout.output_model, output_path)
        shutil.copy2(layout.process_report, report_destination)
        progress({"stage": "complete", "progress": 1.0, "message": "Runtime Rebuild complete"})
        return ProcessingResult(output_path, report_destination, layout.root, report, logs)

    def surface_retopology(
        self,
        request: SurfaceRetopologyRequest,
        *,
        on_log: LogCallback | None = None,
        on_progress: ProgressCallback | None = None,
        _cancellation_token: CancellationToken | None = None,
        _generate_evaluation: bool = True,
    ) -> ProcessingResult:
        """Rebuild a continuous surface, create region UVs, and project Base Color."""
        self._activate_cancellation(_cancellation_token)
        input_path = request.input_path.expanduser().resolve()
        output_path = request.output_path.expanduser().resolve()
        self._validate_input(input_path)
        self._validate_roundtrip_output(input_path, output_path)
        if request.target_triangles < 1_000:
            raise ValueError("Target triangles must be at least 1,000.")
        if request.texture_resolution not in {512, 1024, 2048, 4096, 8192}:
            raise ValueError("Texture resolution must be 512, 1024, 2048, 4096, or 8192.")
        if not 0 <= request.uv_margin_pixels <= 128:
            raise ValueError("UV margin must be between 0 and 128 pixels.")
        if not 128 <= request.voxel_divisions <= 1024:
            raise ValueError("Voxel divisions must be between 128 and 1024.")
        if not 16 <= request.uv_regions <= 1024:
            raise ValueError("UV regions must be between 16 and 1024.")
        if not 1 <= request.uv_target_regions <= request.uv_regions:
            raise ValueError("UV target regions must be between 1 and the initial UV region count.")
        if request.maximum_chart_faces != 0 and not 64 <= request.maximum_chart_faces <= request.target_triangles:
            raise ValueError("Maximum chart faces must be 0 or between 64 and the target triangle count.")
        if not 0 <= request.maximum_merge_trials <= 65_536:
            raise ValueError("Maximum merge trials must be between 0 and 65,536.")
        if not 1 <= request.maximum_merge_batch_size <= 512:
            raise ValueError("Maximum merge batch size must be between 1 and 512.")
        texture_path = request.texture_path.expanduser().resolve() if request.texture_path else None
        if texture_path is not None:
            self._validate_texture(texture_path)
        blender = discover_blender(request.blender_path, self.config.blender_path)
        if blender is None:
            raise FileNotFoundError("Blender executable was not found. Select blender.exe or set BLENDER_PATH.")
        work = (request.work_directory or self.config.work_directory).expanduser().resolve()
        layout = JobLayout.create(work, input_path)
        layout.copy_source(input_path)
        report_destination = (
            request.report_path.expanduser().resolve()
            if request.report_path
            else output_path.with_suffix(".report.json")
        )
        if report_destination == output_path:
            raise ValueError("Report path must differ from the output FBX path.")
        topology_path = layout.output / "retopology.fbx"
        topology_report_path = layout.output / "retopology.report.json"
        uv_path = layout.output / "retopology-uv.fbx"
        uv_report_path = layout.output / "retopology-uv.report.json"
        bake_report_path = layout.output / "retopology-bake.report.json"
        bake_directory = layout.output / "textures"
        uv_layout_path = layout.output / "uv-layout.png"
        uv_texture_layout_path = layout.output / "uv-layout-texture.png"
        bake_directory.mkdir(parents=True, exist_ok=True)
        logs: list[str] = []

        def event(kind: str, payload: dict[str, Any]) -> None:
            record = {"time": datetime.now(timezone.utc).isoformat(), "kind": kind, **payload}
            with layout.event_log.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")

        def log(message: str) -> None:
            logs.append(message)
            event("log", {"level": "info", "message": message})
            if on_log:
                on_log(message)

        def progress(update: dict[str, Any]) -> None:
            event("progress", update)
            if on_progress:
                on_progress(update)

        scripts = Path(__file__).with_name("blender")
        commands: list[tuple[str, float, list[Any]]] = [
            (
                "surface",
                0.08,
                [
                    blender, "--background", "--factory-startup", "--disable-autoexec",
                    "--python", scripts / "retopology_probe.py", "--",
                    "--input", input_path,
                    "--output", topology_path,
                    "--report", topology_report_path,
                    "--method", "voxel",
                    "--target-triangles", str(request.target_triangles),
                    "--voxel-divisions", str(request.voxel_divisions),
                ],
            ),
            (
                "uv",
                0.42,
                [
                    blender, "--background", "--factory-startup", "--disable-autoexec",
                    "--python", scripts / "retopology_uv_probe.py", "--",
                    "--input", topology_path,
                    "--output", uv_path,
                    "--report", uv_report_path,
                    "--mode", "regions",
                    "--regions", str(request.uv_regions),
                    "--curvature-weight", str(request.curvature_weight),
                    "--unwrap-method", "conformal",
                    "--repair-overlap-regions",
                    "--merge-regions",
                    "--target-regions", str(request.uv_target_regions),
                    "--maximum-chart-faces", str(request.maximum_chart_faces),
                    "--maximum-merge-trials", str(request.maximum_merge_trials),
                    "--maximum-merge-batch-size", str(request.maximum_merge_batch_size),
                    "--maximum-angle-stretch", "30",
                    "--maximum-area-stretch", "2.5",
                    "--resolution", str(request.texture_resolution),
                    "--margin-pixels", str(request.uv_margin_pixels),
                ],
            ),
            (
                "bake",
                0.72,
                [
                    blender, "--background", "--factory-startup", "--disable-autoexec",
                    "--python", scripts / "retopology_bake_probe.py", "--",
                    "--source", input_path,
                    "--target", uv_path,
                    "--output", layout.output_model,
                    "--report", bake_report_path,
                    "--bake-dir", bake_directory,
                    "--resolution", str(request.texture_resolution),
                    "--margin-pixels", str(request.uv_margin_pixels),
                ],
            ),
        ]
        if request.adaptive_initial_regions:
            commands[1][2].append("--adaptive-initial-regions")
        if request.organize_uv_islands:
            commands[1][2].extend(["--organize-islands", "--organization-packing", "efficient"])
        if texture_path is not None:
            commands[-1][2].extend(["--texture-override", texture_path])
        if not request.bake_shape_normal:
            commands[-1][2].append("--skip-shape-normal")

        process_seconds: dict[str, float] = {}
        uv_retry_history: list[dict[str, Any]] = []
        log(f"Using Blender: {blender}")
        try:
            for stage, fraction, command in commands:
                self._cancellation.raise_if_cancelled()
                progress({"stage": stage, "progress": fraction, "message": f"Running Surface Retopology {stage}"})
                try:
                    result = run_process(
                        command,
                        cwd=layout.root,
                        timeout_seconds=self.config.inspection_timeout_seconds,
                        cancellation=self._cancellation,
                        controller=self._controller,
                        on_log=log,
                        on_progress=progress,
                    )
                    process_seconds[stage] = result.elapsed_seconds
                except ProcessExecutionError as initial_error:
                    if stage != "uv" or not request.adaptive_initial_regions or not uv_report_path.is_file():
                        raise
                    with uv_report_path.open("r", encoding="utf-8") as handle:
                        initial_uv_report = json.load(handle)
                    retry_candidates = final_uv_retry_candidates(initial_uv_report)
                    if not retry_candidates:
                        raise
                    sampling = initial_uv_report["segmentation"]["initial_region_sampling"]
                    selected_regions = int(sampling["selected_regions"])
                    uv_retry_history.append(uv_attempt_summary(initial_uv_report, selected_regions))
                    elapsed = initial_error.result.elapsed_seconds
                    last_error: ProcessExecutionError = initial_error
                    for retry_index, retry_regions in enumerate(retry_candidates, start=1):
                        self._cancellation.raise_if_cancelled()
                        log(
                            "Final UV validation failed after the adaptive preliminary pass; "
                            f"retrying with {retry_regions} initial regions."
                        )
                        progress({
                            "stage": "uv_retry",
                            "progress": fraction,
                            "message": (
                                f"Retrying final UV validation with {retry_regions} initial regions "
                                f"({retry_index}/{len(retry_candidates)})"
                            ),
                        })
                        retry_command = command_with_fixed_uv_regions(command, retry_regions)
                        try:
                            retry_result = run_process(
                                retry_command,
                                cwd=layout.root,
                                timeout_seconds=self.config.inspection_timeout_seconds,
                                cancellation=self._cancellation,
                                controller=self._controller,
                                on_log=log,
                                on_progress=progress,
                            )
                            elapsed += retry_result.elapsed_seconds
                            with uv_report_path.open("r", encoding="utf-8") as handle:
                                retry_report = json.load(handle)
                            uv_retry_history.append(uv_attempt_summary(retry_report, retry_regions))
                            retry_report["final_validation_retry"] = {
                                "triggered": True,
                                "initial_selected_regions": selected_regions,
                                "attempts": uv_retry_history,
                                "selected_regions": retry_regions,
                            }
                            self._write_json(uv_report_path, retry_report)
                            process_seconds[stage] = elapsed
                            break
                        except ProcessExecutionError as retry_error:
                            elapsed += retry_error.result.elapsed_seconds
                            last_error = retry_error
                            if uv_report_path.is_file():
                                with uv_report_path.open("r", encoding="utf-8") as handle:
                                    retry_report = json.load(handle)
                                uv_retry_history.append(uv_attempt_summary(retry_report, retry_regions))
                    else:
                        raise last_error
            with bake_report_path.open("r", encoding="utf-8") as handle:
                preview_bake = json.load(handle)
            preview_commands: list[tuple[str, float, list[Any]]] = [
                (
                    "uv_layout",
                    0.90,
                    [
                        blender, "--background", "--factory-startup", "--disable-autoexec",
                        "--python", scripts / "render_uv_layout.py", "--",
                        "--input", layout.output_model,
                        "--output", uv_layout_path,
                        "--size", "1536",
                        "--line-width", "1",
                    ],
                ),
                (
                    "uv_texture_layout",
                    0.95,
                    [
                        blender, "--background", "--factory-startup", "--disable-autoexec",
                        "--python", scripts / "render_uv_layout.py", "--",
                        "--input", layout.output_model,
                        "--output", uv_texture_layout_path,
                        "--size", "1536",
                        "--texture", Path(preview_bake["basecolor"]),
                        "--texture-opacity", "0.78",
                        "--line-width", "1",
                        "--line-opacity", "0.55",
                    ],
                ),
            ]
            for stage, fraction, command in preview_commands:
                self._cancellation.raise_if_cancelled()
                progress({"stage": stage, "progress": fraction, "message": f"Running Surface Retopology {stage}"})
                result = run_process(
                    command,
                    cwd=layout.root,
                    timeout_seconds=self.config.inspection_timeout_seconds,
                    cancellation=self._cancellation,
                    controller=self._controller,
                    on_log=log,
                    on_progress=progress,
                )
                process_seconds[stage] = result.elapsed_seconds
        except CancelledError:
            cancelled = {
                "application": "Tora_MeshForge",
                "schema_version": "1.0",
                "operation": "surface_retopology",
                "status": "cancelled",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "input": str(input_path),
                "requested_output": str(output_path),
                "warnings": [],
                "errors": [],
            }
            self._write_json(layout.process_report, cancelled)
            report_destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(layout.process_report, report_destination)
            raise
        except (ProcessExecutionError, TimeoutError) as exc:
            failure = {
                "application": "Tora_MeshForge",
                "schema_version": "1.0",
                "operation": "surface_retopology",
                "status": "failure",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "input": {"path": str(input_path), "file_size_bytes": input_path.stat().st_size},
                "requested_output": str(output_path),
                "warnings": [],
                "errors": [{"type": type(exc).__name__, "message": str(exc)}],
            }
            self._write_json(layout.process_report, failure)
            report_destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(layout.process_report, report_destination)
            raise

        with topology_report_path.open("r", encoding="utf-8") as handle:
            topology = json.load(handle)
        with uv_report_path.open("r", encoding="utf-8") as handle:
            uv = json.load(handle)
        with bake_report_path.open("r", encoding="utf-8") as handle:
            bake = json.load(handle)
        merge_result = uv.get("segmentation", {}).get("constrained_merge", {})
        merge_succeeded = bool(
            merge_result.get("enabled")
            and merge_result.get("search_complete") is True
        )
        organization_succeeded = (
            not request.organize_uv_islands
            or uv.get("segmentation", {}).get("organization", {}).get("enabled") is True
        )
        passed = bool(
            topology.get("passed_basic_validation")
            and uv.get("passed")
            and merge_succeeded
            and organization_succeeded
            and bake.get("passed")
        )
        if not passed:
            raise RuntimeError("Surface Retopology returned without passing all validation stages.")
        warnings = ["Surface Retopology changes topology and reconstructs a closed surface; inspect thin and contacting parts."]
        if uv_retry_history:
            warnings.append(
                "The adaptive UV preliminary choice failed final validation; "
                f"a later {uv_retry_history[-1]['requested_regions']}-region attempt passed."
            )
        if merge_result.get("search_limited"):
            warnings.append(
                "UV merging exhausted the eligible candidate space at "
                f"{merge_result.get('produced_regions_after_repair', merge_result.get('produced_regions'))} regions."
            )
        bake_object_report: dict[str, Any] = {
            "basecolor": bake["basecolor"],
            "invalid_mask": bake["invalid_mask"],
            "invalid_pixel_ratio": bake["invalid_pixel_ratio"],
        }
        if bake.get("normal"):
            bake_object_report.update({
                "normal": bake["normal"],
                "invalid_normal_mask": bake["shape_normal"]["invalid_normal_mask"],
                "normal_invalid_pixel_ratio": bake["shape_normal"]["normal_invalid_pixel_ratio"],
            })
        report: dict[str, Any] = {
            "application": "Tora_MeshForge",
            "schema_version": "1.0",
            "operation": "surface_retopology",
            "status": "success",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "input": {"path": str(input_path), "file_size_bytes": input_path.stat().st_size},
            "settings": {
                "target_triangles": request.target_triangles,
                "texture_resolution": request.texture_resolution,
                "uv_margin_pixels": request.uv_margin_pixels,
                "voxel_divisions": request.voxel_divisions,
                "initial_uv_regions": request.uv_regions,
                "curvature_weight": request.curvature_weight,
                "adaptive_initial_regions": request.adaptive_initial_regions,
                "uv_target_regions": request.uv_target_regions,
                "maximum_chart_faces": request.maximum_chart_faces,
                "maximum_merge_trials": request.maximum_merge_trials,
                "maximum_merge_batch_size": request.maximum_merge_batch_size,
                "organize_uv_islands": request.organize_uv_islands,
                "bake_shape_normal": request.bake_shape_normal,
            },
            "source": topology["source"],
            "output": {
                **topology["result"],
                "path": str(output_path),
                "job_path": str(layout.output_model.resolve()),
                "file_size_bytes": layout.output_model.stat().st_size,
            },
            "surface": topology,
            "uv": uv,
            "bake": {
                **bake,
                "objects": [bake_object_report],
            },
            "material": {
                "metallic": 0.0,
                "roughness": 0.5,
                "normal_map": bool(bake.get("shape_normal", {}).get("enabled")),
            },
            "artifacts": {
                "uv_layout": str(uv_layout_path.resolve()),
                "uv_texture_layout": str(uv_texture_layout_path.resolve()),
            },
            "validation": {"passed": True, "checks": {
                "surface_rebuilt": topology["result"]["components"] == 1,
                "target_reached": topology["result"]["triangles"] <= request.target_triangles * 1.02,
                "region_uv_passed": uv["passed"],
                "uv_regions_coarsened": merge_succeeded,
                "uv_merge_search_complete": merge_succeeded,
                "uv_organized": organization_succeeded,
                "basecolor_transfer_passed": bake["passed"],
                "output_reloaded": bool(bake.get("reload_validation", {}).get("passed")),
                "shape_normal_transfer_passed": (
                    not request.bake_shape_normal
                    or bool(bake.get("shape_normal", {}).get("enabled") and bake["passed"])
                ),
            }, "failed_checks": [], "warnings": []},
            "timings": {key + "_seconds": round(value, 3) for key, value in process_seconds.items()},
            "warnings": warnings,
            "errors": [],
        }
        public_texture_directory = output_path.parent / f"{output_path.stem}_textures"
        public_texture_directory.mkdir(parents=True, exist_ok=True)
        for item in report["bake"]["objects"]:
            for field in ("basecolor", "invalid_mask", "normal", "invalid_normal_mask"):
                source_value = item.get(field)
                if not source_value:
                    continue
                source_artifact = Path(source_value)
                destination = public_texture_directory / source_artifact.name
                shutil.copy2(source_artifact, destination)
                item["job_" + field] = str(source_artifact)
                item[field] = str(destination.resolve())
        public_uv_layout = output_path.with_name(output_path.stem + ".uv-layout.png")
        public_uv_texture_layout = output_path.with_name(output_path.stem + ".uv-layout-texture.png")
        shutil.copy2(uv_layout_path, public_uv_layout)
        shutil.copy2(uv_texture_layout_path, public_uv_texture_layout)
        report["artifacts"]["job_uv_layout"] = report["artifacts"]["uv_layout"]
        report["artifacts"]["job_uv_texture_layout"] = report["artifacts"]["uv_texture_layout"]
        report["artifacts"]["uv_layout"] = str(public_uv_layout.resolve())
        report["artifacts"]["uv_texture_layout"] = str(public_uv_texture_layout.resolve())
        output_path.parent.mkdir(parents=True, exist_ok=True)
        report_destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(layout.output_model, output_path)

        previews_complete = True
        if _generate_evaluation:
            report["artifacts"]["source_previews"] = {}
            preview_modes = ("geometry", "mesh", "texture", "material")
            preview_views: tuple[tuple[str, float], ...] = (
                ("hero", -60.0),
                ("side", 30.0),
                ("back", 120.0),
            )
            required_preview_keys = {
                f"preview_{mode}" if view_name == "hero" else f"preview_{view_name}_{mode}"
                for view_name, _ in preview_views
                for mode in preview_modes
            }
            preview_entities: tuple[tuple[str, Path, dict[str, Any], str], ...] = (
                (
                    "source",
                    input_path,
                    report["artifacts"]["source_previews"],
                    f"{int(report['source']['triangles']):,}",
                ),
                (
                    "output",
                    output_path,
                    report["artifacts"],
                    f"{int(report['output']['triangles']):,}",
                ),
            )
            preview_total = len(preview_entities) * len(preview_views) * len(preview_modes) + 1
            preview_index = 0
            for entity_kind, entity_input, entity_artifacts, triangle_label in preview_entities:
                for view_name, azimuth in preview_views:
                    for mode in preview_modes:
                        self._cancellation.raise_if_cancelled()
                        preview_index += 1
                        fraction = 0.96 + (preview_index / preview_total) * 0.035
                        if entity_kind == "source":
                            filename = (
                                f"{output_path.stem}.source-preview-{mode}.png"
                                if view_name == "hero"
                                else f"{output_path.stem}.source-preview-{view_name}-{mode}.png"
                            )
                            label = f"SOURCE {view_name.upper()} {mode.upper()} - {triangle_label} TRIS"
                        else:
                            filename = (
                                f"{output_path.stem}.preview-{mode}.png"
                                if view_name == "hero"
                                else f"{output_path.stem}.preview-{view_name}-{mode}.png"
                            )
                            label = f"OUTPUT {view_name.upper()} {mode.upper()} - {triangle_label} TRIS"
                        preview_path = output_path.with_name(filename)
                        artifact_key = (
                            f"preview_{mode}" if view_name == "hero"
                            else f"preview_{view_name}_{mode}"
                        )
                        stage = f"single_preview_{entity_kind}_{view_name}_{mode}"
                        progress({
                            "stage": stage,
                            "progress": fraction,
                            "message": f"Rendering {entity_kind} {view_name} {mode} preview",
                        })
                        command: list[Any] = [
                            blender, "--background", "--factory-startup", "--disable-autoexec",
                            "--python", scripts / "render_preview.py", "--",
                            "--input", entity_input,
                            "--output", preview_path,
                            "--mode", mode,
                            "--azimuth-degrees", str(azimuth),
                            "--elevation-degrees", "18",
                            "--label", label,
                            "--frame-reference", input_path,
                        ]
                        if entity_kind == "source" and texture_path is not None:
                            command.extend(["--texture-override", texture_path])
                        try:
                            result = run_process(
                                command,
                                cwd=output_path.parent,
                                timeout_seconds=self.config.inspection_timeout_seconds,
                                cancellation=self._cancellation,
                                controller=self._controller,
                                on_log=log,
                                on_progress=progress,
                            )
                            process_seconds[stage] = result.elapsed_seconds
                            entity_artifacts[artifact_key] = str(preview_path.resolve())
                        except CancelledError:
                            report["status"] = "cancelled"
                            self._write_json(layout.process_report, report)
                            shutil.copy2(layout.process_report, report_destination)
                            raise
                        except Exception as exc:
                            report["errors"].append({
                                "stage": stage,
                                "type": type(exc).__name__,
                                "message": str(exc),
                            })
                            log(f"Single evaluation preview failed: {type(exc).__name__}: {exc}")

            self._cancellation.raise_if_cancelled()
            source_uv_texture_path = output_path.with_name(
                output_path.stem + ".source-uv-layout-texture.png"
            )
            progress({
                "stage": "single_source_uv_texture",
                "progress": 0.995,
                "message": "Rendering SOURCE UV over Base Color",
            })
            source_uv_command: list[Any] = [
                blender, "--background", "--factory-startup", "--disable-autoexec",
                "--python", scripts / "render_uv_layout.py", "--",
                "--input", input_path,
                "--output", source_uv_texture_path,
                "--active-uv",
                "--max-polygons", "25000",
                "--size", "1536",
                "--texture-opacity", "0.78",
                "--line-width", "1",
                "--line-opacity", "0.55",
            ]
            if texture_path is not None:
                source_uv_command.extend(["--texture", texture_path])
            else:
                source_uv_command.append("--texture-from-material")
            try:
                result = run_process(
                    source_uv_command,
                    cwd=output_path.parent,
                    timeout_seconds=self.config.inspection_timeout_seconds,
                    cancellation=self._cancellation,
                    controller=self._controller,
                    on_log=log,
                    on_progress=progress,
                )
                process_seconds["single_source_uv_texture"] = result.elapsed_seconds
                report["artifacts"]["source_previews"]["uv_texture_layout"] = str(
                    source_uv_texture_path.resolve()
                )
            except CancelledError:
                report["status"] = "cancelled"
                self._write_json(layout.process_report, report)
                shutil.copy2(layout.process_report, report_destination)
                raise
            except Exception as exc:
                report["errors"].append({
                    "stage": "single_source_uv_texture",
                    "type": type(exc).__name__,
                    "message": str(exc),
                })
                log(f"SOURCE UV-over-Base-Color preview failed: {type(exc).__name__}: {exc}")

            output_previews_complete = required_preview_keys.issubset(report["artifacts"])
            source_previews_complete = (required_preview_keys | {"uv_texture_layout"}).issubset(
                report["artifacts"]["source_previews"]
            )
            previews_complete = output_previews_complete and source_previews_complete
            report["validation"]["checks"]["result_previews_complete"] = output_previews_complete
            report["validation"]["checks"]["source_previews_complete"] = source_previews_complete
            if not previews_complete:
                report["warnings"].append(
                    "One or more result-evaluation previews could not be generated; the FBX was retained."
                )

        progress({
            "stage": "runtime_validation",
            "progress": 0.997,
            "message": "Running General Runtime readiness validation",
        })
        report["runtime_readiness"] = build_runtime_readiness(report)
        runtime_ready = bool(report["runtime_readiness"]["ready"])
        report["validation"]["checks"]["general_runtime_ready"] = runtime_ready
        report["validation"]["passed"] = runtime_ready and previews_complete
        report["validation"]["failed_checks"] = [
            key for key, value in report["validation"]["checks"].items() if not value
        ]
        report["status"] = (
            "failure" if not runtime_ready else "success" if previews_complete else "partial"
        )

        evaluation_complete = True
        if _generate_evaluation:
            evaluation_complete = False
            evaluation_path = output_path.with_name(output_path.stem + ".evaluation.html")
            try:
                evaluation_path.write_text(
                    build_single_evaluation_html(report, output_path.parent),
                    encoding="utf-8",
                    newline="\n",
                )
                report["artifacts"]["final_evaluation"] = str(evaluation_path.resolve())
                evaluation_complete = True
            except Exception as exc:
                report["errors"].append({
                    "stage": "final_evaluation",
                    "type": type(exc).__name__,
                    "message": str(exc),
                })
                report["warnings"].append(
                    "Result evaluation page generation failed; the FBX and previews were retained."
                )
                log(f"Single evaluation generation failed: {type(exc).__name__}: {exc}")
            report["validation"]["checks"]["final_evaluation_complete"] = evaluation_complete

        report["timings"] = {
            key + "_seconds": round(value, 3) for key, value in process_seconds.items()
        }
        required_checks_passed = runtime_ready and previews_complete and evaluation_complete
        report["validation"]["passed"] = required_checks_passed
        report["validation"]["failed_checks"] = [
            key for key, value in report["validation"]["checks"].items() if not value
        ]
        report["status"] = (
            "failure" if not runtime_ready else "success" if required_checks_passed else "partial"
        )
        self._write_json(layout.process_report, report)
        shutil.copy2(layout.process_report, report_destination)
        if not runtime_ready:
            raise RuntimeError(
                "General Runtime validation failed: "
                + ", ".join(report["runtime_readiness"]["failed_checks"])
            )
        progress({"stage": "complete", "progress": 1.0, "message": "Surface Retopology complete"})
        return ProcessingResult(output_path, report_destination, layout.root, report, logs)

    def triangle_sweep(
        self,
        request: TriangleSweepRequest,
        *,
        on_log: LogCallback | None = None,
        on_progress: ProgressCallback | None = None,
        _cancellation_token: CancellationToken | None = None,
    ) -> TriangleSweepResult:
        """Run Surface Retopology at multiple triangle targets and compare the results."""
        sweep_cancellation = self._activate_cancellation(_cancellation_token)
        input_path = request.input_path.expanduser().resolve()
        self._validate_input(input_path)
        texture_path = request.texture_path.expanduser().resolve() if request.texture_path else None
        if texture_path is not None:
            self._validate_texture(texture_path)
        blender = discover_blender(request.blender_path, self.config.blender_path)
        if blender is None:
            raise FileNotFoundError("Blender executable was not found. Select blender.exe or set BLENDER_PATH.")
        targets = parse_triangle_targets(request.triangle_targets)
        output_directory = request.output_directory.expanduser().resolve()
        output_directory.mkdir(parents=True, exist_ok=True)
        report_path = (
            request.report_path.expanduser().resolve()
            if request.report_path
            else output_directory / "triangle-sweep.report.json"
        )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        work_directory = (request.work_directory or self.config.work_directory).expanduser().resolve()
        logs: list[str] = []
        results: list[dict[str, Any]] = []
        comparison_path = output_directory / "surface-comparison.json"
        texture_comparison_path = output_directory / "texture-comparison.json"
        evaluation_path = output_directory / "final-evaluation.html"

        def log(message: str) -> None:
            logs.append(message)
            if on_log:
                on_log(message)

        def progress(stage: str, fraction: float, message: str) -> None:
            if on_progress:
                on_progress({"stage": stage, "progress": fraction, "message": message})

        report: dict[str, Any] = {
            "application": "Tora_MeshForge",
            "schema_version": "1.0",
            "operation": "triangle_sweep",
            "status": "running",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "input": {"path": str(input_path), "file_size_bytes": input_path.stat().st_size},
            "output_directory": str(output_directory),
            "settings": {
                "triangle_targets": list(targets),
                "texture_resolution": request.texture_resolution,
                "uv_margin_pixels": request.uv_margin_pixels,
                "voxel_divisions": request.voxel_divisions,
                "initial_uv_regions": request.uv_regions,
                "curvature_weight": request.curvature_weight,
                "adaptive_initial_regions": request.adaptive_initial_regions,
                "uv_target_regions": request.uv_target_regions,
                "maximum_chart_faces": request.maximum_chart_faces,
                "maximum_merge_trials": request.maximum_merge_trials,
                "maximum_merge_batch_size": request.maximum_merge_batch_size,
                "organize_uv_islands": request.organize_uv_islands,
                "comparison_samples": request.comparison_samples,
                "bake_shape_normal": request.bake_shape_normal,
            },
            "results": results,
            "comparison": None,
            "texture_comparison": None,
            "runtime_readiness": {
                "profile": "general_runtime",
                "all_candidates_ready": False,
                "ready_candidates": 0,
                "failed_candidates": 0,
            },
            "analysis": {},
            "artifacts": {"source_previews": {}},
            "validation": {"passed": False, "successful_candidates": 0, "failed_candidates": 0},
            "warnings": [
                "Triangle Sweep reports measured geometry, Base Color, and UV quality; choose the production target after visual review."
            ],
            "errors": [],
        }
        self._write_json(report_path, report)

        for index, target in enumerate(targets):
            sweep_cancellation.raise_if_cancelled()
            candidate_directory = output_directory / f"{target}-triangles"
            candidate_directory.mkdir(parents=True, exist_ok=True)
            candidate_output = candidate_directory / "result.fbx"
            candidate_report = candidate_directory / "report.json"
            progress(
                f"sweep_{target}_prepare",
                index / len(targets) * 0.80,
                f"[{index + 1}/{len(targets)}] Preparing {target:,}-triangle candidate",
            )

            def child_log(message: str, *, target_value: int = target) -> None:
                log(f"[{target_value:,}] {message}")

            def child_progress(
                update: dict[str, Any],
                *,
                candidate_index: int = index,
                target_value: int = target,
            ) -> None:
                local_fraction = max(0.0, min(1.0, float(update.get("progress", 0.0))))
                total_fraction = ((candidate_index + local_fraction) / len(targets)) * 0.80
                progress(
                    f"sweep_{target_value}_{update.get('stage', 'working')}",
                    total_fraction,
                    f"[{candidate_index + 1}/{len(targets)}] {target_value:,}: "
                    f"{update.get('message', update.get('stage', 'Working'))}",
                )

            try:
                child_result = self.surface_retopology(
                    SurfaceRetopologyRequest(
                        input_path=input_path,
                        output_path=candidate_output,
                        target_triangles=target,
                        blender_path=blender,
                        work_directory=work_directory,
                        report_path=candidate_report,
                        texture_path=texture_path,
                        texture_resolution=request.texture_resolution,
                        uv_margin_pixels=request.uv_margin_pixels,
                        voxel_divisions=request.voxel_divisions,
                        uv_regions=request.uv_regions,
                        curvature_weight=request.curvature_weight,
                        adaptive_initial_regions=request.adaptive_initial_regions,
                        uv_target_regions=request.uv_target_regions,
                        maximum_chart_faces=request.maximum_chart_faces,
                        maximum_merge_trials=request.maximum_merge_trials,
                        maximum_merge_batch_size=request.maximum_merge_batch_size,
                        organize_uv_islands=request.organize_uv_islands,
                        bake_shape_normal=request.bake_shape_normal,
                    ),
                    on_log=child_log,
                    on_progress=child_progress,
                    _cancellation_token=sweep_cancellation,
                    _generate_evaluation=False,
                )
            except CancelledError:
                report["status"] = "cancelled"
                report["analysis"] = summarize_sweep_results(results)
                self._write_json(report_path, report)
                raise
            except Exception as exc:
                error = {"type": type(exc).__name__, "message": str(exc)}
                results.append({
                    "target_triangles": target,
                    "status": "failure",
                    "output_directory": str(candidate_directory),
                    "output_path": str(candidate_output),
                    "report_path": str(candidate_report),
                    "error": error,
                })
                report["errors"].append({"target_triangles": target, **error})
                log(f"[{target:,}] Candidate failed: {type(exc).__name__}: {exc}")
                self._write_json(report_path, report)
                continue

            child = child_result.report
            uv = child["uv"]
            bake = child["bake"]
            result_record = {
                "target_triangles": target,
                "status": "success",
                "output_directory": str(candidate_directory),
                "output_path": str(candidate_output.resolve()),
                "report_path": str(candidate_report.resolve()),
                "output_triangles": int(child["output"]["triangles"]),
                "output_vertices": int(child["output"]["vertices"]),
                "uv_regions": int(uv["segmentation"]["produced_regions"]),
                "selected_initial_regions": int(
                    uv.get("final_validation_retry", {}).get(
                        "selected_regions",
                        uv["segmentation"].get("initial_region_sampling", {}).get(
                            "selected_regions", uv["segmentation"]["requested_regions"]
                        ),
                    )
                ),
                "nondegenerate_uv_ratio": float(uv["uv_area"]["nondegenerate_ratio"]),
                "uv_overlap_ratio": float(uv["uv_overlap"]["overlap_ratio"]),
                "uv_coverage_ratio": float(bake["uv_coverage_ratio"]),
                "invalid_basecolor_pixels": int(bake["invalid_pixels"]),
                "shape_normal": bake.get("shape_normal", {"enabled": False}),
                "runtime_readiness": child.get("runtime_readiness", {
                    "profile": "general_runtime",
                    "status": "fail",
                    "ready": False,
                    "failed_checks": ["runtime_readiness_missing"],
                }),
                "timings": child.get("timings", {}),
                "warnings": child.get("warnings", []),
                "artifacts": {
                    "uv_layout": child["artifacts"]["uv_layout"],
                    "uv_texture_layout": child["artifacts"]["uv_texture_layout"],
                },
            }
            bake_object = next(iter(bake.get("objects", [])), {})
            for field in ("normal", "invalid_normal_mask"):
                if bake_object.get(field):
                    result_record["artifacts"][field] = bake_object[field]
            results.append(result_record)
            self._write_json(report_path, report)

        successful = [item for item in results if item.get("status") == "success"]
        if not successful:
            report["status"] = "failure"
            report["analysis"] = summarize_sweep_results(results)
            report["validation"] = {
                "passed": False,
                "successful_candidates": 0,
                "failed_candidates": len(results),
            }
            self._write_json(report_path, report)
            raise RuntimeError(f"Triangle Sweep produced no successful candidates. See {report_path}")

        sweep_cancellation.raise_if_cancelled()
        progress("sweep_compare", 0.82, "Comparing candidate surfaces with the source")
        compare_command: list[Any] = [
            blender, "--background", "--factory-startup", "--disable-autoexec",
            "--python", Path(__file__).with_name("blender") / "compare_surface.py", "--",
            "--source", input_path,
            "--output", comparison_path,
            "--samples", str(request.comparison_samples),
        ]
        compare_command.extend(
            f"{item['target_triangles']}={item['output_path']}" for item in successful
        )
        try:
            run_process(
                compare_command,
                cwd=output_directory,
                timeout_seconds=self.config.inspection_timeout_seconds,
                cancellation=self._cancellation,
                controller=self._controller,
                on_log=log,
                on_progress=on_progress,
            )
            with comparison_path.open("r", encoding="utf-8") as handle:
                comparison = json.load(handle)
            report["comparison"] = comparison
            for item in successful:
                item["surface_distance"] = comparison["results"][str(item["target_triangles"])]
        except CancelledError:
            report["status"] = "cancelled"
            report["analysis"] = summarize_sweep_results(results)
            self._write_json(report_path, report)
            raise
        except Exception as exc:
            error = {"type": type(exc).__name__, "message": str(exc)}
            report["errors"].append({"stage": "surface_comparison", **error})
            report["warnings"].append("Surface-distance comparison failed; candidate outputs were retained.")
            log(f"Surface comparison failed: {type(exc).__name__}: {exc}")

        sweep_cancellation.raise_if_cancelled()
        progress("sweep_texture_compare", 0.84, "Comparing baked Base Color with the source surface")
        texture_compare_command: list[Any] = [
            blender, "--background", "--factory-startup", "--disable-autoexec",
            "--python", Path(__file__).with_name("blender") / "compare_texture.py", "--",
            "--source", input_path,
            "--output", texture_comparison_path,
            "--samples", str(request.comparison_samples),
        ]
        if texture_path is not None:
            texture_compare_command.extend(["--source-texture", texture_path])
        texture_compare_command.extend(
            f"{item['target_triangles']}={item['output_path']}" for item in successful
        )
        try:
            run_process(
                texture_compare_command,
                cwd=output_directory,
                timeout_seconds=self.config.inspection_timeout_seconds,
                cancellation=self._cancellation,
                controller=self._controller,
                on_log=log,
                on_progress=on_progress,
            )
            with texture_comparison_path.open("r", encoding="utf-8") as handle:
                texture_comparison = json.load(handle)
            report["texture_comparison"] = texture_comparison
            for item in successful:
                item["texture_quality"] = texture_comparison["results"][str(item["target_triangles"])]
        except CancelledError:
            report["status"] = "cancelled"
            report["analysis"] = summarize_sweep_results(results)
            self._write_json(report_path, report)
            raise
        except Exception as exc:
            error = {"type": type(exc).__name__, "message": str(exc)}
            report["errors"].append({"stage": "texture_comparison", **error})
            report["warnings"].append(
                "Base Color comparison failed; surface measurements and candidate outputs were retained."
            )
            log(f"Texture comparison failed: {type(exc).__name__}: {exc}")

        preview_modes = ("geometry", "mesh", "texture", "material")
        preview_views: tuple[tuple[str, float], ...] = (
            ("hero", -60.0),
            ("side", 30.0),
            ("back", 120.0),
        )
        preview_total = (len(successful) + 1) * len(preview_modes) * len(preview_views)
        preview_index = 0
        scripts = Path(__file__).with_name("blender")
        source_triangles = report.get("comparison", {}).get("source_triangles")
        source_label_triangles = f"{int(source_triangles):,}" if source_triangles is not None else "SOURCE"
        source_bounds = report.get("comparison", {}).get("source_bounds", {})
        frame_arguments: list[Any] = []
        if source_bounds.get("minimum") and source_bounds.get("maximum"):
            frame_arguments = [
                "--frame-min", *(str(value) for value in source_bounds["minimum"]),
                "--frame-max", *(str(value) for value in source_bounds["maximum"]),
            ]
        preview_entities: list[dict[str, Any]] = [{
            "kind": "source",
            "target_triangles": "source",
            "input_path": input_path,
            "output_directory": output_directory,
            "actual_triangles": source_label_triangles,
            "artifacts": report["artifacts"]["source_previews"],
        }]
        preview_entities.extend({
            "kind": "candidate",
            "target_triangles": item["target_triangles"],
            "input_path": Path(item["output_path"]),
            "output_directory": Path(item["output_directory"]),
            "actual_triangles": f"{int(item['output_triangles']):,}",
            "artifacts": item["artifacts"],
        } for item in successful)
        for entity in preview_entities:
            artifacts = entity["artifacts"]
            entity_directory = Path(entity["output_directory"])
            for view_name, azimuth in preview_views:
                for mode in preview_modes:
                    sweep_cancellation.raise_if_cancelled()
                    preview_index += 1
                    fraction = 0.86 + (preview_index / max(1, preview_total)) * 0.13
                    if entity["kind"] == "source":
                        filename = (
                            f"source-preview-{mode}.png" if view_name == "hero"
                            else f"source-preview-{view_name}-{mode}.png"
                        )
                        label = f"SOURCE {view_name.upper()} {mode.upper()} - {entity['actual_triangles']} TRIS"
                    else:
                        filename = (
                            f"preview-{mode}.png" if view_name == "hero"
                            else f"preview-{view_name}-{mode}.png"
                        )
                        label = f"{view_name.upper()} {mode.upper()} - {entity['actual_triangles']} TRIS"
                    preview_path = entity_directory / filename
                    artifact_key = (
                        f"preview_{mode}" if view_name == "hero"
                        else f"preview_{view_name}_{mode}"
                    )
                    progress(
                        f"sweep_preview_{entity['target_triangles']}_{view_name}_{mode}",
                        fraction,
                        f"Rendering {entity['target_triangles']} {view_name} {mode} preview",
                    )
                    command: list[Any] = [
                        blender, "--background", "--factory-startup", "--disable-autoexec",
                        "--python", scripts / "render_preview.py", "--",
                        "--input", Path(entity["input_path"]),
                        "--output", preview_path,
                        "--mode", mode,
                        "--azimuth-degrees", str(azimuth),
                        "--elevation-degrees", "18",
                        "--label", label,
                        *frame_arguments,
                    ]
                    if entity["kind"] == "source" and texture_path is not None:
                        command.extend(["--texture-override", texture_path])
                    try:
                        run_process(
                            command,
                            cwd=output_directory,
                            timeout_seconds=self.config.inspection_timeout_seconds,
                            cancellation=self._cancellation,
                            controller=self._controller,
                            on_log=log,
                            on_progress=on_progress,
                        )
                        artifacts[artifact_key] = str(preview_path.resolve())
                    except CancelledError:
                        report["status"] = "cancelled"
                        report["analysis"] = summarize_sweep_results(results)
                        self._write_json(report_path, report)
                        raise
                    except Exception as exc:
                        error = {"type": type(exc).__name__, "message": str(exc)}
                        report["errors"].append({
                            "stage": f"preview_{view_name}_{mode}",
                            "target_triangles": entity["target_triangles"],
                            **error,
                        })
                        log(
                            f"[{entity['target_triangles']}] {view_name} {mode} preview failed: "
                            f"{type(exc).__name__}: {exc}"
                        )

        sweep_cancellation.raise_if_cancelled()
        source_uv_texture_path = output_directory / "source-uv-layout-texture.png"
        progress(
            "sweep_source_uv_texture",
            0.992,
            "Rendering SOURCE UV over Base Color",
        )
        source_uv_command: list[Any] = [
            blender, "--background", "--factory-startup", "--disable-autoexec",
            "--python", scripts / "render_uv_layout.py", "--",
            "--input", input_path,
            "--output", source_uv_texture_path,
            "--active-uv",
            "--max-polygons", "25000",
            "--size", "1536",
            "--texture-opacity", "0.78",
            "--line-width", "1",
            "--line-opacity", "0.55",
        ]
        if texture_path is not None:
            source_uv_command.extend(["--texture", texture_path])
        else:
            source_uv_command.append("--texture-from-material")
        try:
            run_process(
                source_uv_command,
                cwd=output_directory,
                timeout_seconds=self.config.inspection_timeout_seconds,
                cancellation=self._cancellation,
                controller=self._controller,
                on_log=log,
                on_progress=on_progress,
            )
            report["artifacts"]["source_previews"]["uv_texture_layout"] = str(
                source_uv_texture_path.resolve()
            )
        except CancelledError:
            report["status"] = "cancelled"
            report["analysis"] = summarize_sweep_results(results)
            self._write_json(report_path, report)
            raise
        except Exception as exc:
            error = {"type": type(exc).__name__, "message": str(exc)}
            report["errors"].append({"stage": "source_uv_texture", **error})
            report["warnings"].append(
                "SOURCE UV-over-Base-Color preview failed; candidate outputs were retained."
            )
            log(f"SOURCE UV-over-Base-Color preview failed: {type(exc).__name__}: {exc}")

        failed_count = sum(item.get("status") != "success" for item in results)
        required_preview_keys = {
            f"preview_{mode}" if view_name == "hero" else f"preview_{view_name}_{mode}"
            for view_name, _ in preview_views
            for mode in preview_modes
        }
        candidate_previews_complete = all(
            required_preview_keys.issubset(item["artifacts"]) for item in successful
        )
        source_previews_complete = (required_preview_keys | {"uv_texture_layout"}).issubset(
            report["artifacts"]["source_previews"]
        )
        previews_complete = candidate_previews_complete and source_previews_complete
        comparison_complete = report["comparison"] is not None
        texture_comparison_complete = report["texture_comparison"] is not None
        runtime_ready_count = sum(
            bool(item.get("runtime_readiness", {}).get("ready")) for item in successful
        )
        runtime_readiness_complete = runtime_ready_count == len(successful)
        report["runtime_readiness"] = {
            "profile": "general_runtime",
            "all_candidates_ready": runtime_readiness_complete,
            "ready_candidates": runtime_ready_count,
            "failed_candidates": len(successful) - runtime_ready_count,
        }
        base_passed = (
            failed_count == 0
            and previews_complete
            and comparison_complete
            and texture_comparison_complete
            and runtime_readiness_complete
        )
        report["status"] = "success" if base_passed else "partial"
        report["analysis"] = summarize_sweep_results(results)
        evaluation_complete = False
        try:
            evaluation_path.write_text(
                build_sweep_evaluation_html(report, output_directory),
                encoding="utf-8",
                newline="\n",
            )
            report["artifacts"]["final_evaluation"] = str(evaluation_path.resolve())
            evaluation_complete = True
        except Exception as exc:
            error = {"type": type(exc).__name__, "message": str(exc)}
            report["errors"].append({"stage": "final_evaluation", **error})
            report["warnings"].append(
                "Final evaluation page generation failed; candidate outputs were retained."
            )
            log(f"Final evaluation generation failed: {type(exc).__name__}: {exc}")
        passed = base_passed and evaluation_complete
        report["status"] = "success" if passed else "partial"
        report["validation"] = {
            "passed": passed,
            "successful_candidates": len(successful),
            "failed_candidates": failed_count,
            "surface_comparison_complete": comparison_complete,
            "texture_comparison_complete": texture_comparison_complete,
            "runtime_readiness_complete": runtime_readiness_complete,
            "previews_complete": previews_complete,
            "source_previews_complete": source_previews_complete,
            "final_evaluation_complete": evaluation_complete,
        }
        self._write_json(report_path, report)
        progress("sweep_complete", 1.0, "Triangle Sweep complete")
        return TriangleSweepResult(output_directory, report_path, report, logs)

    def _validate_input(self, path: Path) -> None:
        if not path.is_file():
            raise FileNotFoundError(f"Input model does not exist: {path}")
        if path.suffix.lower() not in SUPPORTED_INPUTS:
            raise ValueError(f"Unsupported input format: {path.suffix}")
        limit = self.config.maximum_source_size_mb * 1024 * 1024
        if path.stat().st_size > limit:
            raise ValueError(f"Input exceeds the configured {self.config.maximum_source_size_mb} MB limit.")

    @staticmethod
    def _validate_roundtrip_output(input_path: Path, output_path: Path) -> None:
        if output_path.suffix.lower() != ".fbx":
            raise ValueError("Milestone 3 static round-trip output must use the .fbx extension.")
        if input_path == output_path:
            raise ValueError("Output path must differ from the source model path.")
        if output_path.parent.exists() and not output_path.parent.is_dir():
            raise ValueError(f"Output parent is not a directory: {output_path.parent}")

    @staticmethod
    def _validate_texture(path: Path) -> None:
        if not path.is_file():
            raise FileNotFoundError(f"Texture override does not exist: {path}")
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".tga", ".bmp", ".exr", ".webp"}:
            raise ValueError(f"Unsupported texture override format: {path.suffix}")

    @staticmethod
    def _write_json(path: Path, data: dict[str, Any]) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        temporary.replace(path)

    @staticmethod
    def _build_warnings(report: dict[str, Any]) -> list[str]:
        features = report.get("features", {})
        textures = report.get("textures", {})
        warnings: list[str] = []
        if features.get("armature"):
            warnings.append("Armatures are detected; rig preservation is outside the MVP scope.")
        if features.get("animation"):
            warnings.append("Animation is detected; processing must remain disabled in the MVP.")
        if features.get("shape_keys"):
            warnings.append("Shape keys are detected; processing must remain disabled in the MVP.")
        if textures.get("missing_files"):
            warnings.append("One or more external texture files could not be resolved by Blender.")
        if int(report.get("geometry", {}).get("triangles", 0)) > 2_000_000:
            warnings.append("The source mesh is very dense; later processing may require substantial memory and time.")
        return warnings
