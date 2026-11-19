"""Screenshot uploads are size-checked from the PNG/JPEG header before any decoding."""
import struct
import zlib

import cv2
import numpy as np
import pytest

import app.ocr as ocr


def png_header(width, height):
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(b"\x00" * 64)) + chunk(b"IEND", b"")


def encoded(ext, width=64, height=48):
    ok, buf = cv2.imencode(ext, np.full((height, width, 3), 200, np.uint8))
    assert ok
    return buf.tobytes()


def test_declared_size_reads_png_and_jpeg_headers():
    assert ocr.declared_size(encoded(".png", 64, 48)) == (64, 48)
    assert ocr.declared_size(encoded(".jpg", 64, 48)) == (64, 48)
    assert ocr.declared_size(png_header(6000, 5000)) == (6000, 5000)
    assert ocr.declared_size(encoded(".bmp")) is None
    assert ocr.declared_size(b"\xff\xd8\xff") is None  # truncated JPEG
    assert ocr.declared_size(b"") is None


def test_decompression_bomb_is_refused_before_decoding(monkeypatch):
    def must_not_decode(*args, **kwargs):
        raise AssertionError("decoder was called for an oversized image")

    monkeypatch.setattr(ocr.cv2, "imdecode", must_not_decode)
    with pytest.raises(ValueError, match="megapixels"):
        ocr.decode_image(png_header(6000, 5000))  # 30 MP, each side within 10,000
    with pytest.raises(ValueError, match="out of range"):
        ocr.decode_image(png_header(20000, 20000))


def test_real_png_and_jpeg_still_decode():
    assert ocr.decode_image(encoded(".png")).shape == (48, 64, 3)
    assert ocr.decode_image(encoded(".jpg")).shape == (48, 64, 3)


def test_other_formats_and_garbage_are_refused():
    for data in (encoded(".bmp"), b"not an image", png_header(64, 48)[:20]):
        with pytest.raises(ValueError):
            ocr.decode_image(data)


def test_header_that_lies_about_the_size_is_refused():
    real = encoded(".png", 64, 48)
    lying = real[:16] + struct.pack(">II", 32, 32) + real[24:]  # IHDR says 32x32, CRC now wrong
    with pytest.raises(ValueError):
        ocr.decode_image(lying)


def test_pixel_limit_allows_long_scrolling_screenshots():
    assert 1080 * 10_000 <= ocr.MAX_PIXELS
