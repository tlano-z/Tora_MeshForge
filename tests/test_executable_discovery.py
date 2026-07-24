from pathlib import Path

from tora_meshforge.utils.executable_discovery import discover_blender


def test_explicit_blender_path_has_priority(tmp_path: Path, monkeypatch) -> None:
    executable = tmp_path / "blender.exe"
    executable.write_bytes(b"fixture")
    monkeypatch.setenv("BLENDER_PATH", str(tmp_path / "missing.exe"))
    assert discover_blender(executable) == executable.resolve()


def test_missing_explicit_path_can_fall_back_to_environment(tmp_path: Path, monkeypatch) -> None:
    executable = tmp_path / "blender.exe"
    executable.write_bytes(b"fixture")
    monkeypatch.setenv("BLENDER_PATH", str(executable))
    assert discover_blender(tmp_path / "missing.exe") == executable.resolve()

