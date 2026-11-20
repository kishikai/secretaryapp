import io
import json
import sys
import threading
import types
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


@pytest.mark.parametrize("code", [401, 403])
def test_token_rejection_stops_the_collector_and_keeps_the_text(code, caplog):
    send = FakeSend(http_error(code))
    with pytest.raises(cc.TokenRejected):
        cc.poll_once(lambda: "hello", send, "old")
    assert send.calls == ["hello"]
    assert "token rejected; check SECRETARY_TOKEN" in caplog.text


def test_main_exits_2_when_the_token_is_rejected(monkeypatch):
    texts = iter(["already there", "new text"])
    monkeypatch.setitem(sys.modules, "pyperclip", types.SimpleNamespace(paste=lambda: next(texts)))
    monkeypatch.setenv("SECRETARY_TOKEN", "st_bad")

    def reject(*args):
        raise http_error(401)

    monkeypatch.setattr(cc, "send_message", reject)
    assert cc.main([]) == 2


def test_main_exits_with_an_error_without_a_token(monkeypatch, caplog):
    monkeypatch.delenv("SECRETARY_TOKEN", raising=False)
    assert cc.main([]) == 2
    assert "SECRETARY_TOKEN" in caplog.text


def test_token_comes_from_env_or_first_line_of_file(tmp_path):
    path = tmp_path / "token"
    path.write_text("  st_from_file  \nsecond line\n")
    assert cc.load_token(str(path), {}) == "st_from_file"
    assert cc.load_token(None, {"SECRETARY_TOKEN": "st_env\n"}) == "st_env"
    # The file wins when both are given.
    assert cc.load_token(str(path), {"SECRETARY_TOKEN": "st_env"}) == "st_from_file"


@pytest.mark.parametrize("environ", [{}, {"SECRETARY_TOKEN": "  "}, {"SECRETARY_TOKEN": "a b"}])
def test_missing_or_malformed_token_is_an_error(environ):
    with pytest.raises(ValueError):
        cc.load_token(None, environ)


def test_unreadable_or_empty_token_file_is_an_error(tmp_path):
    empty = tmp_path / "empty"
    empty.write_text("\n")
    for path in (empty, tmp_path / "missing"):
        with pytest.raises(ValueError):
            cc.load_token(str(path), {})


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
            seen["authorization"] = self.headers["Authorization"]
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
        result = cc.send_message(url, "hi there", "chat-1", "st_secret")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert result == {"id": 7, "duplicate": False}
    assert seen["path"] == "/messages"
    assert seen["method"] == "POST"
    assert seen["content_type"] == "application/json"
    assert seen["authorization"] == "Bearer st_secret"
    assert seen["body"] == {"message_text": "hi there", "source": "clipboard", "chat_id": "chat-1"}


@pytest.mark.parametrize("value", ["0", "-1", "abc"])
def test_interval_must_be_positive(value):
    with pytest.raises(SystemExit):
        cc.parse_args(["--interval", value])


@pytest.mark.parametrize("value", ["localhost:8000", "127.0.0.1:8000", "ftp://host"])
def test_api_url_must_be_http(value):
    with pytest.raises(SystemExit):
        cc.parse_args(["--api-url", value])


def test_heartbeat_failures_never_stop_the_collector():
    for error in (http_error(500), http_error(404), urllib.error.URLError("down"), TimeoutError()):
        def send(error=error):
            raise error
        cc.beat(send)  # logged, not raised


@pytest.mark.parametrize("code", [401, 403])
def test_heartbeat_token_rejection_stops_the_collector(code):
    def send():
        raise http_error(code)
    with pytest.raises(cc.TokenRejected):
        cc.beat(send)


def test_send_heartbeat_against_real_http_server():
    seen = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            seen["path"], seen["authorization"] = self.path, self.headers["Authorization"]
            self.send_response(204)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        cc.send_heartbeat(f"http://127.0.0.1:{server.server_port}/", "st_secret")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert seen == {"path": "/collector/heartbeat", "authorization": "Bearer st_secret"}
