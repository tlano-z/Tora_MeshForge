"""Render a deterministic textured preview for manual reduction QA."""
from __future__ import annotations

import argparse
import math
from pathlib import Path
import sys
from typing import Any

import bpy
from mathutils import Vector
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from roundtrip_scene import apply_texture_override, clear_scene, import_model  # noqa: E402


FONT_5X7 = {
    " ": ("00000",) * 7,
    "-": ("00000", "00000", "00000", "11111", "00000", "00000", "00000"),
    "+": ("00000", "00100", "00100", "11111", "00100", "00100", "00000"),
    ",": ("00000", "00000", "00000", "00000", "00110", "00100", "01000"),
    "?": ("01110", "10001", "00001", "00010", "00100", "00000", "00100"),
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "10000", "11110", "00001", "00001", "11110"),
    "6": ("01110", "10000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00001", "01110"),
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "C": ("01111", "10000", "10000", "10000", "10000", "10000", "01111"),
    "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "G": ("01110", "10001", "10000", "10111", "10001", "10001", "01110"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "I": ("11111", "00100", "00100", "00100", "00100", "00100", "11111"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "N": ("10001", "11001", "11001", "10101", "10011", "10011", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "V": ("10001", "10001", "10001", "10001", "10001", "01010", "00100"),
    "W": ("10001", "10001", "10001", "10101", "10101", "10101", "01010"),
    "X": ("10001", "10001", "01010", "00100", "01010", "10001", "10001"),
    "Y": ("10001", "10001", "01010", "00100", "00100", "00100", "00100"),
}


def arguments() -> argparse.Namespace:
    values = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--texture-override", type=Path)
    parser.add_argument("--label", default="")
    parser.add_argument("--mode", choices=("geometry", "mesh", "texture", "material"), default="geometry")
    parser.add_argument("--azimuth-degrees", type=float)
    parser.add_argument("--elevation-degrees", type=float, default=18.0)
    parser.add_argument("--frame-min", type=float, nargs=3)
    parser.add_argument("--frame-max", type=float, nargs=3)
    parser.add_argument("--frame-reference", type=Path)
    return parser.parse_args(values)


def scene_bounds() -> tuple[Vector, Vector]:
    minimum = Vector((math.inf, math.inf, math.inf))
    maximum = Vector((-math.inf, -math.inf, -math.inf))
    found = False
    for obj in bpy.context.scene.objects:
        if obj.type != "MESH":
            continue
        found = True
        for corner in obj.bound_box:
            world = obj.matrix_world @ Vector(corner)
            for index in range(3):
                minimum[index] = min(minimum[index], world[index])
                maximum[index] = max(maximum[index], world[index])
    if not found:
        raise ValueError("Preview requires at least one mesh object.")
    return minimum, maximum


def point_at(obj: Any, target: Vector) -> None:
    obj.rotation_euler = (target - obj.location).to_track_quat("-Z", "Y").to_euler()


def add_area_light(name: str, location: Vector, target: Vector, energy: float, size: float) -> None:
    data = bpy.data.lights.new(name=name, type="SUN")
    data.energy = energy
    data.angle = math.radians(6.0)
    light = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(light)
    light.location = location
    point_at(light, target)


def setup_wireframe_material(scene: Any) -> None:
    material = bpy.data.materials.new("ToraMeshForgePreviewWireframe")
    material.use_nodes = True
    nodes = material.node_tree.nodes
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    emission = nodes.new("ShaderNodeEmission")
    mix = nodes.new("ShaderNodeMixRGB")
    wire = nodes.new("ShaderNodeWireframe")
    wire.use_pixel_size = True
    wire.inputs["Size"].default_value = 1.1
    mix.blend_type = "MIX"
    mix.inputs[1].default_value = (0.012, 0.015, 0.022, 1.0)
    mix.inputs[2].default_value = (0.08, 0.72, 1.0, 1.0)
    emission.inputs["Strength"].default_value = 1.0
    material.node_tree.links.new(wire.outputs["Fac"], mix.inputs[0])
    material.node_tree.links.new(mix.outputs[0], emission.inputs["Color"])
    material.node_tree.links.new(emission.outputs[0], output.inputs["Surface"])
    scene.view_layers[0].material_override = material
    bpy.context.view_layer.material_override = material
    for obj in scene.objects:
        if obj.type == "MESH":
            obj.data.materials.clear()
            obj.data.materials.append(material)

    world = bpy.data.worlds.new("ToraMeshForgeWireWorld") if not bpy.data.worlds else bpy.data.worlds[0]
    scene.world = world
    world.use_nodes = True
    background = world.node_tree.nodes.get("Background")
    background.inputs["Color"].default_value = (0.012, 0.015, 0.022, 1.0)
    background.inputs["Strength"].default_value = 1.0


def neutralize_materials(scene: Any) -> None:
    """Keep Base Color and Normal while removing unrelated PBR styling."""
    seen: set[int] = set()
    for obj in scene.objects:
        if obj.type != "MESH":
            continue
        for slot in obj.material_slots:
            material = slot.material
            if material is None or int(material.as_pointer()) in seen:
                continue
            seen.add(int(material.as_pointer()))
            if not material.use_nodes or material.node_tree is None:
                material.use_nodes = True
            for principled in (
                node for node in material.node_tree.nodes if node.type == "BSDF_PRINCIPLED"
            ):
                for input_name, value in (
                    ("Metallic", 0.0),
                    ("Roughness", 0.58),
                    ("Specular IOR Level", 0.18),
                    ("Coat Weight", 0.0),
                    ("Emission Strength", 0.0),
                ):
                    socket = principled.inputs.get(input_name)
                    if socket is None:
                        continue
                    for link in list(socket.links):
                        material.node_tree.links.remove(link)
                    socket.default_value = value


def setup_render(
    output: Path,
    mode: str,
    azimuth_degrees: float | None,
    elevation_degrees: float,
    frame_minimum: list[float] | None = None,
    frame_maximum: list[float] | None = None,
) -> None:
    if frame_minimum is not None and frame_maximum is not None:
        minimum, maximum = Vector(frame_minimum), Vector(frame_maximum)
    else:
        minimum, maximum = scene_bounds()
    center = (minimum + maximum) * 0.5
    diagonal = max(1e-5, (maximum - minimum).length)
    radius = diagonal * 0.5
    if azimuth_degrees is None:
        direction = Vector((1.25, -2.2, 0.75)).normalized()
    else:
        azimuth = math.radians(azimuth_degrees)
        elevation = math.radians(elevation_degrees)
        direction = Vector((
            math.cos(elevation) * math.cos(azimuth),
            math.cos(elevation) * math.sin(azimuth),
            math.sin(elevation),
        )).normalized()
    distance = radius / math.tan(math.radians(50.0) * 0.5) * 1.55

    camera_data = bpy.data.cameras.new("ToraMeshForgePreviewCamera")
    camera_data.lens = 50
    camera_data.clip_start = max(1e-6, diagonal / 1000)
    camera_data.clip_end = diagonal * 100
    camera = bpy.data.objects.new("ToraMeshForgePreviewCamera", camera_data)
    bpy.context.scene.collection.objects.link(camera)
    camera.location = center + direction * distance
    point_at(camera, center)
    bpy.context.scene.camera = camera

    scene = bpy.context.scene
    if mode in {"geometry", "texture"}:
        scene.render.engine = "BLENDER_WORKBENCH"
        shading = scene.display.shading
        shading.background_type = "VIEWPORT"
        shading.background_color = (0.012, 0.015, 0.022)
        shading.show_specular_highlight = False
        if mode == "geometry":
            shading.type = "SOLID"
            shading.light = "STUDIO"
            shading.color_type = "SINGLE"
            shading.single_color = (0.42, 0.55, 0.68)
            shading.show_shadows = True
            shading.show_cavity = True
            shading.cavity_type = "BOTH"
        else:
            # Workbench TEXTURE + FLAT displays Base Color without source PBR
            # parameters, scene lights, or metallic environment reflections.
            shading.type = "SOLID"
            shading.light = "FLAT"
            shading.color_type = "TEXTURE"
            shading.show_shadows = False
            shading.show_cavity = False
            scene.view_settings.view_transform = "Standard"
    elif mode == "mesh":
        scene.render.engine = "BLENDER_EEVEE_NEXT"
        if hasattr(scene, "eevee") and hasattr(scene.eevee, "taa_render_samples"):
            scene.eevee.taa_render_samples = 16
        scene.view_settings.view_transform = "Standard"
        setup_wireframe_material(scene)
    else:
        # Fixed lighting and neutral PBR parameters make Base Color + Normal
        # comparable without inheriting source Metallic or Roughness styling.
        neutralize_materials(scene)
        add_area_light("Key", center + Vector((-1.2, -1.4, 2.0)).normalized() * diagonal, center, 2.2, diagonal)
        add_area_light("Fill", center + Vector((1.5, -0.5, 0.5)).normalized() * diagonal, center, 0.65, diagonal * 0.8)
        add_area_light("Rim", center + Vector((0.2, 1.5, 1.2)).normalized() * diagonal, center, 1.0, diagonal * 0.7)
        world = bpy.data.worlds.new("ToraMeshForgePreviewWorld") if not bpy.data.worlds else bpy.data.worlds[0]
        bpy.context.scene.world = world
        world.use_nodes = True
        background = world.node_tree.nodes.get("Background")
        background.inputs["Color"].default_value = (0.015, 0.015, 0.02, 1.0)
        background.inputs["Strength"].default_value = 0.04
        scene.render.engine = "BLENDER_EEVEE_NEXT"
        scene.view_settings.view_transform = "Standard"
    scene.render.resolution_x = 640
    scene.render.resolution_y = 640
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.film_transparent = False
    scene.render.filepath = str(output.resolve())
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.image_settings.color_depth = "8"
    output.parent.mkdir(parents=True, exist_ok=True)


def annotate_preview(path: Path, label: str) -> None:
    image = bpy.data.images.load(str(path.resolve()), check_existing=False)
    width, height = int(image.size[0]), int(image.size[1])
    values = np.empty(len(image.pixels), dtype=np.float32)
    image.pixels.foreach_get(values)
    canvas = values.reshape((height, width, 4))
    text = label.upper()
    scale = max(2, min(4, width // max(1, len(text) * 7)))
    glyph_width = 5 * scale
    advance = 6 * scale
    text_width = len(text) * advance - scale
    padding = 10
    banner_height = 7 * scale + padding * 2
    left = 14
    bottom = height - 14 - banner_height
    right = min(width, left + text_width + padding * 2)
    canvas[bottom : height - 14, left:right, :3] = (0.01, 0.012, 0.016)
    canvas[bottom : height - 14, left:right, 3] = 1.0
    cursor = left + padding
    glyph_bottom = bottom + padding
    for character in text:
        glyph = FONT_5X7.get(character, FONT_5X7["?"])
        for row, bits in enumerate(glyph):
            for column, bit in enumerate(bits):
                if bit != "1":
                    continue
                x0 = cursor + column * scale
                y0 = glyph_bottom + (6 - row) * scale
                canvas[y0 : y0 + scale, x0 : x0 + scale, :3] = (1.0, 1.0, 1.0)
                canvas[y0 : y0 + scale, x0 : x0 + scale, 3] = 1.0
        cursor += advance
    image.pixels.foreach_set(canvas.reshape(-1))
    image.filepath_raw = str(path.resolve())
    image.file_format = "PNG"
    image.save()
    bpy.data.images.remove(image)


def main() -> int:
    args = arguments()
    clear_scene()
    reference_bounds: tuple[Vector, Vector] | None = None
    if args.frame_reference:
        import_model(args.frame_reference)
        reference_bounds = scene_bounds()
        clear_scene()
    import_model(args.input)
    if args.texture_override:
        apply_texture_override(args.texture_override)
    setup_render(
        args.output,
        args.mode,
        args.azimuth_degrees,
        args.elevation_degrees,
        list(reference_bounds[0]) if reference_bounds is not None else args.frame_min,
        list(reference_bounds[1]) if reference_bounds is not None else args.frame_max,
    )
    bpy.ops.render.render(write_still=True)
    triangles = sum(
        sum(max(0, len(polygon.vertices) - 2) for polygon in obj.data.polygons)
        for obj in bpy.context.scene.objects if obj.type == "MESH"
    )
    label = args.label.strip() or f"{args.mode.upper()} - {triangles:,} TRIS"
    annotate_preview(args.output, label)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
