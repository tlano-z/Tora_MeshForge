from __future__ import annotations

import os
from pathlib import Path
import re
import shutil


def _version_key(path: Path) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", str(path.parent))
    return tuple(int(item) for item in numbers[-3:])


def _is_blender(path: Path | None) -> bool:
    return bool(path and path.is_file() and path.suffix.lower() == ".exe")


def discover_blender(explicit_path: Path | None = None, saved_path: Path | None = None) -> Path | None:
    """Find Blender using the documented priority order."""
    candidates = [explicit_path, saved_path]
    environment = os.environ.get("BLENDER_PATH")
    if environment:
        candidates.append(Path(environment))
    on_path = shutil.which("blender") or shutil.which("blender.exe")
    if on_path:
        candidates.append(Path(on_path))
    for candidate in candidates:
        if candidate:
            resolved = Path(candidate).expanduser().resolve()
            if _is_blender(resolved):
                return resolved
    roots = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Blender Foundation",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Blender Foundation",
    ]
    discovered: list[Path] = []
    for root in roots:
        if root.is_dir():
            discovered.extend(root.glob("Blender */blender.exe"))
            discovered.extend(root.glob("blender.exe"))
    discovered = [item.resolve() for item in discovered if _is_blender(item)]
    return max(discovered, key=_version_key) if discovered else None

