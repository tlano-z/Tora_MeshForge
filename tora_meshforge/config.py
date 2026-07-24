from __future__ import annotations

from dataclasses import dataclass, fields, replace
from pathlib import Path
import tomllib
from typing import Any


@dataclass(frozen=True, slots=True)
class AppConfig:
    blender_path: Path | None = None
    work_directory: Path = Path("work")
    maximum_source_size_mb: int = 2048
    inspection_timeout_seconds: int = 1800
    maximum_texture_resolution: int = 8192
    default_target_triangles: int | None = None

    def resolved(self, base_directory: Path) -> "AppConfig":
        work = self.work_directory
        if not work.is_absolute():
            work = (base_directory / work).resolve()
        blender = self.blender_path
        if blender is not None and not blender.is_absolute():
            blender = (base_directory / blender).resolve()
        return replace(self, work_directory=work, blender_path=blender)


def _path_or_none(value: Any) -> Path | None:
    if value in (None, ""):
        return None
    return Path(str(value)).expanduser()


def load_config(path: Path | None = None) -> AppConfig:
    """Load TOML configuration, returning defaults when no path is supplied."""
    if path is None:
        return AppConfig().resolved(Path.cwd())
    path = path.expanduser().resolve()
    with path.open("rb") as handle:
        raw = tomllib.load(handle)
    tools = raw.get("tools", {})
    app = raw.get("application", {})
    processing = raw.get("processing", {})
    texture = raw.get("texture", {})
    values = {
        "blender_path": _path_or_none(tools.get("blender")),
        "work_directory": Path(app.get("work_directory", "work")),
        "maximum_source_size_mb": int(app.get("maximum_source_size_mb", 2048)),
        "inspection_timeout_seconds": int(app.get("inspection_timeout_seconds", 1800)),
        "maximum_texture_resolution": int(texture.get("maximum_resolution", 8192)),
        "default_target_triangles": processing.get("target_triangles"),
    }
    known = {item.name for item in fields(AppConfig)}
    return AppConfig(**{key: value for key, value in values.items() if key in known}).resolved(path.parent)

