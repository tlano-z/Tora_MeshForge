from __future__ import annotations

import os
from pathlib import Path
import sys

from tora_meshforge import __version__


_DLL_DIRECTORY_HANDLES: list[object] = []


def prepare_windows_dll_search() -> None:
    """Support venvs created from Blender's Windows Python distribution."""
    if os.name != "nt" or not hasattr(os, "add_dll_directory"):
        return
    candidates = [
        Path(sys.base_prefix) / "bin",
        Path(sys.base_prefix).parent.parent,
        Path(sys.prefix) / "Lib" / "site-packages" / "shiboken6",
        Path(sys.prefix) / "Lib" / "site-packages" / "PySide6",
    ]
    for directory in candidates:
        if directory.is_dir():
            _DLL_DIRECTORY_HANDLES.append(os.add_dll_directory(str(directory)))


def main() -> int:
    prepare_windows_dll_search()
    try:
        from PySide6.QtWidgets import QApplication
    except (ImportError, OSError):
        print("PySide6 is required for the GUI. Install with: py -3.11 -m pip install -e .", file=sys.stderr)
        return 1
    if "--check" in sys.argv[1:]:
        print(f"Tora_MeshForge {__version__} GUI preflight: PASS")
        return 0
    from tora_meshforge.gui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("Tora_MeshForge")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("Tora_MeshForge")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
