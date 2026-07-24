from __future__ import annotations

import argparse
import os
from pathlib import Path

from tora_meshforge.gui.app import prepare_windows_dll_search

prepare_windows_dll_search()
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QImage, QPainter


def main() -> int:
    app = QGuiApplication.instance() or QGuiApplication([])
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--columns", type=int, default=3)
    parser.add_argument("items", nargs="+", help="LABEL=IMAGE_PATH")
    args = parser.parse_args()
    items = []
    for item in args.items:
        label, path = item.split("=", 1)
        image = QImage(path)
        if image.isNull():
            raise FileNotFoundError(path)
        items.append((label, image))
    columns = max(1, args.columns)
    cell_width = max(image.width() for _, image in items)
    cell_height = max(image.height() for _, image in items)
    rows = (len(items) + columns - 1) // columns
    sheet = QImage(columns * cell_width, rows * cell_height, QImage.Format_RGBA8888)
    sheet.fill(QColor("#08090c"))
    font_path = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "segoeuib.ttf"
    font_id = QFontDatabase.addApplicationFont(str(font_path))
    families = QFontDatabase.applicationFontFamilies(font_id)
    painter = QPainter(sheet)
    painter.setFont(QFont(families[0] if families else "Arial", 24, QFont.Bold))
    for index, (label, image) in enumerate(items):
        x = (index % columns) * cell_width
        y = (index // columns) * cell_height
        painter.drawImage(x, y, image)
        label_width = min(cell_width - 28, max(250, len(label) * 19))
        painter.fillRect(QRect(x + 14, y + 14, label_width, 42), QColor(0, 0, 0, 175))
        painter.setPen(QColor("white"))
        painter.drawText(QRect(x + 24, y + 18, label_width - 20, 34), label)
    painter.end()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if not sheet.save(str(args.output), "PNG"):
        raise RuntimeError(f"Could not save {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
