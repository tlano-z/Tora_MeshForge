from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_windows_launcher_uses_venv_python_directly() -> None:
    path = PROJECT_ROOT / "Tora_MeshForge.bat"
    launcher = path.read_text(encoding="utf-8")
    raw = path.read_bytes()

    assert ".venv\\Scripts\\python.exe" in launcher
    assert "-m tora_meshforge.gui.app" in launcher
    assert "tora-meshforge-gui.exe" not in launcher
    assert "pythonw.exe" not in launcher
    assert "CreateNoWindow = $true" in launcher
    assert "ProcessWindowStyle]::Hidden" in launcher
    assert raw.isascii()
    assert b"\n" not in raw.replace(b"\r\n", b"")


def test_gui_entry_point_uses_console_python_compatible_launcher() -> None:
    metadata = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    scripts = metadata.split("[project.scripts]", 1)[1].split("[tool.hatch.version]", 1)[0]
    assert "tora-meshforge-gui" in scripts
    assert "[project.gui-scripts]" not in metadata
