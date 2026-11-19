"""Real-engine check; skipped unless the optional OCR install (requirements-ocr.txt) is present."""
import io

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("paddleocr")
from PIL import Image, ImageDraw, ImageFont  # noqa: E402  (Pillow comes with PaddleOCR)

from app.main import create_app  # noqa: E402


def dark_bubble_png() -> bytes:
    image = Image.new("RGB", (390, 200), (0, 0, 0))
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype("DejaVuSans.ttf", 16)
    draw.rounded_rectangle((12, 20, 260, 140), 16, fill=(48, 48, 48))
    draw.text((24, 30), "Meeting moved to 6pm", font=font, fill=(228, 230, 235))
    draw.text((24, 100), "Bring the chairs", font=font, fill=(228, 230, 235))
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def test_real_engine_reads_one_bubble(tmp_path):
    client = TestClient(create_app(str(tmp_path / "ocr.db")))
    response = client.post("/ocr/screenshots", files={"file": ("shot.png", dark_bubble_png(), "image/png")})
    assert response.status_code == 200
    [message] = response.json()["messages"]
    assert message["message_text"] == "Meeting moved to 6pm\n\nBring the chairs"
    assert message["side"] == "left" and message["source"] == "ocr"
    assert message["confidence"] > 0.9
