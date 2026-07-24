"""Bake Base Color from the dense source surface to a rebuilt target."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
import time
import traceback
from typing import Any

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from roundtrip_scene import apply_texture_override, clear_scene, import_model  # noqa: E402
from runtime_rebuild_scene import (  # noqa: E402
    RUNTIME_UV_NAME,
    activate_bake_target,
    base_bake_changed_pixels,
    make_emission_copy,
    make_mask_material,
    make_target_material,
    scene_diagonal,
    select_only,
    uv_area_metrics,
    uv_overlap_metrics,
    write_invalid_mask,
)


def arguments() -> argparse.Namespace:
    values = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--texture-override", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--bake-dir", type=Path, required=True)
    parser.add_argument("--resolution", type=int, default=2048)
    parser.add_argument("--margin-pixels", type=int, default=4)
    parser.add_argument("--ray-distance-ratio", type=float, default=0.02)
    parser.add_argument("--cage-extrusion-ratio", type=float, default=0.005)
    parser.add_argument("--skip-shape-normal", action="store_true")
    return parser.parse_args(values)


def prepare_source_materials(sources: list[Any]) -> None:
    for source in sources:
        if not source.material_slots:
            raise ValueError(f"{source.name} has no source material for Base Color transfer.")
        for index, slot in enumerate(source.material_slots):
            slot.material = make_emission_copy(slot.material, f"RetopoSource_{source.name}_{index}")


def configure_selected_bake(diagonal: float, margin_pixels: int, ray_ratio: float, cage_ratio: float) -> None:
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 1
    bake = scene.render.bake
    bake.target = "IMAGE_TEXTURES"
    bake.save_mode = "INTERNAL"
    bake.use_selected_to_active = True
    bake.use_clear = False
    bake.margin = margin_pixels
    bake.margin_type = "EXTEND"
    bake.use_cage = False
    bake.cage_extrusion = diagonal * cage_ratio
    bake.max_ray_distance = diagonal * ray_ratio


def configure_tangent_normal_bake() -> None:
    bake = bpy.context.scene.render.bake
    bake.normal_space = "TANGENT"
    bake.normal_r = "POS_X"
    bake.normal_g = "POS_Y"
    bake.normal_b = "POS_Z"


def activate_image_node(material: Any, image: Any) -> Any:
    node = material.node_tree.nodes.new("ShaderNodeTexImage")
    node.image = image
    node.interpolation = "Linear"
    for candidate in material.node_tree.nodes:
        candidate.select = False
    node.select = True
    material.node_tree.nodes.active = node
    return node


def connect_tangent_normal(material: Any, principled: Any, texture: Any) -> Any:
    normal = material.node_tree.nodes.new("ShaderNodeNormalMap")
    normal.space = "TANGENT"
    normal.inputs["Strength"].default_value = 1.0
    material.node_tree.links.new(texture.outputs["Color"], normal.inputs["Color"])
    material.node_tree.links.new(normal.outputs["Normal"], principled.inputs["Normal"])
    return normal


def finalize_normal_image(image: Any, uv_mask: Any, invalid_path: Path) -> dict[str, Any]:
    values = np.empty(len(image.pixels), dtype=np.float32)
    image.pixels.foreach_get(values)
    normal = values.reshape((-1, 4))
    mask_values = np.empty(len(uv_mask.pixels), dtype=np.float32)
    uv_mask.pixels.foreach_get(mask_values)
    expected = np.max(mask_values.reshape((-1, 4))[:, :3], axis=1) > 0.5
    sentinel = (
        (normal[:, 0] > 0.98)
        & (normal[:, 1] < 0.02)
        & (normal[:, 2] > 0.98)
    )
    finite = np.all(np.isfinite(normal[:, :3]), axis=1)
    invalid = expected & (sentinel | ~finite)
    valid = expected & ~invalid
    invalid_count = int(np.count_nonzero(invalid))
    expected_count = int(np.count_nonzero(expected))
    if expected_count == 0:
        raise RuntimeError("UV occupancy mask bake produced no pixels for Normal validation.")

    invalid_pixels = np.zeros_like(normal)
    invalid_pixels[invalid, 0] = 1.0
    invalid_pixels[invalid, 3] = 1.0
    invalid_image = bpy.data.images.new(
        invalid_path.stem,
        width=image.size[0],
        height=image.size[1],
        alpha=True,
    )
    invalid_image.pixels.foreach_set(invalid_pixels.reshape(-1))
    invalid_image.filepath_raw = str(invalid_path)
    invalid_image.file_format = "PNG"
    invalid_image.save()

    # Unoccupied or failed pixels must remain a neutral tangent-space Normal,
    # never the magenta projection sentinel used during validation.
    replace = sentinel | ~finite
    normal[replace, 0] = 0.5
    normal[replace, 1] = 0.5
    normal[replace, 2] = 1.0
    normal[replace, 3] = 1.0
    image.pixels.foreach_set(normal.reshape(-1))
    image.update()

    decoded = normal[valid, :3] * 2.0 - 1.0
    lengths = np.linalg.norm(decoded, axis=1) if np.any(valid) else np.empty(0)
    return {
        "normal_uv_occupied_pixels": expected_count,
        "normal_valid_pixels": int(np.count_nonzero(valid)),
        "normal_invalid_pixels": invalid_count,
        "normal_invalid_pixel_ratio": invalid_count / max(1, expected_count),
        "normal_non_finite_values": int(np.count_nonzero(~np.isfinite(normal[:, :3]))),
        "normal_decoded_length_mean": float(np.mean(lengths)) if lengths.size else 0.0,
        "invalid_normal_mask": str(invalid_path.resolve()),
    }


def select_sources_to_target(sources: list[Any], target: Any) -> None:
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    for source in sources:
        source.hide_set(False)
        source.hide_render = False
        source.select_set(True)
    target.hide_set(False)
    target.hide_render = False
    target.select_set(True)
    bpy.context.view_layer.objects.active = target


def export_target(target: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    # Blender may leave hidden selected-to-active source objects selected.
    # Explicitly unhide and deselect every object before FBX use_selection.
    for obj in bpy.context.scene.objects:
        obj.hide_set(False)
        obj.select_set(False)
    target.select_set(True)
    bpy.context.view_layer.objects.active = target
    bpy.ops.export_scene.fbx(
        filepath=str(path.resolve()),
        use_selection=True,
        object_types={"MESH"},
        use_mesh_modifiers=True,
        use_tspace=True,
        use_custom_props=False,
        add_leaf_bones=False,
        bake_anim=False,
        path_mode="COPY",
        embed_textures=True,
        axis_forward="-Z",
        axis_up="Y",
    )


def validate_exported_output(
    path: Path,
    expected_triangles: int,
    expected_resolution: int,
    expect_normal: bool,
) -> dict[str, Any]:
    clear_scene()
    import_model(path)
    meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    output_triangles = sum(
        sum(max(0, len(polygon.vertices) - 2) for polygon in obj.data.polygons)
        for obj in meshes
    )
    runtime_uv_present = bool(meshes) and all(
        obj.data.uv_layers.get(RUNTIME_UV_NAME) is not None for obj in meshes
    )
    base_color_connected = False
    base_color_srgb = False
    base_color_resolution_valid = False
    normal_connected = False
    normal_non_color = False
    normal_resolution_valid = False
    for obj in meshes:
        for slot in obj.material_slots:
            material = slot.material
            if material is None or not material.use_nodes or material.node_tree is None:
                continue
            for principled in (
                node for node in material.node_tree.nodes if node.type == "BSDF_PRINCIPLED"
            ):
                base = principled.inputs.get("Base Color")
                if base is not None and base.is_linked and base.links[0].from_node.type == "TEX_IMAGE":
                    image = base.links[0].from_node.image
                    base_color_connected = image is not None
                    base_color_srgb = bool(
                        image is not None and image.colorspace_settings.name == "sRGB"
                    )
                    base_color_resolution_valid = bool(
                        image is not None
                        and tuple(image.size) == (expected_resolution, expected_resolution)
                    )
                normal_input = principled.inputs.get("Normal")
                if normal_input is None or not normal_input.is_linked:
                    continue
                normal_node = normal_input.links[0].from_node
                color = normal_node.inputs.get("Color") if normal_node.type == "NORMAL_MAP" else None
                if color is None or not color.is_linked or color.links[0].from_node.type != "TEX_IMAGE":
                    continue
                image = color.links[0].from_node.image
                normal_connected = image is not None
                normal_non_color = bool(
                    image is not None and image.colorspace_settings.name == "Non-Color"
                )
                normal_resolution_valid = bool(
                    image is not None
                    and tuple(image.size) == (expected_resolution, expected_resolution)
                )
    normals_finite = all(
        all(math.isfinite(float(component)) for component in vertex.normal)
        for obj in meshes
        for vertex in obj.data.vertices
    )
    tangents_recalculable = True
    if expect_normal:
        try:
            for obj in meshes:
                obj.data.calc_tangents(uvmap=RUNTIME_UV_NAME)
        except Exception:
            tangents_recalculable = False
    checks = {
        "single_target_mesh": len(meshes) == 1,
        "triangle_count_preserved": output_triangles == expected_triangles,
        "runtime_uv_present": runtime_uv_present,
        "vertex_normals_finite": normals_finite,
        "basecolor_connected": base_color_connected,
        "basecolor_srgb": base_color_srgb,
        "basecolor_resolution": base_color_resolution_valid,
        "normal_connection_requirement": not expect_normal or normal_connected,
        "normal_colorspace_requirement": not expect_normal or normal_non_color,
        "normal_resolution_requirement": not expect_normal or normal_resolution_valid,
        "tangents_recalculable": not expect_normal or tangents_recalculable,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "failed_checks": [name for name, passed in checks.items() if not passed],
        "mesh_objects": len(meshes),
        "triangles": output_triangles,
        "observed_material": {
            "basecolor_connected": base_color_connected,
            "basecolor_srgb": base_color_srgb,
            "basecolor_resolution_valid": base_color_resolution_valid,
            "normal_connected": normal_connected,
            "normal_non_color": normal_non_color,
            "normal_resolution_valid": normal_resolution_valid,
            "vertex_normals_finite": normals_finite,
            "tangents_recalculable": tangents_recalculable,
        },
    }


def main() -> int:
    args = arguments()
    started = time.perf_counter()
    try:
        args.source = args.source.expanduser().resolve()
        args.target = args.target.expanduser().resolve()
        args.output = args.output.expanduser().resolve()
        args.report = args.report.expanduser().resolve()
        args.bake_dir = args.bake_dir.expanduser().resolve()
        args.bake_dir.mkdir(parents=True, exist_ok=True)
        clear_scene()
        import_model(args.source)
        if args.texture_override:
            apply_texture_override(args.texture_override.expanduser().resolve())
        sources = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
        if not sources:
            raise ValueError("Base Color transfer requires a source mesh.")
        prepare_source_materials(sources)

        existing = set(bpy.data.objects)
        import_model(args.target)
        targets = [obj for obj in bpy.data.objects if obj not in existing and obj.type == "MESH"]
        if len(targets) != 1:
            raise ValueError(f"Base Color transfer expects one rebuilt target; found {len(targets)}.")
        target = targets[0]
        runtime_uv = target.data.uv_layers.get(RUNTIME_UV_NAME)
        if runtime_uv is None:
            raise ValueError(f"Rebuilt target has no {RUNTIME_UV_NAME} UV layer.")
        runtime_uv.active_render = True
        target.data.uv_layers.active_index = next(
            index for index, layer in enumerate(target.data.uv_layers)
            if layer.name == RUNTIME_UV_NAME
        )

        target_triangles = sum(
            max(0, len(polygon.vertices) - 2) for polygon in target.data.polygons
        )
        name = f"retopology_{target_triangles}"
        base_path = args.bake_dir / f"basecolor_retopology_{target_triangles}.png"
        invalid_path = args.bake_dir / f"invalid_basecolor_retopology_{target_triangles}.png"
        normal_path = args.bake_dir / f"normal_retopology_{target_triangles}.png"
        invalid_normal_path = args.bake_dir / f"invalid_normal_retopology_{target_triangles}.png"
        base_image = bpy.data.images.new(
            "TMF_RetopologyBaseColor",
            width=args.resolution,
            height=args.resolution,
            alpha=True,
            float_buffer=False,
        )
        base_image.generated_color = (1.0, 0.0, 1.0, 0.0)
        base_image.colorspace_settings.name = "sRGB"
        target_material, texture_node, principled = make_target_material(name, base_image)
        target.data.materials.clear()
        target.data.materials.append(target_material)
        activate_bake_target(target, target_material, base_image)

        diagonal = scene_diagonal(sources)
        configure_selected_bake(
            diagonal,
            args.margin_pixels,
            args.ray_distance_ratio,
            args.cage_extrusion_ratio,
        )
        select_sources_to_target(sources, target)
        result = bpy.ops.object.bake(type="EMIT")
        if "FINISHED" not in result:
            raise RuntimeError(f"Selected-to-active Base Color bake did not finish: {sorted(result)}")
        changed_pixels = base_bake_changed_pixels(base_image)
        if changed_pixels == 0:
            raise RuntimeError("Selected-to-active Base Color bake produced no pixels.")
        target_material.node_tree.links.new(texture_node.outputs["Color"], principled.inputs["Base Color"])
        base_image.filepath_raw = str(base_path)
        base_image.file_format = "PNG"
        base_image.save()

        normal_image = None
        normal_texture = None
        tangents_valid = False
        if not args.skip_shape_normal:
            normal_image = bpy.data.images.new(
                "TMF_RetopologyShapeNormal",
                width=args.resolution,
                height=args.resolution,
                alpha=True,
                float_buffer=False,
            )
            normal_image.generated_color = (1.0, 0.0, 1.0, 1.0)
            normal_image.colorspace_settings.name = "Non-Color"
            normal_texture = activate_image_node(target_material, normal_image)
            activate_bake_target(target, target_material, normal_image)
            try:
                target.data.calc_tangents(uvmap=RUNTIME_UV_NAME)
                tangents_valid = True
            except Exception as exc:
                raise RuntimeError(f"Runtime UV tangent generation failed: {exc}") from exc
            configure_selected_bake(
                diagonal,
                args.margin_pixels,
                args.ray_distance_ratio,
                args.cage_extrusion_ratio,
            )
            configure_tangent_normal_bake()
            select_sources_to_target(sources, target)
            result = bpy.ops.object.bake(type="NORMAL")
            if "FINISHED" not in result:
                raise RuntimeError(f"Selected-to-active shape Normal bake did not finish: {sorted(result)}")
            connect_tangent_normal(target_material, principled, normal_texture)

        uv_mask = bpy.data.images.new(
            "TMF_RetopologyUVMask",
            width=args.resolution,
            height=args.resolution,
            alpha=True,
        )
        uv_mask.generated_color = (0.0, 0.0, 0.0, 0.0)
        mask_material = make_mask_material(name, uv_mask)
        target.data.materials.clear()
        target.data.materials.append(mask_material)
        activate_bake_target(target, mask_material)
        for source in sources:
            source.hide_render = True
            source.hide_set(True)
        select_only([target], target)
        bpy.context.scene.render.bake.use_selected_to_active = False
        bpy.context.scene.render.bake.use_clear = True
        bpy.context.scene.render.bake.margin = 0
        result = bpy.ops.object.bake(type="EMIT")
        if "FINISHED" not in result:
            raise RuntimeError(f"UV occupancy bake did not finish: {sorted(result)}")
        diagnostics = write_invalid_mask(base_image, uv_mask, invalid_path)
        normal_diagnostics: dict[str, Any] = {}
        if normal_image is not None:
            normal_diagnostics = finalize_normal_image(normal_image, uv_mask, invalid_normal_path)
            normal_image.filepath_raw = str(normal_path)
            normal_image.file_format = "PNG"
            normal_image.save()

        target.data.materials.clear()
        target.data.materials.append(target_material)
        runtime_uv = target.data.uv_layers.get(RUNTIME_UV_NAME)
        if runtime_uv is not None:
            runtime_uv.active_render = True
        base_image.pack()
        if normal_image is not None:
            normal_image.pack()
        area = uv_area_metrics(target, RUNTIME_UV_NAME)
        overlap = uv_overlap_metrics(target, RUNTIME_UV_NAME)
        expected_triangles = sum(
            max(0, len(polygon.vertices) - 2) for polygon in target.data.polygons
        )
        export_target(target, args.output)
        reload_validation = validate_exported_output(
            args.output,
            expected_triangles,
            args.resolution,
            normal_image is not None,
        )
        report = {
            "operation": "retopology_material_bake",
            "source": str(args.source),
            "target": str(args.target),
            "output": str(args.output),
            "resolution": [args.resolution, args.resolution],
            "ray_distance_ratio": args.ray_distance_ratio,
            "cage_extrusion_ratio": args.cage_extrusion_ratio,
            "changed_pixels": changed_pixels,
            "basecolor": str(base_path.resolve()),
            "normal": str(normal_path.resolve()) if normal_image is not None else None,
            "maps": ["basecolor"] + (["normal"] if normal_image is not None else []),
            "shape_normal": {
                "enabled": normal_image is not None,
                "method": "dense_source_geometry_to_reduced_surface",
                "space": "tangent",
                "channel_convention": "OpenGL +Y",
                "tangents_valid": tangents_valid,
                **normal_diagnostics,
            },
            "uv_area": area,
            "uv_overlap": overlap,
            "reload_validation": reload_validation,
            **diagnostics,
            "elapsed_seconds": time.perf_counter() - started,
        }
        report["passed"] = (
            area["nondegenerate_ratio"] >= 0.999
            and overlap["overlap_ratio"] <= 0.001
            and diagnostics["invalid_pixel_ratio"] <= 0.01
            and reload_validation["passed"]
            and (
                normal_image is None
                or (
                    tangents_valid
                    and normal_diagnostics["normal_invalid_pixel_ratio"] <= 0.01
                    and normal_diagnostics["normal_non_finite_values"] == 0
                )
            )
        )
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("TMF_RETOPOLOGY_BAKE " + json.dumps(report), flush=True)
        return 0 if report["passed"] else 1
    except Exception as exc:
        failure = {
            "operation": "retopology_basecolor_probe",
            "errors": [{"type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc()}],
        }
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(failure, indent=2), encoding="utf-8")
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
