from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from typing import Sequence
import zipfile


REQUIRED_PUBLIC_FILES = (
    "README.md",
    "README.en.md",
    "LICENSE",
    "THIRD_PARTY_NOTICES.md",
    "SECURITY.md",
    "Install-Tora_MeshForge.bat",
    "Tora_MeshForge.bat",
    "docs/about.md",
    "docs/installation.md",
    "docs/licensing.md",
)
PRIVATE_PATH_PREFIXES = ("AgentWork/", "work/", ".venv/", ".agents/", ".codex/")
PRIVATE_ASSET_SUFFIXES = (".fbx", ".blend", ".glb", ".gltf", ".obj")
DEVELOPMENT_ONLY_FILES = (
    "AGENTS.md",
    "CODE_OF_CONDUCT.md",
    "CONTRIBUTING.md",
    "docs/release-checklist.md",
    "docs/runtime-rebuild-roadmap.md",
)


class ReleaseVerificationError(RuntimeError):
    pass


def _run(command: Sequence[str], cwd: Path) -> None:
    completed = subprocess.run(command, cwd=cwd, check=False, shell=False)
    if completed.returncode != 0:
        raise ReleaseVerificationError(f"Command failed ({completed.returncode}): {' '.join(command)}")


def _tracked_files(root: Path) -> list[str]:
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        check=False,
        capture_output=True,
        shell=False,
    )
    if completed.returncode != 0:
        return []
    return [item.decode("utf-8").replace("\\", "/") for item in completed.stdout.split(b"\0") if item]


def _verify_source_tree(root: Path) -> dict[str, object]:
    missing = [name for name in REQUIRED_PUBLIC_FILES if not (root / name).is_file()]
    if missing:
        raise ReleaseVerificationError(f"Required public files are missing: {', '.join(missing)}")

    metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    project = metadata["project"]
    if project.get("license") != "MIT":
        raise ReleaseVerificationError("pyproject.toml must use the SPDX expression MIT.")
    license_files = set(project.get("license-files", []))
    if not {"LICENSE", "THIRD_PARTY_NOTICES.md"}.issubset(license_files):
        raise ReleaseVerificationError("License and third-party notices must be included in distribution metadata.")

    tracked = _tracked_files(root)
    private_tracked = [
        name
        for name in tracked
        if name.startswith(PRIVATE_PATH_PREFIXES) or name.lower().endswith(PRIVATE_ASSET_SUFFIXES)
    ]
    if private_tracked:
        raise ReleaseVerificationError(f"Private fixtures or work files are tracked: {', '.join(private_tracked)}")

    return {
        "required_files": len(REQUIRED_PUBLIC_FILES),
        "git_index_available": bool(tracked),
        "tracked_files_checked": len(tracked),
    }


def _verify_wheel(wheel: Path) -> dict[str, object]:
    with zipfile.ZipFile(wheel) as archive:
        names = [name.replace("\\", "/") for name in archive.namelist()]
        metadata_name = next((name for name in names if name.endswith(".dist-info/METADATA")), None)
        metadata = archive.read(metadata_name).decode("utf-8") if metadata_name else ""
    required_suffixes = (
        "tora_meshforge/blender/retopology_bake_probe.py",
        ".dist-info/licenses/LICENSE",
        ".dist-info/licenses/THIRD_PARTY_NOTICES.md",
    )
    missing = [suffix for suffix in required_suffixes if not any(name.endswith(suffix) for name in names)]
    if missing:
        raise ReleaseVerificationError(f"Wheel is missing packaged files: {', '.join(missing)}")
    metadata_fields = (
        "License-Expression: MIT",
        "License-File: LICENSE",
        "License-File: THIRD_PARTY_NOTICES.md",
    )
    missing_metadata = [field for field in metadata_fields if field not in metadata]
    if missing_metadata:
        raise ReleaseVerificationError(f"Wheel license metadata is incomplete: {', '.join(missing_metadata)}")
    leaked = [name for name in names if name.startswith(PRIVATE_PATH_PREFIXES)]
    if leaked:
        raise ReleaseVerificationError(f"Wheel contains private paths: {', '.join(leaked)}")
    return {"path": str(wheel), "entries": len(names), "size_bytes": wheel.stat().st_size}


def _verify_sdist(sdist: Path) -> dict[str, object]:
    with tarfile.open(sdist, "r:gz") as archive:
        names = [member.name.replace("\\", "/") for member in archive.getmembers() if member.isfile()]
        package_info_name = next((name for name in names if name.endswith("/PKG-INFO")), None)
        package_info_member = archive.getmember(package_info_name) if package_info_name else None
        package_info_file = archive.extractfile(package_info_member) if package_info_member else None
        package_info = package_info_file.read().decode("utf-8") if package_info_file else ""
    required_suffixes = tuple(f"/{name}" for name in REQUIRED_PUBLIC_FILES) + ("/pyproject.toml",)
    missing = [suffix for suffix in required_suffixes if not any(name.endswith(suffix) for name in names)]
    if missing:
        raise ReleaseVerificationError(f"Source distribution is missing files: {', '.join(missing)}")
    if "License-Expression: MIT" not in package_info:
        raise ReleaseVerificationError("Source distribution PKG-INFO is missing the MIT license expression.")
    leaked = [
        name
        for name in names
        if any(f"/{prefix}" in f"/{name}" for prefix in PRIVATE_PATH_PREFIXES)
        or name.lower().endswith(PRIVATE_ASSET_SUFFIXES)
    ]
    if leaked:
        raise ReleaseVerificationError(f"Source distribution contains private fixtures: {', '.join(leaked)}")
    development_files = [
        name
        for name in names
        if any(name.endswith(f"/{path}") for path in DEVELOPMENT_ONLY_FILES)
    ]
    if development_files:
        raise ReleaseVerificationError(
            "Source distribution contains development-only documents: "
            + ", ".join(development_files)
        )
    return {"path": str(sdist), "entries": len(names), "size_bytes": sdist.stat().st_size}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify a Tora_MeshForge source release and its Python distributions.")
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--output", type=Path, help="Keep built wheel and sdist in this directory")
    parser.add_argument("--report", type=Path, help="Write release verification JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    if sys.prefix == sys.base_prefix:
        print("Release verification must run inside a virtual environment.", file=sys.stderr)
        return 1

    report: dict[str, object] = {
        "application": "Tora_MeshForge",
        "operation": "release_verification",
        "status": "running",
    }
    try:
        report["source_tree"] = _verify_source_tree(root)
        if not args.skip_tests:
            _run([sys.executable, "-m", "pytest"], root)
            report["tests"] = "pass"
        if not args.skip_build:
            if args.output:
                output = args.output.expanduser().resolve()
                output.mkdir(parents=True, exist_ok=True)
                _run([sys.executable, "-m", "build", "--wheel", "--sdist", "--outdir", str(output)], root)
                wheel = max(output.glob("tora_meshforge-*.whl"), key=lambda item: item.stat().st_mtime)
                sdist = max(output.glob("tora_meshforge-*.tar.gz"), key=lambda item: item.stat().st_mtime)
                report["wheel"] = _verify_wheel(wheel)
                report["sdist"] = _verify_sdist(sdist)
            else:
                with tempfile.TemporaryDirectory(prefix="tora-meshforge-release-") as temporary:
                    output = Path(temporary)
                    _run([sys.executable, "-m", "build", "--wheel", "--sdist", "--outdir", str(output)], root)
                    wheel = next(output.glob("tora_meshforge-*.whl"))
                    sdist = next(output.glob("tora_meshforge-*.tar.gz"))
                    report["wheel"] = _verify_wheel(wheel)
                    report["sdist"] = _verify_sdist(sdist)
        report["status"] = "pass"
    except (OSError, ReleaseVerificationError, StopIteration, ValueError) as exc:
        report["status"] = "fail"
        report["error"] = str(exc)
        print(f"Release verification failed: {exc}", file=sys.stderr)
        exit_code = 1
    else:
        print("Tora_MeshForge release verification: PASS")
        exit_code = 0

    if args.report:
        destination = args.report.expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
