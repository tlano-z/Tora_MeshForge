from __future__ import annotations

from typing import Any


class BlenderBaseColorBaker:
    def capabilities(self) -> dict[str, Any]:
        return {
            "name": "blender_uv_repack_base_color",
            "maps": ["basecolor"],
            "device": "cpu",
            "method": "source_uv_to_runtime_uv",
            "selected_to_active": False,
            "invalid_projection_mask": True,
            "output_material": "neutral_nonmetal",
        }


class BlenderShapeNormalBaker:
    def capabilities(self) -> dict[str, Any]:
        return {
            "name": "blender_shape_difference_normal",
            "maps": ["normal"],
            "device": "cpu",
            "method": "dense_source_geometry_to_reduced_surface",
            "space": "tangent",
            "channel_convention": "OpenGL +Y",
            "selected_to_active": True,
            "invalid_projection_mask": True,
        }
