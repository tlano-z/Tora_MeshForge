from pathlib import Path

from tora_meshforge.cli import main


def test_validate_accepts_minimum_inspection_report(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    report.write_text(
        '{"application":"Tora_MeshForge","schema_version":"1.0","status":"success",'
        '"source":{},"geometry":{},"textures":{},"devices":{}}',
        encoding="utf-8",
    )
    assert main(["validate", "--report", str(report)]) == 0


def test_validate_rejects_missing_fields(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    report.write_text("{}", encoding="utf-8")
    assert main(["validate", "--report", str(report)]) == 1


def test_validate_accepts_roundtrip_report(tmp_path: Path) -> None:
    report = tmp_path / "roundtrip.json"
    report.write_text(
        '{"application":"Tora_MeshForge","schema_version":"1.0","operation":"static_fbx_round_trip",'
        '"status":"success","input":{},"source":{},"output":{},"validation":{"passed":true}}',
        encoding="utf-8",
    )
    assert main(["validate", "--report", str(report)]) == 0


def test_validate_accepts_runtime_rebuild_report(tmp_path: Path) -> None:
    report = tmp_path / "runtime-rebuild.json"
    report.write_text(
        '{"application":"Tora_MeshForge","schema_version":"1.0","operation":"runtime_rebuild",'
        '"status":"success","input":{},"source":{},"output":{},"validation":{"passed":true}}',
        encoding="utf-8",
    )
    assert main(["validate", "--report", str(report)]) == 0


def test_validate_accepts_surface_retopology_report(tmp_path: Path) -> None:
    report = tmp_path / "surface-retopology.json"
    report.write_text(
        '{"application":"Tora_MeshForge","schema_version":"1.0","operation":"surface_retopology",'
        '"status":"success","input":{},"source":{},"output":{},"validation":{"passed":true},'
        '"runtime_readiness":{"profile":"general_runtime","status":"pass","ready":true}}',
        encoding="utf-8",
    )
    assert main(["validate", "--report", str(report)]) == 0


def test_validate_accepts_triangle_sweep_runtime_readiness(tmp_path: Path) -> None:
    report = tmp_path / "triangle-sweep.json"
    report.write_text(
        '{"application":"Tora_MeshForge","schema_version":"1.0","operation":"triangle_sweep",'
        '"status":"success","input":{},"output_directory":"out","settings":{},"results":[],'
        '"validation":{"passed":true},'
        '"runtime_readiness":{"profile":"general_runtime","all_candidates_ready":true}}',
        encoding="utf-8",
    )
    assert main(["validate", "--report", str(report)]) == 0
