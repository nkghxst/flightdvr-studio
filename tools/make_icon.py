# FlightDVR Studio - browse, trim and convert HDZero goggle DVR footage.
# Copyright (C) 2026 Isadu Nkemi
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along with
# this program. If not, see <https://www.gnu.org/licenses/>.

"""Generate the application icon.

Draws a quad silhouette around a play triangle: four motors, one video. Written
out as a multi-resolution .ico so Windows picks the right size for the taskbar,
the Start Menu and the desktop, plus a PNG at every size the other two
platforms need: Linux reads one from the .desktop file, and macOS wants a full
iconset up to 1024px for iconutil to compile into an .icns.

Everything is drawn from the same vector description, so no size is an upscale.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush, QColor, QImage, QImageWriter, QPainter, QPainterPath, QPen, QPolygonF,
)
from PySide6.QtWidgets import QApplication

SIZES = [1024, 512, 256, 128, 64, 48, 32, 16]

BACKDROP = QColor("#161b22")
ACCENT = QColor("#3fd0c9")
HOT = QColor("#ff7a45")


def draw(size: int) -> QImage:
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)

    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = size / 256.0  # everything below is designed at 256px

    # Rounded backdrop.
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(BACKDROP))
    painter.drawRoundedRect(QRectF(6 * s, 6 * s, 244 * s, 244 * s), 52 * s, 52 * s)

    motors = ((74, 74), (182, 74), (74, 182), (182, 182))

    # Arms first, so the body and motors sit cleanly on top of them.
    if size >= 32:
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(ACCENT, 9 * s, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        for x, y in motors:
            painter.drawLine(QPointF(128 * s, 128 * s), QPointF(x * s, y * s))
    painter.setPen(Qt.PenStyle.NoPen)

    # Motors, as rings rather than dots. The inner disc is painted in the
    # backdrop colour instead of being cleared, so there is no transparent hole.
    radius = 30 * s
    for x, y in motors:
        painter.setBrush(QBrush(ACCENT))
        painter.drawEllipse(QPointF(x * s, y * s), radius, radius)
        painter.setBrush(QBrush(BACKDROP))
        painter.drawEllipse(QPointF(x * s, y * s), radius * 0.46, radius * 0.46)

    # Central body holding a play triangle, drawn last so nothing covers it.
    body = QPainterPath()
    body.addRoundedRect(QRectF(86 * s, 86 * s, 84 * s, 84 * s), 22 * s, 22 * s)
    painter.setBrush(QBrush(HOT))
    painter.drawPath(body)

    painter.setBrush(QBrush(BACKDROP))
    painter.drawPolygon(QPolygonF([
        QPointF(114 * s, 106 * s),
        QPointF(114 * s, 150 * s),
        QPointF(152 * s, 128 * s),
    ]))

    painter.end()
    return image


def main() -> int:
    QApplication([])
    root = Path(__file__).resolve().parents[1]
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else root / "packaging/flightdvr.ico"
    target.parent.mkdir(parents=True, exist_ok=True)

    # PNGs first. They are what the Linux and macOS packaging needs, and unlike
    # the .ico they do not depend on an optional Qt image plugin.
    for size in SIZES:
        draw(size).save(str(target.with_name(f"icon_{size}.png")))

    # The app loads its window icon from inside the package, so it has one when
    # run from source as well as when packaged.
    resources = root / "flightdvr" / "resources"
    resources.mkdir(parents=True, exist_ok=True)
    bundled = resources / "icon.ico"

    if b"ico" not in [bytes(f) for f in QImageWriter.supportedImageFormats()]:
        # Some Linux Qt builds ship without the ICO plugin. Both .ico files are
        # committed, so a build there carries on with the copies in the
        # repository instead of stopping over an icon.
        print(f"This Qt build cannot write .ico; wrote {len(SIZES)} PNG sizes.")
        return 0 if target.exists() and bundled.exists() else 1

    # QImageWriter writes one image; Qt's ICO handler takes the largest and
    # scales, so write the 256px master and let Windows downscale. 256 is also
    # the largest size the ICO format holds.
    master = draw(256)
    for path in (target, bundled):
        writer = QImageWriter(str(path), b"ico")
        if not writer.write(master):
            print(f"Failed to write {path}: {writer.errorString()}")
            return 1

    print(f"wrote {target} and {bundled} ({target.stat().st_size} bytes), "
          f"plus {len(SIZES)} PNG sizes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
