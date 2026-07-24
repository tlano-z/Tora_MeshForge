from __future__ import annotations

from typing import Any


class BlenderUVBackend:
    def capabilities(self) -> dict[str, Any]:
        return {
            "name": "blender_uv",
            "modes": ["consolidated", "angle", "smart"],
            "supports_pixel_margin": True,
            "replaces_existing_uv": True,
            "keeps_source_uv_during_repack": True,
            "supports_validated_boundary_weld": True,
        }
