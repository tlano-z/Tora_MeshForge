# Third-Party Notices

Tora_MeshForge's own code is licensed under the MIT License. The components below are separate works and remain under their respective licenses.

## Runtime and external dependencies

| Component | Purpose | Supported/tested version | License | Delivery | Source and notices |
|---|---|---:|---|---|---|
| Blender | Model import, reconstruction, baking, export, and validation backend | 4.2 LTS or newer | GPL-2.0-or-later | Installed separately by the user; not bundled | <https://developer.blender.org/docs/license/> |
| PySide6 | Python bindings and GUI package | `>=6.6,<7`; tested with 6.11.1 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only, or commercial | Installed as a separate Python dependency; not contained in the Tora_MeshForge wheel | <https://doc.qt.io/qtforpython-6/> |
| PySide6 Essentials | Qt libraries required by PySide6 | Same version as PySide6 | LGPL/GPL or commercial, with module-specific notices | Transitive PySide6 wheel dependency | <https://doc.qt.io/qtforpython-6/licenses.html> |
| PySide6 Addons | Additional Qt libraries required by the PySide6 meta-package | Same version as PySide6 | LGPL/GPL or commercial, with module-specific notices | Transitive PySide6 wheel dependency | <https://doc.qt.io/qtforpython-6/licenses.html> |
| Shiboken6 | Python/C++ binding support for PySide6 | Same version as PySide6 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only, or commercial | Transitive PySide6 wheel dependency | <https://doc.qt.io/qtforpython-6/> |

The installer creates a virtual environment and asks pip to obtain these Python dependencies from their publishers. It does not copy their source or binaries into the Tora_MeshForge source tree.

## Build and test tools

Hatchling, build, pytest, pytest-cov, and their dependencies are used only to build or test the project. They are not imported by Tora_MeshForge at runtime and are not bundled in its wheel. Consult each installed distribution's metadata before redistributing a development environment.

## Binary redistribution warning

These notices describe the current source and Python-wheel distribution. A future standalone executable or portable archive that contains PySide6, Qt DLLs, plugins, or other third-party binaries must ship the applicable upstream license texts and notices and must be reviewed against the exact components included. See `docs/licensing.md`.
