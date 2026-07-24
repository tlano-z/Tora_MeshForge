from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from tora_meshforge import __version__
from tora_meshforge.config import load_config
from tora_meshforge.diagnostics import collect_environment_diagnostics
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tora-meshforge", description="Tora_MeshForge command-line interface")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--config", type=Path, help="TOML configuration file")
    subparsers = parser.add_subparsers(dest="command", required=True)
    doctor = subparsers.add_parser("doctor", help="Verify Python, PySide6, Blender, work-directory, and package readiness")
    doctor.add_argument("--blender-path", type=Path)
    doctor.add_argument("--workdir", type=Path)
    doctor.add_argument("--report", type=Path, help="Write the complete diagnostic report as JSON")
    doctor.add_argument("--json", action="store_true", help="Print JSON instead of a human-readable summary")
    inspect = subparsers.add_parser("inspect", help="Inspect a model with Blender")
    inspect.add_argument("--input", type=Path, required=True)
    inspect.add_argument("--report", type=Path)
    inspect.add_argument("--blender-path", type=Path)
    inspect.add_argument("--workdir", type=Path)
    inspect.add_argument("--texture", type=Path, help="Use this image when the model has exactly one unresolved texture")
    inspect.add_argument("--verbose", action="store_true")
    process = subparsers.add_parser("process", help="Run a validated static round trip, Fast Optimize, or Runtime Rebuild job")
    process.add_argument(
        "--mode",
        choices=("static-roundtrip", "fast-optimize", "runtime-rebuild", "surface-retopology"),
        default="static-roundtrip",
    )
    process.add_argument("--input", type=Path, required=True)
    process.add_argument("--output", type=Path, required=True)
    process.add_argument("--report", type=Path)
    process.add_argument("--blender-path", type=Path)
    process.add_argument("--workdir", type=Path)
    process.add_argument("--texture", type=Path, help="Use this image when the model has exactly one unresolved texture")
    process.add_argument("--target-triangles", type=int, default=50_000)
    process.add_argument("--preserve-small-parts", action=argparse.BooleanOptionalAction, default=True)
    process.add_argument("--texture-resolution-mode", choices=("auto", "match-source", "manual"), default="auto")
    process.add_argument("--texture-resolution", type=int, choices=(512, 1024, 2048, 4096, 8192))
    process.add_argument("--maximum-texture-resolution", type=int, choices=(512, 1024, 2048, 4096, 8192), default=4096)
    process.add_argument("--uv-mode", choices=("consolidated", "angle", "smart"), default="consolidated")
    process.add_argument("--uv-margin-pixels", type=int, default=4)
    process.add_argument("--voxel-divisions", type=int, default=768)
    process.add_argument("--uv-regions", type=int, default=192)
    process.add_argument("--curvature-weight", type=float, default=24.0)
    process.add_argument("--adaptive-initial-regions", action=argparse.BooleanOptionalAction, default=True)
    process.add_argument("--uv-target-regions", type=int, default=1)
    process.add_argument("--maximum-chart-faces", type=int, default=0, help="0 disables the face-count cap")
    process.add_argument("--maximum-merge-trials", type=int, default=0, help="0 searches until no safe candidates remain")
    process.add_argument("--maximum-merge-batch-size", type=int, default=128)
    process.add_argument("--organize-uv-islands", action=argparse.BooleanOptionalAction, default=True)
    process.add_argument(
        "--shape-normal",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Bake the dense-source to reduced-surface tangent-space Normal map",
    )
    process.add_argument("--verbose", action="store_true")
    sweep = subparsers.add_parser(
        "sweep",
        help="Run Surface Retopology for multiple triangle targets and compare geometry, Base Color, and UV quality",
    )
    sweep.add_argument("--input", type=Path, required=True)
    sweep.add_argument("--output-directory", type=Path, required=True)
    sweep.add_argument("--report", type=Path)
    sweep.add_argument("--blender-path", type=Path)
    sweep.add_argument("--workdir", type=Path)
    sweep.add_argument("--texture", type=Path, help="Use this image when the model has exactly one unresolved texture")
    sweep.add_argument(
        "--triangle-targets",
        nargs="+",
        default=[str(value) for value in DEFAULT_TRIANGLE_SWEEP_TARGETS],
        metavar="TRIANGLES",
        help="Triangle targets such as: 50000 25000 10000 5000",
    )
    sweep.add_argument("--texture-resolution", type=int, choices=(512, 1024, 2048, 4096), default=2048)
    sweep.add_argument("--uv-margin-pixels", type=int, default=4)
    sweep.add_argument("--voxel-divisions", type=int, default=768)
    sweep.add_argument("--uv-regions", type=int, default=192)
    sweep.add_argument("--curvature-weight", type=float, default=24.0)
    sweep.add_argument("--adaptive-initial-regions", action=argparse.BooleanOptionalAction, default=True)
    sweep.add_argument("--uv-target-regions", type=int, default=1)
    sweep.add_argument("--maximum-chart-faces", type=int, default=0)
    sweep.add_argument("--maximum-merge-trials", type=int, default=0)
    sweep.add_argument("--maximum-merge-batch-size", type=int, default=128)
    sweep.add_argument("--organize-uv-islands", action=argparse.BooleanOptionalAction, default=True)
    sweep.add_argument(
        "--shape-normal",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Bake and connect a shape-difference Normal map for every candidate",
    )
    sweep.add_argument("--comparison-samples", type=int, default=25_000)
    sweep.add_argument("--verbose", action="store_true")
    validate = subparsers.add_parser("validate", help="Validate an inspection report")
    validate.add_argument("--report", type=Path, required=True)
    return parser


def _validate_report(path: Path) -> int:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    common = {"application", "schema_version", "status"}
    if data.get("operation") == "triangle_sweep":
        required = common | {"input", "output_directory", "settings", "results", "validation"}
        if data.get("status") == "success":
            required.add("runtime_readiness")
    elif data.get("operation") in {"static_fbx_round_trip", "fast_optimize", "runtime_rebuild", "surface_retopology"}:
        required = common | {"input"}
        if data.get("status") == "success":
            required |= {"source", "output", "validation"}
            if data.get("operation") == "surface_retopology":
                required.add("runtime_readiness")
    else:
        required = common | {"source", "geometry", "textures", "devices"}
    missing = sorted(required - data.keys())
    if missing:
        print(f"Invalid report; missing: {', '.join(missing)}", file=sys.stderr)
        return 1
    print(f"Valid Tora_MeshForge report: {path.resolve()}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "validate":
        return _validate_report(args.report)
    config = load_config(args.config)
    if args.command == "doctor":
        report = collect_environment_diagnostics(
            config,
            blender_path=args.blender_path,
            work_directory=args.workdir,
        )
        if args.report:
            destination = args.report.expanduser().resolve()
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print(f"Tora_MeshForge {report['application_version']} environment diagnostics")
            for check in report["checks"]:
                print(f"[{check['status'].upper():7}] {check['summary']}")
            print("READY" if report["ready"] else "NOT READY")
            if args.report:
                print(f"Report: {args.report.expanduser().resolve()}")
        return 0 if report["ready"] else 1
    pipeline = Pipeline(config)
    progress = (lambda event: print(f"[{event.get('stage')}] {event.get('message')}", file=sys.stderr)) if args.verbose else None
    logging = (lambda line: print(line, file=sys.stderr)) if args.verbose else None
    if args.command == "sweep":
        try:
            result = pipeline.triangle_sweep(
                TriangleSweepRequest(
                    input_path=args.input,
                    output_directory=args.output_directory,
                    triangle_targets=parse_triangle_targets(args.triangle_targets),
                    blender_path=args.blender_path,
                    work_directory=args.workdir,
                    report_path=args.report,
                    texture_path=args.texture,
                    texture_resolution=args.texture_resolution,
                    uv_margin_pixels=args.uv_margin_pixels,
                    voxel_divisions=args.voxel_divisions,
                    uv_regions=args.uv_regions,
                    curvature_weight=args.curvature_weight,
                    adaptive_initial_regions=args.adaptive_initial_regions,
                    uv_target_regions=args.uv_target_regions,
                    maximum_chart_faces=args.maximum_chart_faces,
                    maximum_merge_trials=args.maximum_merge_trials,
                    maximum_merge_batch_size=args.maximum_merge_batch_size,
                    organize_uv_islands=args.organize_uv_islands,
                    comparison_samples=args.comparison_samples,
                    bake_shape_normal=args.shape_normal,
                ),
                on_progress=progress,
                on_log=logging,
            )
        except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        print(json.dumps({
            "operation": "triangle_sweep",
            "output_directory": str(result.output_directory),
            "report": str(result.report_path),
            "status": result.report["status"],
            "analysis": result.report["analysis"],
            "runtime_readiness": result.report.get("runtime_readiness"),
        }, indent=2))
        return 0 if result.report["validation"]["passed"] else 1
    if args.command == "process":
        try:
            if args.mode == "surface-retopology":
                result = pipeline.surface_retopology(
                    SurfaceRetopologyRequest(
                        input_path=args.input,
                        output_path=args.output,
                        target_triangles=args.target_triangles,
                        blender_path=args.blender_path,
                        work_directory=args.workdir,
                        report_path=args.report,
                        texture_path=args.texture,
                        texture_resolution=args.texture_resolution or min(2048, args.maximum_texture_resolution),
                        uv_margin_pixels=args.uv_margin_pixels,
                        voxel_divisions=args.voxel_divisions,
                        uv_regions=args.uv_regions,
                        curvature_weight=args.curvature_weight,
                        adaptive_initial_regions=args.adaptive_initial_regions,
                        uv_target_regions=args.uv_target_regions,
                        maximum_chart_faces=args.maximum_chart_faces,
                        maximum_merge_trials=args.maximum_merge_trials,
                        maximum_merge_batch_size=args.maximum_merge_batch_size,
                        organize_uv_islands=args.organize_uv_islands,
                        bake_shape_normal=args.shape_normal,
                    ),
                    on_log=logging,
                    on_progress=progress,
                )
            elif args.mode == "runtime-rebuild":
                result = pipeline.runtime_rebuild(
                    RuntimeRebuildRequest(
                        args.input,
                        args.output,
                        args.target_triangles,
                        args.blender_path,
                        args.workdir,
                        args.report,
                        args.texture,
                        args.texture_resolution_mode,
                        args.texture_resolution,
                        args.maximum_texture_resolution,
                        args.uv_mode,
                        args.uv_margin_pixels,
                        args.preserve_small_parts,
                    ),
                    on_log=logging,
                    on_progress=progress,
                )
            elif args.mode == "fast-optimize":
                result = pipeline.optimize(
                    FastOptimizeRequest(
                        args.input,
                        args.output,
                        args.target_triangles,
                        args.blender_path,
                        args.workdir,
                        args.report,
                        args.texture,
                        args.preserve_small_parts,
                    ),
                    on_log=logging,
                    on_progress=progress,
                )
            else:
                result = pipeline.run(
                    RoundTripRequest(args.input, args.output, args.blender_path, args.workdir, args.report, args.texture),
                    on_log=logging,
                    on_progress=progress,
                )
        except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
            print(f"Processing failed: {exc}", file=sys.stderr)
            return 1
        print(json.dumps({
            "operation": result.report["operation"],
            "output": str(result.output_path),
            "report": str(result.report_path),
            "validation_passed": result.report["validation"]["passed"],
            "source_triangles": result.report["source"]["triangles"],
            "output_triangles": result.report["output"]["triangles"],
            "runtime_readiness": result.report.get("runtime_readiness"),
            "warnings": result.report.get("warnings", []),
        }, ensure_ascii=False, indent=2))
        return 0
    try:
        result = pipeline.inspect(
            InspectionRequest(args.input, args.blender_path, args.workdir, args.report, args.texture),
            on_log=logging,
            on_progress=progress,
        )
    except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
        print(f"Inspection failed: {exc}", file=sys.stderr)
        return 1
    geometry = result.report["geometry"]
    recommendation = result.report["recommendation"]
    print(json.dumps({
        "report": str(result.report_path),
        "triangles": geometry["triangles"],
        "vertices": geometry["vertices"],
        "recommended_target_triangles": recommendation["target_triangles"],
        "recommended_texture_resolution": recommendation["texture_resolution"],
        "warnings": result.report.get("warnings", []),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
