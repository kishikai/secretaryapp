import os
import struct
from dataclasses import dataclass
from statistics import median
from typing import Optional, Protocol

MIN_SIDE = 16
MAX_SIDE = 10_000
# A long scrolling screenshot (1080 x 10,000) is about 11 MP. The limit stops decompression
# bombs: a 1 MB PNG can declare 20,000 x 20,000 pixels and take 2.3 GB to decode.
MAX_PIXELS = 25_000_000
# Second layer: OpenCV refuses larger images itself, but only if this is set before cv2 loads.
os.environ.setdefault("OPENCV_IO_MAX_IMAGE_PIXELS", str(MAX_PIXELS))

import cv2  # noqa: E402
import numpy as np  # noqa: E402
RING = 3  # pixels around a text row sampled for its background colour
COLOUR_TOLERANCE = 14  # per channel; absorbs anti-aliasing and JPEG noise


@dataclass(frozen=True)
class OcrLine:
    text: str
    confidence: float
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass(frozen=True)
class OcrMessage:
    text: str
    confidence: float
    side: str  # "left" | "right"
    x0: float
    y0: float
    x1: float
    y1: float


class OcrEngine(Protocol):
    def read(self, image: np.ndarray) -> list[OcrLine]: ...


_JPEG_FRAME_MARKERS = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def declared_size(data: bytes) -> Optional[tuple[int, int]]:
    """(width, height) from a PNG or JPEG header without decoding; None for anything else."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR" and len(data) >= 24:
        return struct.unpack(">II", data[16:24])
    if data[:2] != b"\xff\xd8":
        return None
    i = 2
    while i + 9 <= len(data):
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        if marker == 0xFF:  # fill byte
            i += 1
            continue
        if marker == 0x01 or 0xD0 <= marker <= 0xD8:  # markers without a length
            i += 2
            continue
        if marker in _JPEG_FRAME_MARKERS:
            height, width = struct.unpack(">HH", data[i + 5 : i + 9])
            return width, height
        length = int.from_bytes(data[i + 2 : i + 4], "big")
        if length < 2:
            return None
        i += 2 + length
    return None


def decode_image(data: bytes) -> np.ndarray:
    size = declared_size(data)
    if size is None:
        raise ValueError("File is not a PNG or JPEG image")
    width, height = size
    if min(width, height) < MIN_SIDE or max(width, height) > MAX_SIDE:
        raise ValueError(
            f"Image size {width}x{height} is out of range "
            f"(each side must be {MIN_SIDE}-{MAX_SIDE} pixels)"
        )
    if width * height > MAX_PIXELS:
        raise ValueError(f"Image is {width}x{height}; the limit is {MAX_PIXELS // 1_000_000} megapixels")
    try:
        image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    except cv2.error:
        image = None
    if image is None:
        raise ValueError("File is not a readable image")
    # Header and pixels must agree; OpenCV applies EXIF rotation, which may swap the sides.
    if (image.shape[1], image.shape[0]) not in {(width, height), (height, width)}:
        raise ValueError("File is not a readable image")
    return image


class _Row:
    def __init__(self, line: OcrLine):
        self.lines = [line]
        self.x0, self.y0, self.x1, self.y1 = line.x0, line.y0, line.x1, line.y1
        self.confidence = line.confidence

    def overlaps_vertically(self, line: OcrLine) -> bool:
        overlap = min(self.y1, line.y1) - max(self.y0, line.y0)
        smaller = min(self.y1 - self.y0, line.y1 - line.y0)
        return overlap >= 0.5 * smaller

    def add(self, line: OcrLine) -> None:
        self.lines.append(line)
        self.x0, self.y0 = min(self.x0, line.x0), min(self.y0, line.y0)
        self.x1, self.y1 = max(self.x1, line.x1), max(self.y1, line.y1)
        self.confidence = min(self.confidence, line.confidence)

    @property
    def text(self) -> str:
        return " ".join(l.text.strip() for l in sorted(self.lines, key=lambda l: l.x0))


def _ring(image: np.ndarray, row: "_Row") -> tuple[np.ndarray, np.ndarray]:
    """Coordinates of a rectangle RING pixels outside the row, clipped to the image."""
    h, w = image.shape[:2]
    x0, y0 = max(int(row.x0) - RING, 0), max(int(row.y0) - RING, 0)
    x1, y1 = min(int(row.x1) + RING, w - 1), min(int(row.y1) + RING, h - 1)
    across, down = np.arange(x0, x1 + 1), np.arange(y0, y1 + 1)
    ys = np.r_[np.full(across.size, y0), np.full(across.size, y1), down, down]
    xs = np.r_[across, across, np.full(down.size, x0), np.full(down.size, x1)]
    return ys, xs


def _region(labels: np.ndarray, ys: np.ndarray, xs: np.ndarray) -> int:
    found = labels[ys, xs]
    found = found[found > 0]
    return int(np.bincount(found).argmax()) if found.size else 0


class _Bubble:
    """The chat bubble around a row: a connected region of the row's background colour."""

    def __init__(self, labels: np.ndarray, label: int, x0: int, x1: int):
        self.labels, self.label, self.x0, self.x1 = labels, label, x0, x1

    def contains(self, image: np.ndarray, row: _Row) -> bool:
        return _region(self.labels, *_ring(image, row)) == self.label


def _bubble(image: np.ndarray, row: _Row, cache: dict) -> Optional[_Bubble]:
    """The row's bubble, or None when the row sits on the page background.

    The region of the row's background colour counts as the page when it reaches two image corners,
    so paragraph gaps inside one bubble keep it whole and a 2px gap between bubbles splits them.
    """
    ys, xs = _ring(image, row)
    colour = np.median(image[ys, xs], axis=0)
    key = tuple(int(c) // 4 for c in colour)
    if key not in cache:
        mask = np.all(np.abs(image.astype(np.int16) - colour) <= COLOUR_TOLERANCE, axis=2)
        _, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=4)
        cache[key] = labels, stats
    labels, stats = cache[key]
    label = _region(labels, ys, xs)
    corners = [labels[0, 0], labels[0, -1], labels[-1, 0], labels[-1, -1]]
    if label == 0 or sum(int(c) == label for c in corners) >= 2:
        return None
    left = int(stats[label, cv2.CC_STAT_LEFT])
    return _Bubble(labels, label, left, left + int(stats[label, cv2.CC_STAT_WIDTH]))


def group_lines(
    lines: list[OcrLine], image: np.ndarray
) -> tuple[list[OcrMessage], list[OcrLine]]:
    """Group OCR lines into chat bubbles. Returns (messages, skipped lines)."""
    # shortcut: thresholds tuned on synthetic screenshots; retune once real Messenger samples exist.
    width = image.shape[1]
    rows: list[_Row] = []
    for line in sorted((l for l in lines if l.text.strip()), key=lambda l: (l.y0, l.x0)):
        for row in rows:
            if row.overlaps_vertically(line):
                row.add(line)
                break
        else:
            rows.append(_Row(line))
    rows.sort(key=lambda r: r.y0)

    def side_of(x0: float, x1: float) -> str:
        return "right" if (width - x1) < x0 else "left"

    # Timestamps and notices sit on the page; a short centred line inside a bubble is message text.
    cache: dict = {}
    skipped: list[OcrLine] = []
    kept: list[tuple[_Row, Optional[_Bubble]]] = []
    for row in rows:
        bubble = _bubble(image, row, cache)
        centre = (row.x0 + row.x1) / 2
        if (
            bubble is None
            and abs(centre - width / 2) <= 0.06 * width
            and (row.x1 - row.x0) <= 0.5 * width
            and row.x0 >= 0.2 * width
        ):
            skipped.extend(row.lines)
        else:
            kept.append((row, bubble))
    if not kept:
        return [], skipped

    median_height = median(r.y1 - r.y0 for r, _ in kept)
    groups: list[tuple[str, Optional[_Bubble], list[_Row]]] = []
    for row, bubble in kept:
        # A bubble's side comes from the bubble, not the row: the last wrapped line of an
        # own (right) message is left-aligned and can end far from the right edge.
        side = side_of(bubble.x0, bubble.x1) if bubble else side_of(row.x0, row.x1)
        if groups:
            g_side, g_bubble, g_rows = groups[-1]
            if g_bubble is not None:
                join = g_bubble.contains(image, row)
            elif bubble is not None:
                join = False
            else:  # both on the page: fall back to line spacing
                join = (
                    side == g_side
                    and row.x0 <= max(r.x1 for r in g_rows)
                    and min(r.x0 for r in g_rows) <= row.x1
                    and row.y0 - max(r.y1 for r in g_rows) <= 0.6 * median_height
                )
            if join:
                g_rows.append(row)
                continue
        groups.append((side, bubble, [row]))

    def bubble_text(g_rows: list[_Row]) -> str:
        # A gap wider than normal line spacing inside one bubble is a blank line in the original.
        text = g_rows[0].text
        for prev, row in zip(g_rows, g_rows[1:]):
            text += ("\n\n" if row.y0 - prev.y1 > 0.6 * median_height else "\n") + row.text
        return text

    messages = [
        OcrMessage(
            text=bubble_text(g_rows),
            confidence=min(r.confidence for r in g_rows),
            side=side,
            x0=min(r.x0 for r in g_rows),
            y0=min(r.y0 for r in g_rows),
            x1=max(r.x1 for r in g_rows),
            y1=max(r.y1 for r in g_rows),
        )
        for side, _, g_rows in groups
    ]
    return messages, skipped


def extract_messages(
    data: bytes, engine: OcrEngine
) -> tuple[list[OcrMessage], list[OcrLine]]:
    image = decode_image(data)
    lines = engine.read(image)
    return group_lines(lines, image)
