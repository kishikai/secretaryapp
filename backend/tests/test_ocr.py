import sys

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.ocr import OcrLine, decode_image, group_lines

W = 400
BLANK = np.full((800, W, 3), 255, np.uint8)  # no bubbles: spacing rule decides


def line(text, x0, y0, x1, y1, conf=0.9):
    return OcrLine(text, conf, x0, y0, x1, y1)


def png(w=W, h=800):
    ok, buf = cv2.imencode(".png", np.full((h, w, 3), 255, np.uint8))
    assert ok
    return buf.tobytes()


class FakeEngine:
    def __init__(self, lines):
        self.lines = lines

    def read(self, image):
        return self.lines


def test_two_line_bubble_merges():
    messages, skipped = group_lines(
        [line("hello", 20, 100, 120, 120), line("world", 20, 124, 120, 144)], BLANK
    )
    assert [m.text for m in messages] == ["hello\nworld"]
    assert messages[0].side == "left"
    assert (messages[0].y0, messages[0].y1) == (100, 144)
    assert skipped == []


def test_left_then_right_bubbles():
    messages, _ = group_lines(
        [line("hi", 20, 100, 80, 120), line("yo", 320, 150, 380, 170)], BLANK
    )
    assert [(m.text, m.side) for m in messages] == [("hi", "left"), ("yo", "right")]


def test_big_vertical_gap_splits_same_side():
    messages, _ = group_lines(
        [line("one", 20, 100, 100, 120), line("two", 20, 300, 100, 320)], BLANK
    )
    assert [m.text for m in messages] == ["one", "two"]


def test_centred_short_line_is_skipped():
    centred = line("10:42", 170, 50, 230, 66)
    messages, skipped = group_lines([centred, line("hi", 20, 100, 80, 120)], BLANK)
    assert skipped == [centred]
    assert [m.text for m in messages] == ["hi"]


def test_one_row_in_two_boxes_merges_in_x_order():
    messages, _ = group_lines(
        [line("world", 100, 101, 160, 121), line("hello", 20, 100, 90, 120)], BLANK
    )
    assert [m.text for m in messages] == ["hello world"]


def test_confidence_is_minimum():
    messages, _ = group_lines(
        [line("a", 20, 100, 100, 120, 0.9), line("b", 20, 124, 100, 144, 0.6)], BLANK
    )
    assert messages[0].confidence == 0.6


def test_blank_text_dropped():
    messages, skipped = group_lines([line("   ", 20, 100, 100, 120)], BLANK)
    assert messages == [] and skipped == []


def test_decode_image_rejects_bad_input():
    with pytest.raises(ValueError):
        decode_image(b"not an image")
    with pytest.raises(ValueError):
        decode_image(png(10, 10))
    with pytest.raises(ValueError):
        decode_image(png(10001, 20))
    assert decode_image(png(32, 32)).shape == (32, 32, 3)


LINES = [
    line("10:42", 170, 20, 230, 36),
    line("hi there", 20, 100, 120, 120, 0.91234567),
    line("hello", 300, 200, 380, 220, 0.8),
]


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(str(tmp_path / "t.db"), ocr_engine=FakeEngine(LINES)))


def upload(client, data=None, **form):
    return client.post(
        "/ocr/screenshots",
        files={"file": ("shot.png", data if data is not None else png(), "image/png")},
        data=form,
    )


def test_screenshot_stores_messages(client):
    response = upload(client, chat_id="grp")
    assert response.status_code == 200
    body = response.json()
    assert body["skipped_lines"] == ["10:42"]
    assert body["rejected"] == []
    assert [(m["message_text"], m["side"]) for m in body["messages"]] == [
        ("hi there", "left"),
        ("hello", "right"),
    ]
    first = body["messages"][0]
    assert first["source"] == "ocr"
    assert first["chat_id"] == "grp"
    assert first["confidence"] == 0.9123
    assert first["duplicate"] is False
    assert len(client.get("/messages").json()) == 2


def test_same_screenshot_twice_is_duplicate(client):
    upload(client, chat_id="grp")
    body = upload(client, chat_id="grp").json()
    assert [m["duplicate"] for m in body["messages"]] == [True, True]
    assert len(client.get("/messages").json()) == 2


def test_non_image_is_422(client):
    response = upload(client, data=b"not an image")
    assert response.status_code == 422


def test_chat_id_too_long_is_422(client):
    assert upload(client, chat_id="x" * 201).status_code == 422


def test_too_large_is_413(client):
    assert upload(client, data=b"\0" * (10 * 1024 * 1024 + 1)).status_code == 413


def test_engine_missing_is_503(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "app.ocr_paddle", None)
    client = TestClient(create_app(str(tmp_path / "t.db"), ocr_engine=None))
    response = upload(client)
    assert response.status_code == 503
    assert response.json()["detail"] == "OCR engine not installed; see README"


def bubbles(*rects, background=(0, 0, 0), colour=(48, 48, 48), h=400):
    image = np.full((h, W, 3), background, np.uint8)
    for x0, y0, x1, y1 in rects:
        cv2.rectangle(image, (x0, y0), (x1, y1), colour, -1)
    return image


def test_paragraph_gap_inside_one_dark_bubble_stays_one_message():
    # Mirrors a real dark-mode screenshot: blank lines inside one bubble.
    image = bubbles((6, 0, 342, 300))
    rows = [line("Venue, Thursday", 19, 7, 172, 21), line("Present:", 16, 42, 72, 60),
            line("alex", 14, 60, 48, 79), line("only 9 of us", 16, 250, 165, 268)]
    messages, _ = group_lines(rows, image)
    assert [m.text for m in messages] == ["Venue, Thursday\n\nPresent:\nalex\n\nonly 9 of us"]


def test_adjacent_bubbles_with_tight_text_gap_are_split():
    # Text gap is small, but a 2px background gap separates the bubbles.
    image = bubbles((6, 90, 200, 125), (6, 128, 200, 163))
    rows = [line("first", 18, 100, 120, 116), line("second", 18, 138, 120, 154)]
    messages, _ = group_lines(rows, image)
    assert [m.text for m in messages] == ["first", "second"]


def test_light_mode_bubbles_on_white_page():
    image = bubbles((10, 20, 260, 110), (150, 140, 390, 180), background=(255, 255, 255), colour=(235, 230, 228))
    cv2.rectangle(image, (150, 140), (390, 180), (255, 132, 0), -1)  # own message, blue
    rows = [line("Are we still", 25, 30, 200, 55), line("", 0, 0, 0, 0), line("meeting at 6?", 25, 75, 220, 100),
            line("Yes", 330, 150, 380, 170)]
    messages, _ = group_lines(rows, image)
    assert [(m.text, m.side) for m in messages] == [("Are we still\n\nmeeting at 6?", "left"), ("Yes", "right")]
