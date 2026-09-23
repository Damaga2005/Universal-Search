"""Generate packaging/universal_search.ico using only the standard library.

The icon is a magnifying glass on a rounded blue tile, rendered at several
sizes and packed as PNG-compressed ICO entries (supported since Vista).

Usage:  .venv\\Scripts\\python packaging\\make_icon.py
"""

import math
import struct
import zlib
from pathlib import Path

SIZES = (16, 32, 48, 64, 256)
BACKGROUND = (37, 99, 235, 255)  # indigo/blue tile
FOREGROUND = (255, 255, 255, 255)


def _point_segment_distance(px, py, ax, ay, bx, by) -> float:
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if length_sq == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length_sq))
    closest_x, closest_y = ax + t * dx, ay + t * dy
    return math.hypot(px - closest_x, py - closest_y)


def render(size: int) -> bytes:
    """RGBA pixels for one icon size."""
    pixels = bytearray(size * size * 4)
    radius = size * 0.22          # rounded-corner radius of the tile
    lens_cx, lens_cy = size * 0.42, size * 0.40
    lens_r = size * 0.20          # magnifier ring radius
    ring = size * 0.055           # ring thickness
    handle_end = (size * 0.72, size * 0.70)

    for y in range(size):
        for x in range(size):
            # rounded rectangle mask
            dx = max(radius - x, x - (size - 1 - radius), 0.0)
            dy = max(radius - y, y - (size - 1 - radius), 0.0)
            if math.hypot(dx, dy) > radius:
                continue  # transparent
            offset = (y * size + x) * 4
            pixels[offset : offset + 4] = BACKGROUND

            distance = math.hypot(x + 0.5 - lens_cx, y + 0.5 - lens_cy)
            if abs(distance - lens_r) <= ring / 2:
                pixels[offset : offset + 4] = FOREGROUND
                continue
            # handle: from the lower-right of the ring to the corner
            angle_x = lens_cx + lens_r * math.cos(math.radians(45))
            angle_y = lens_cy + lens_r * math.sin(math.radians(45))
            if _point_segment_distance(
                x + 0.5, y + 0.5, angle_x, angle_y, handle_end[0], handle_end[1]
            ) <= ring / 2:
                pixels[offset : offset + 4] = FOREGROUND
                continue
            # lens glass: translucent white inside the ring
            if distance < lens_r - ring / 2:
                pixels[offset : offset + 4] = (255, 255, 255, 48)
    return bytes(pixels)


def write_png(size: int, rgba: bytes) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    stride = size * 4
    raw = b"".join(
        b"\x00" + rgba[row * stride : (row + 1) * stride] for row in range(size)
    )
    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def write_ico(images: dict[int, bytes]) -> bytes:
    entries = sorted(images.items())
    count = len(entries)
    header = struct.pack("<HHH", 0, 1, count)
    offset = 6 + count * 16
    directory = b""
    payloads = b""
    for size, png in entries:
        dimension = 0 if size >= 256 else size
        directory += struct.pack(
            "<BBBBHHII",
            dimension, dimension, 0, 0, 1, 32, len(png), offset,
        )
        payloads += png
        offset += len(png)
    return header + directory + payloads


def main() -> None:
    target = Path(__file__).with_name("universal_search.ico")
    images = {size: write_png(size, render(size)) for size in SIZES}
    target.write_bytes(write_ico(images))
    print(f"wrote {target} ({target.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
