"""Render a UV wire layout to PNG in Blender background mode."""
from __future__ import annotations

import argparse
import math
from pathlib import Path
import sys
from typing import Any

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from roundtrip_scene import clear_scene, import_model  # noqa: E402


def arguments() -> argparse.Namespace:
    values = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--uv-name", default="ToraMeshForgeUV")
    parser.add_argument("--size", type=int, default=1024)
    parser.add_argument("--texture", type=Path)
    parser.add_argument("--texture-from-material", action="store_true")
    parser.add_argument("--active-uv", action="store_true")
    parser.add_argument("--texture-opacity", type=float, default=0.72)
    parser.add_argument("--line-width", type=int, default=1)
    parser.add_argument("--line-opacity", type=float, default=1.0)
    parser.add_argument(
        "--max-polygons",
        type=int,
        default=0,
        help="Uniformly sample at most this many polygons; 0 draws every polygon.",
    )
    return parser.parse_args(values)


def draw_line(
    canvas: np.ndarray,
    start: Any,
    end: Any,
    line_width: int,
    line_opacity: float,
) -> None:
    height, width = canvas.shape[:2]
    x0 = float(start.x) * (width - 1)
    y0 = float(start.y) * (height - 1)
    x1 = float(end.x) * (width - 1)
    y1 = float(end.y) * (height - 1)
    steps = max(1, int(max(abs(x1 - x0), abs(y1 - y0))))
    xs = np.rint(np.linspace(x0, x1, steps + 1)).astype(np.int32)
    ys = np.rint(np.linspace(y0, y1, steps + 1)).astype(np.int32)
    valid = (xs >= 0) & (xs < width) & (ys >= 0) & (ys < height)
    xs = xs[valid]
    ys = ys[valid]
    radius = max(0, min(4, line_width - 1))
    for offset_y in range(-radius, radius + 1):
        for offset_x in range(-radius, radius + 1):
            target_x = xs + offset_x
            target_y = ys + offset_y
            inside = (target_x >= 0) & (target_x < width) & (target_y >= 0) & (target_y < height)
            opacity = max(0.0, min(1.0, line_opacity))
            current = canvas[target_y[inside], target_x[inside], :3]
            line_color = np.asarray((0.08, 0.82, 1.0), dtype=np.float32)
            canvas[target_y[inside], target_x[inside], :3] = current * (1.0 - opacity) + line_color * opacity
            canvas[target_y[inside], target_x[inside], 3] = 1.0


def image_canvas(texture: Any, size: int, opacity: float) -> np.ndarray:
    if int(texture.size[0]) != size or int(texture.size[1]) != size:
        texture.scale(size, size)
    values = np.empty(len(texture.pixels), dtype=np.float32)
    texture.pixels.foreach_get(values)
    pixels = values.reshape((size, size, 4)).copy()
    source_alpha = np.clip(pixels[:, :, 3:4], 0.0, 1.0)
    background = np.asarray((0.012, 0.015, 0.022), dtype=np.float32)
    pixels[:, :, :3] = (
        pixels[:, :, :3] * source_alpha * max(0.0, min(1.0, opacity))
        + background * (1.0 - source_alpha)
    )
    pixels[:, :, 3] = 1.0
    return pixels


def texture_canvas(path: Path, size: int, opacity: float) -> np.ndarray:
    texture = bpy.data.images.load(str(path.expanduser().resolve()), check_existing=False)
    try:
        return image_canvas(texture, size, opacity)
    finally:
        bpy.data.images.remove(texture)


def source_material_texture(meshes: list[Any]) -> Any | None:
    candidates: list[tuple[int, Any]] = []
    seen: set[int] = set()
    for obj in meshes:
        for slot in obj.material_slots:
            material = slot.material
            if material is None or not material.use_nodes or material.node_tree is None:
                continue
            for node in material.node_tree.nodes:
                if node.type != "TEX_IMAGE" or node.image is None:
                    continue
                pointer = int(node.image.as_pointer())
                if pointer in seen:
                    continue
                seen.add(pointer)
                color_output = node.outputs.get("Color")
                base_color_link = bool(color_output and any(
                    link.to_node.type == "BSDF_PRINCIPLED"
                    and link.to_socket.name == "Base Color"
                    for link in color_output.links
                ))
                candidates.append((1 if base_color_link else 0, node.image))
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def main() -> int:
    args = arguments()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    clear_scene()
    import_model(args.input.resolve())
    meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    if not meshes:
        raise ValueError("UV layout preview requires a mesh object.")
    size = max(256, min(4096, args.size))
    if args.texture:
        canvas = texture_canvas(args.texture, size, args.texture_opacity)
    elif args.texture_from_material:
        texture = source_material_texture(meshes)
        if texture is None:
            raise ValueError("No source Base Color image was found in imported materials.")
        canvas = image_canvas(texture, size, args.texture_opacity)
    else:
        canvas = np.empty((size, size, 4), dtype=np.float32)
        canvas[:, :, :] = (0.012, 0.015, 0.022, 1.0)
    total_polygons = sum(len(obj.data.polygons) for obj in meshes)
    polygon_stride = (
        max(1, math.ceil(total_polygons / args.max_polygons))
        if args.max_polygons > 0
        else 1
    )
    polygon_index = 0
    for obj in meshes:
        layer = (
            obj.data.uv_layers.active
            if args.active_uv
            else obj.data.uv_layers.get(args.uv_name)
        )
        if layer is None:
            raise ValueError(f"{obj.name} has no UV layer named {args.uv_name}.")
        for polygon in obj.data.polygons:
            draw_polygon = polygon_index % polygon_stride == 0
            polygon_index += 1
            if not draw_polygon:
                continue
            loops = list(polygon.loop_indices)
            for index, loop_index in enumerate(loops):
                next_index = loops[(index + 1) % len(loops)]
                draw_line(
                    canvas,
                    layer.data[loop_index].uv,
                    layer.data[next_index].uv,
                    args.line_width,
                    args.line_opacity,
                )
    image = bpy.data.images.new("ToraMeshForgeUVLayout", width=size, height=size, alpha=True)
    image.pixels.foreach_set(canvas.reshape(-1))
    image.filepath_raw = str(args.output.resolve())
    image.file_format = "PNG"
    image.save()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
