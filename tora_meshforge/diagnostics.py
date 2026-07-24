from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import tempfile
from typing import Any

from tora_meshforge import __version__
from tora_meshforge.config import AppConfig
from tora_meshforge.utils.executable_discovery import discover_blender


MINIMUM_PYTHON = (3, 11)
MINIMUM_BLENDER = (4, 2)
MINIMUM_PYSIDE = (6, 6)
REQUIRED_BLENDER_SCRIPTS = (
    "inspect_scene.py",
    "roundtrip_scene.py",
    "optimize_scene.py",
    "runtime_rebuild_scene.py",
    "retopology_bake_probe.py",
)


def _version_tuple(value: str) -> tuple[int, ...]:
    return tuple(int(item) for item in re.findall(r"\d+", value)[:3])


def _check(identifier: str, status: str, summary: str, **details: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": identifier,
        "status": status,
        "passed": status != "fail",
        "summary": summary,
    }
    if details:
        result["details"] = details
    return result


def _python_check() -> dict[str, Any]:
    current = sys.version_info[:3]
    passed = current >= MINIMUM_PYTHON
    return _check(
        "python",
        "pass" if passed else "fail",
        f"Python {platform.python_version()}" + (" is supported." if passed else " is too old."),
        version=platform.python_version(),
        executable=str(Path(sys.executable).resolve()),
        minimum=".".join(map(str, MINIMUM_PYTHON)),
        virtual_environment=sys.prefix != sys.base_prefix,
    )


def _platform_check() -> dict[str, Any]:
    windows = os.name == "nt"
    return _check(
        "platform",
        "pass" if windows else "warning",
        "Windows runtime detected." if windows else "This platform is not part of the Windows-first release target.",
        system=platform.system(),
        release=platform.release(),
        machine=platform.machine(),
    )


def _pyside_check() -> dict[str, Any]:
    try:
        from tora_meshforge.gui.app import prepare_windows_dll_search

        prepare_windows_dll_search()
        import PySide6
        from PySide6.QtCore import qVersion
    except (ImportError, OSError) as exc:
        return _check("pyside6", "fail", f"PySide6 could not be loaded: {exc}")
    version = str(PySide6.__version__)
    parsed = _version_tuple(version)
    passed = MINIMUM_PYSIDE <= parsed < (7,)
    return _check(
        "pyside6",
        "pass" if passed else "fail",
        f"PySide6 {version} with Qt {qVersion()}" + (" is supported." if passed else " is outside the supported range."),
        version=version,
        qt_version=qVersion(),
        supported_range=">=6.6,<7",
    )


def _blender_version(path: Path) -> tuple[str | None, str | None]:
    try:
        completed = subprocess.run(
            [str(path), "--background", "--factory-startup", "--disable-autoexec", "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, str(exc)
    output = "\n".join(part for part in (completed.stdout, completed.stderr) if part).strip()
    if completed.returncode != 0:
        return None, output or f"Blender exited with code {completed.returncode}."
    match = re.search(r"\bBlender\s+(\d+(?:\.\d+){1,2})", output)
    return (match.group(1) if match else None), (None if match else "Blender version could not be parsed.")


def _blender_check(config: AppConfig, explicit_path: Path | None) -> dict[str, Any]:
    blender = discover_blender(explicit_path, config.blender_path)
    if blender is None:
        return _check(
            "blender",
            "fail",
            "Blender was not found. Install Blender 4.2 LTS or newer, select blender.exe, or set BLENDER_PATH.",
            minimum=".".join(map(str, MINIMUM_BLENDER)),
        )
    version, error = _blender_version(blender)
    if error:
        return _check("blender", "fail", f"Blender could not be verified: {error}", path=str(blender))
    parsed = _version_tuple(version or "")
    passed = parsed >= MINIMUM_BLENDER
    return _check(
        "blender",
        "pass" if passed else "fail",
        f"Blender {version}" + (" is supported." if passed else " is too old."),
        path=str(blender),
        version=version,
        minimum=".".join(map(str, MINIMUM_BLENDER)),
    )


def _work_directory_check(path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    try:
        resolved.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix="tmf-doctor-", suffix=".tmp", dir=resolved, delete=False) as handle:
            probe = Path(handle.name)
            handle.write(b"Tora_MeshForge")
        probe.unlink()
    except OSError as exc:
        return _check("work_directory", "fail", f"Work directory is not writable: {exc}", path=str(resolved))
    return _check("work_directory", "pass", "Work directory is writable.", path=str(resolved))


def _package_assets_check() -> dict[str, Any]:
    directory = Path(__file__).with_name("blender")
    missing = [name for name in REQUIRED_BLENDER_SCRIPTS if not (directory / name).is_file()]
    return _check(
        "package_assets",
        "fail" if missing else "pass",
        f"Missing packaged Blender scripts: {', '.join(missing)}" if missing else "Required Blender scripts are present.",
        directory=str(directory),
        missing=missing,
    )


def collect_environment_diagnostics(
    config: AppConfig,
    *,
    blender_path: Path | None = None,
    work_directory: Path | None = None,
) -> dict[str, Any]:
    """Inspect the installed runtime without opening a model or modifying source assets."""
    checks = [
        _python_check(),
        _platform_check(),
        _pyside_check(),
        _blender_check(config, blender_path),
        _work_directory_check(work_directory or config.work_directory),
        _package_assets_check(),
    ]
    failed = [item["id"] for item in checks if item["status"] == "fail"]
    warnings = [item["id"] for item in checks if item["status"] == "warning"]
    return {
        "application": "Tora_MeshForge",
        "application_version": __version__,
        "schema_version": "1.0",
        "operation": "environment_diagnostics",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "fail" if failed else ("warning" if warnings else "pass"),
        "ready": not failed,
        "checks": checks,
        "failed_checks": failed,
        "warning_checks": warnings,
    }
