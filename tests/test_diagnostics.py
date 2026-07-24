from __future__ import annotations

from pathlib import Path

from tora_meshforge.config import AppConfig
from tora_meshforge.diagnostics import collect_environment_diagnostics


def test_diagnostics_reports_missing_blender_without_losing_other_checks(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr("tora_meshforge.diagnostics.discover_blender", lambda *_args: None)
    report = collect_environment_diagnostics(AppConfig(work_directory=tmp_path))
    checks = {item["id"]: item for item in report["checks"]}

    assert report["status"] == "fail"
    assert report["ready"] is False
    assert checks["blender"]["status"] == "fail"
    assert checks["work_directory"]["status"] == "pass"
    assert checks["package_assets"]["status"] == "pass"


def test_diagnostics_accepts_supported_blender(
    tmp_path: Path,
    monkeypatch,
) -> None:
    blender = tmp_path / "blender.exe"
    blender.write_bytes(b"placeholder")
    monkeypatch.setattr("tora_meshforge.diagnostics.discover_blender", lambda *_args: blender)
    monkeypatch.setattr("tora_meshforge.diagnostics._blender_version", lambda _path: ("4.5.3", None))

    report = collect_environment_diagnostics(AppConfig(work_directory=tmp_path / "work"))
    checks = {item["id"]: item for item in report["checks"]}

    assert report["ready"] is True
    assert checks["blender"]["status"] == "pass"
    assert checks["blender"]["details"]["version"] == "4.5.3"
