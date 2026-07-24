from tora_meshforge.remesh import BlenderDecimateBackend
from tora_meshforge.bake import BlenderBaseColorBaker, BlenderShapeNormalBaker
from tora_meshforge.uv import BlenderUVBackend


def test_blender_decimate_capabilities_are_safe_for_fast_optimize() -> None:
    capabilities = BlenderDecimateBackend().capabilities()
    assert capabilities["supports_target_triangles"] is True
    assert capabilities["preserves_uv"] is True
    assert capabilities["preserves_materials"] is True
    assert capabilities["requires_rebake"] is False


def test_runtime_rebuild_backends_expose_uv_repack_capabilities() -> None:
    uv = BlenderUVBackend().capabilities()
    bake = BlenderBaseColorBaker().capabilities()
    assert uv["keeps_source_uv_during_repack"] is True
    assert bake["method"] == "source_uv_to_runtime_uv"
    assert bake["selected_to_active"] is False


def test_shape_normal_backend_declares_tangent_space_geometry_transfer() -> None:
    bake = BlenderShapeNormalBaker().capabilities()
    assert bake["maps"] == ["normal"]
    assert bake["space"] == "tangent"
    assert bake["channel_convention"] == "OpenGL +Y"
    assert bake["selected_to_active"] is True
