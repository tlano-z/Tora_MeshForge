from __future__ import annotations

from typing import Any

from tora_meshforge.remesh.base import RemesherBackend


class BlenderDecimateBackend(RemesherBackend):
    def capabilities(self) -> dict[str, Any]:
        return {
            "name": "blender_decimate",
            "topology": "triangle",
            "preserves_uv": True,
            "preserves_materials": True,
            "supports_target_triangles": True,
            "processes_objects_independently": True,
            "requires_rebake": False,
        }
