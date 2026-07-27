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

"""Draw ghost profile avatars.

Everything is built inside a circle-safe area, because GitHub and Ko-fi both
crop avatars to a circle in some places and a square in others. Shapes are kept
bold and high-contrast so they survive being scaled to 32 pixels in a comment
thread.

    python tools/make_avatar.py [output_dir]
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication

SIZE = 512

INK = QColor("#12141a")
BONE = QColor("#f5f3ee")
VIOLET = QColor("#8b7bd8")
TEAL = QColor("#3fd0c9")
EMBER = QColor("#ff7a45")


def ghost_path(x: float, y: float, w: float, h: float, tails: int = 4) -> QPainterPath:
    """A ghost: domed head, straight sides, scalloped hem."""
    path = QPainterPath()
    radius = w / 2
    hem = y + h
    scallop = w / tails

    path.moveTo(x, hem - scallop / 2)
    path.lineTo(x, y + radius)
    path.arcTo(QRectF(x, y, w, w), 180, -180)
    path.lineTo(x + w, hem - scallop / 2)

    # Walk the hem right to left, alternating dips and peaks.
    for i in range(tails):
        cx = x + w - (i + 0.5) * scallop
        end = x + w - (i + 1) * scallop
        if i % 2 == 0:
            path.quadTo(QPointF(cx, hem + scallop * 0.55), QPointF(end, hem - scallop / 2))
        else:
            path.quadTo(QPointF(cx, hem - scallop * 1.15), QPointF(end, hem - scallop / 2))
    path.closeSubpath()
    return path


def eyes(painter: QPainter, x: float, y: float, w: float, colour: QColor,
         radius_scale: float = 1.0) -> None:
    painter.setBrush(QBrush(colour))
    painter.setPen(Qt.PenStyle.NoPen)
    r = w * 0.085 * radius_scale
    for dx in (0.32, 0.68):
        painter.drawEllipse(QPointF(x + w * dx, y + w * 0.46), r, r * 1.15)


def new_canvas() -> tuple[QImage, QPainter]:
    image = QImage(SIZE, SIZE, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    return image, painter


def solid(bg: QColor) -> QImage:
    """Pale ghost on a colour field. The friendliest and the most legible."""
    image, p = new_canvas()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(bg))
    p.drawEllipse(QRectF(0, 0, SIZE, SIZE))
    p.setBrush(QBrush(BONE))
    p.drawPath(ghost_path(SIZE * 0.24, SIZE * 0.20, SIZE * 0.52, SIZE * 0.50))
    eyes(p, SIZE * 0.24, SIZE * 0.20, SIZE * 0.52, bg)
    p.end()
    return image


def inverted() -> QImage:
    """Ghost knocked out of a dark disc, with a coloured rim."""
    image, p = new_canvas()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(INK))
    p.drawEllipse(QRectF(0, 0, SIZE, SIZE))
    p.setPen(QPen(VIOLET, SIZE * 0.035))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QRectF(SIZE * 0.028, SIZE * 0.028, SIZE * 0.944, SIZE * 0.944))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(VIOLET))
    p.drawPath(ghost_path(SIZE * 0.26, SIZE * 0.22, SIZE * 0.48, SIZE * 0.46))
    eyes(p, SIZE * 0.26, SIZE * 0.22, SIZE * 0.48, INK)
    p.end()
    return image


def headphones() -> QImage:
    """The DJ version: same ghost, wearing cans."""
    image, p = new_canvas()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(INK))
    p.drawEllipse(QRectF(0, 0, SIZE, SIZE))

    gx, gy, gw, gh = SIZE * 0.26, SIZE * 0.26, SIZE * 0.48, SIZE * 0.44
    p.setBrush(QBrush(BONE))
    p.drawPath(ghost_path(gx, gy, gw, gh))
    eyes(p, gx, gy, gw, INK)

    # Band over the dome, then a cup on each side.
    band = SIZE * 0.055
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(EMBER, band, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawArc(QRectF(gx - band, gy - band * 0.8, gw + band * 2, gw + band * 2), 0, 180 * 16)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(EMBER))
    cup_w, cup_h = SIZE * 0.115, SIZE * 0.155
    for cx in (gx - band * 0.5, gx + gw + band * 0.5):
        p.drawRoundedRect(
            QRectF(cx - cup_w / 2, gy + gw * 0.30, cup_w, cup_h),
            cup_w * 0.42, cup_w * 0.42,
        )
    p.end()
    return image


def minimal() -> QImage:
    """No disc. A bold silhouette that sits on any background."""
    image, p = new_canvas()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(INK))
    p.drawPath(ghost_path(SIZE * 0.19, SIZE * 0.13, SIZE * 0.62, SIZE * 0.62, tails=5))
    eyes(p, SIZE * 0.19, SIZE * 0.13, SIZE * 0.62, BONE, radius_scale=1.05)
    p.end()
    return image


def main() -> int:
    QApplication([])
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("avatars")
    out.mkdir(parents=True, exist_ok=True)

    designs = {
        "a-violet": solid(VIOLET),
        "b-inverted": inverted(),
        "c-headphones": headphones(),
        "d-minimal": minimal(),
        "e-teal": solid(TEAL),
    }
    for name, image in designs.items():
        image.save(str(out / f"ghost-{name}.png"))
        # A small copy, to check it still reads in a comment thread.
        image.scaled(48, 48, Qt.AspectRatioMode.KeepAspectRatio,
                     Qt.TransformationMode.SmoothTransformation
                     ).save(str(out / f"ghost-{name}-48.png"))

    print(f"wrote {len(designs)} avatars at {SIZE}px (plus 48px previews) to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
