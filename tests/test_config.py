from pathlib import Path

from tora_meshforge.config import AppConfig, load_config


def test_load_config_resolves_relative_paths(tmp_path: Path) -> None:
    config_file = tmp_path / "settings.toml"
    config_file.write_text(
        """
[application]
work_directory = "jobs"
maximum_source_size_mb = 500
inspection_timeout_seconds = 90
[tools]
blender = "tools/blender.exe"
[texture]
maximum_resolution = 4096
""".strip(),
        encoding="utf-8",
    )
    result = load_config(config_file)
    assert result.work_directory == (tmp_path / "jobs").resolve()
    assert result.blender_path == (tmp_path / "tools" / "blender.exe").resolve()
    assert result.maximum_source_size_mb == 500
    assert result.inspection_timeout_seconds == 90
    assert result.maximum_texture_resolution == 4096


def test_default_config_uses_absolute_work_directory() -> None:
    assert AppConfig().resolved(Path.cwd()).work_directory.is_absolute()

