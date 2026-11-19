import io
import json
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import clipboard_collector as cc


class FakeSend:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def __call__(self, text):
        self.calls.append(text)
        outcome = self.outcomes.pop(0) if self.outcomes else {"id": 1, "duplicate": False}
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def http_error(code):
    return urllib.error.HTTPError("http://x/messages", code, "err", {}, io.BytesIO(b""))


def test_new_text_is_sent_and_becomes_last_text():
    send = FakeSend()
    assert cc.poll_once(lambda: "hello", send, None) == "hello"
    assert send.calls == ["hello"]


def test_same_text_is_not_resent():
    send = FakeSend()
    assert cc.poll_once(lambda: "hello", send, "hello") == "hello"
    assert send.calls == []


@pytest.mark.parametrize("value", [None, "", "   \n\t"])
def test_blank_or_none_is_skipped(value):
    send = FakeSend()
    assert cc.poll_once(lambda: value, send, "old") == "old"
    assert send.calls == []


def test_clipboard_read_error_is_handled():
    def boom():
        raise RuntimeError("no clipboard")

    send = FakeSend()
    assert cc.poll_once(boom, send, "old") == "old"
    assert send.calls == []


def test_connection_error_is_retried_next_poll():
    send = FakeSend(urllib.error.URLError("refused"))
    assert cc.poll_once(lambda: "hello", send, "old") == "old"
    assert cc.poll_once(lambda: "hello", send, "old") == "hello"
    assert send.calls == ["hello", "hello"]


def test_timeout_is_retried():
    send = FakeSend(TimeoutError())
    assert cc.poll_once(lambda: "hello", send, None) is None


def test_http_5xx_is_retried():
    send = FakeSend(http_error(503))
    assert cc.poll_once(lambda: "hello", send, "old") == "old"
    assert cc.poll_once(lambda: "hello", send, "old") == "hello"
    assert send.calls == ["hello", "hello"]


def test_http_4xx_is_not_retried():
    send = FakeSend(http_error(422))
    last = cc.poll_once(lambda: "hello", send, "old")
    assert last == "hello"
    assert cc.poll_once(lambda: "hello", send, last) == "hello"
    assert send.calls == ["hello"]


def test_message_text_is_not_logged(caplog):
    caplog.set_level("DEBUG")
    cc.poll_once(lambda: "super secret text", FakeSend(), None)
    cc.poll_once(lambda: "other secret text", FakeSend(http_error(422)), None)
    assert "secret" not in caplog.text


def test_send_message_against_real_http_server():
    seen = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers["Content-Length"])
            seen["path"] = self.path
            seen["method"] = self.command
            seen["content_type"] = self.headers["Content-Type"]
            seen["body"] = json.loads(self.rfile.read(length))
            payload = json.dumps({"id": 7, "duplicate": False}).encode()
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/"  # trailing slash is stripped
        result = cc.send_message(url, "hi there", "chat-1")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert result == {"id": 7, "duplicate": False}
    assert seen["path"] == "/messages"
    assert seen["method"] == "POST"
    assert seen["content_type"] == "application/json"
    assert seen["body"] == {"message_text": "hi there", "source": "clipboard", "chat_id": "chat-1"}


@pytest.mark.parametrize("value", ["0", "-1", "abc"])
def test_interval_must_be_positive(value):
    with pytest.raises(SystemExit):
        cc.parse_args(["--interval", value])


@pytest.mark.parametrize("value", ["localhost:8000", "127.0.0.1:8000", "ftp://host"])
def test_api_url_must_be_http(value):
    with pytest.raises(SystemExit):
        cc.parse_args(["--api-url", value])
