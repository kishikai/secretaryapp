from dataclasses import dataclass
from statistics import median
from typing import Optional, Protocol

import cv2
import numpy as np

MIN_SIDE = 16
MAX_SIDE = 10_000
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


def decode_image(data: bytes) -> np.ndarray:
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("File is not a readable image")
    height, width = image.shape[:2]
    if min(width, height) < MIN_SIDE or max(width, height) > MAX_SIDE:
        raise ValueError(
            f"Image size {width}x{height} is out of range "
            f"(each side must be {MIN_SIDE}-{MAX_SIDE} pixels)"
        )
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


def _same_bubble(image: np.ndarray, a: "_Row", b: "_Row", cache: dict) -> Optional[bool]:
    """True/False if a and b sit in the same/different chat bubble; None if a is on the page background.

    A bubble is a connected region of a's background colour that does not reach two image corners,
    so paragraph gaps inside one bubble keep it whole and a 2px gap between bubbles splits them.
    """
    ys, xs = _ring(image, a)
    colour = np.median(image[ys, xs], axis=0)
    key = tuple(int(c) // 4 for c in colour)
    if key not in cache:
        mask = np.all(np.abs(image.astype(np.int16) - colour) <= COLOUR_TOLERANCE, axis=2)
        cache[key] = cv2.connectedComponents(mask.astype(np.uint8), connectivity=4)[1]
    labels = cache[key]

    def region(ys, xs) -> int:
        found = labels[ys, xs]
        found = found[found > 0]
        return int(np.bincount(found).argmax()) if found.size else 0

    label = region(ys, xs)
    corners = [labels[0, 0], labels[0, -1], labels[-1, 0], labels[-1, -1]]
    if label == 0 or sum(int(c) == label for c in corners) >= 2:
        return None
    return region(*_ring(image, b)) == label


def group_lines(
    lines: list[OcrLine], image: np.ndarray
) -> tuple[list[OcrMessage], list[OcrLine]]:
    """Group OCR lines into chat bubbles. Returns (messages, skipped lines)."""
    # shortcut: thresholds tuned on synthetic screenshots; retune once real Messenger samples exist.
    width = image.shape[1]
    skipped: list[OcrLine] = []
    candidates: list[OcrLine] = []
    for line in lines:
        if not line.text.strip():
            continue
        centre = (line.x0 + line.x1) / 2
        if (
            abs(centre - width / 2) <= 0.06 * width
            and (line.x1 - line.x0) <= 0.5 * width
            and line.x0 >= 0.2 * width
        ):
            skipped.append(line)
        else:
            candidates.append(line)

    rows: list[_Row] = []
    for line in sorted(candidates, key=lambda l: (l.y0, l.x0)):
        for row in rows:
            if row.overlaps_vertically(line):
                row.add(line)
                break
        else:
            rows.append(_Row(line))
    rows.sort(key=lambda r: r.y0)
    if not rows:
        return [], skipped

    median_height = median(r.y1 - r.y0 for r in rows)
    regions: dict = {}
    groups: list[tuple[str, list[_Row]]] = []
    for row in rows:
        side = "right" if (width - row.x1) < row.x0 else "left"
        if groups:
            g_side, g_rows = groups[-1]
            g_x0 = min(r.x0 for r in g_rows)
            g_x1 = max(r.x1 for r in g_rows)
            g_y1 = max(r.y1 for r in g_rows)
            same = _same_bubble(image, g_rows[-1], row, regions)
            close = row.y0 - g_y1 <= 0.6 * median_height  # fallback when there is no bubble
            if (
                side == g_side
                and row.x0 <= g_x1
                and g_x0 <= row.x1
                and (same if same is not None else close)
            ):
                g_rows.append(row)
                continue
        groups.append((side, [row]))

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
        for side, g_rows in groups
    ]
    return messages, skipped


def extract_messages(
    data: bytes, engine: OcrEngine
) -> tuple[list[OcrMessage], list[OcrLine]]:
    image = decode_image(data)
    lines = engine.read(image)
    return group_lines(lines, image)
