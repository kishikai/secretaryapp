"""Watch the local clipboard and post newly copied text to the backend.

Run this on your own desktop. It only sends text that you copy, and it never
logs message contents (only length, id and duplicate flag).
"""
import argparse
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Callable, Optional

log = logging.getLogger("clipboard_collector")

DEFAULT_API_URL = "http://127.0.0.1:8000"
REQUEST_TIMEOUT_SECONDS = 10
HEARTBEAT_SECONDS = 60  # the dashboard shows the collector offline after 5 minutes without one
TOKEN_ENV_VAR = "SECRETARY_TOKEN"


class TokenRejected(Exception):
    """The backend answered 401 or 403: the token is wrong, revoked or lacks access."""


def load_token(token_file: Optional[str], environ=os.environ) -> str:
    """Read the ingest token from --token-file (first line) or SECRETARY_TOKEN."""
    if token_file:
        try:
            with open(token_file, encoding="utf-8") as handle:
                token = handle.readline().strip()
        except OSError as exc:
            raise ValueError(f"cannot read token file {token_file}: {exc.strerror or exc}")
    else:
        token = environ.get(TOKEN_ENV_VAR, "").strip()
    if not token:
        raise ValueError(f"no token: set {TOKEN_ENV_VAR} or pass --token-file PATH")
    # urllib raises ValueError for such a header, which poll_once would mistake for an accepted message.
    if not token.isprintable() or any(ch.isspace() for ch in token):
        raise ValueError("the token must not contain spaces or control characters")
    return token


def send_message(api_url: str, text: str, chat_id: Optional[str], token: str) -> dict:
    """POST one message and return the parsed JSON response.

    urllib.error.HTTPError / URLError propagate so the caller can classify them.
    """
    body = json.dumps({"message_text": text, "source": "clipboard", "chat_id": chat_id}).encode("utf-8")
    request = urllib.request.Request(
        f"{api_url.rstrip('/')}/messages",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


def send_heartbeat(api_url: str, token: str) -> None:
    request = urllib.request.Request(
        f"{api_url.rstrip('/')}/collector/heartbeat",
        data=b"",
        method="POST",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS):
        pass


def beat(send: Callable[[], None]) -> None:
    """Best effort: a failed heartbeat only makes the dashboard show the collector offline."""
    try:
        send()
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            log.error("token rejected; check SECRETARY_TOKEN")
            raise TokenRejected from exc
        log.warning("Heartbeat failed (HTTP %s)", exc.code)
    except (OSError, TimeoutError) as exc:
        log.warning("Heartbeat failed (%s)", type(exc).__name__)


def poll_once(
    read_clipboard: Callable[[], Optional[str]],
    send: Callable[[str], dict],
    last_text: Optional[str],
) -> Optional[str]:
    """Check the clipboard once and return the new last_text."""
    try:
        text = read_clipboard()
    except Exception as exc:
        log.warning("Could not read clipboard (%s)", type(exc).__name__)
        return last_text

    if text is None or not text.strip() or text == last_text:
        return last_text

    try:
        result = send(text)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            # Not a rejected message: keep the text unhandled and stop the collector.
            log.error("token rejected; check SECRETARY_TOKEN")
            raise TokenRejected from exc
        if exc.code >= 500:
            log.error("Backend error HTTP %s for %d chars; will retry", exc.code, len(text))
            return last_text
        log.error("Backend rejected %d chars with HTTP %s; not retrying", len(text), exc.code)
        return text
    except (OSError, TimeoutError) as exc:  # URLError and socket timeouts
        log.error("Could not reach backend (%s); will retry", type(exc).__name__)
        return last_text
    except ValueError:
        # Backend answered 2xx but the body was not JSON; the message was accepted.
        log.error("Backend response was not valid JSON for %d chars; not retrying", len(text))
        return text

    log.info("Sent %d chars: id=%s duplicate=%s", len(text), result.get("id"), result.get("duplicate"))
    return text


def _positive_float(value: str) -> float:
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid number: {value!r}")
    if not number > 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return number


def _http_url(value: str) -> str:
    # Without a scheme urllib raises ValueError, which poll_once would treat as "accepted" and drop the message.
    if not value.startswith(("http://", "https://")):
        raise argparse.ArgumentTypeError("must start with http:// or https://")
    return value


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send copied clipboard text to the Secretary backend.")
    parser.add_argument("--api-url", type=_http_url, default=DEFAULT_API_URL, help="backend base URL (default: %(default)s)")
    parser.add_argument("--chat-id", default=None, help="optional chat id to attach to every message")
    parser.add_argument(
        "--token-file",
        default=None,
        help=f"file whose first line is the ingest token (default: the {TOKEN_ENV_VAR} environment variable)",
    )
    parser.add_argument("--interval", type=_positive_float, default=1.0, help="poll interval in seconds (default: %(default)s)")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    """Run the collector; returns the process exit code (2 for a missing or rejected token)."""
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        token = load_token(args.token_file)
    except ValueError as exc:
        log.error("%s", exc)
        return 2

    import pyperclip  # lazy so tests do not need it installed

    def send(text: str) -> dict:
        return send_message(args.api_url, text, args.chat_id, token)

    try:
        last_text = pyperclip.paste()
    except Exception as exc:
        log.warning("Could not read clipboard at start-up (%s)", type(exc).__name__)
        last_text = None
    log.info("Ignoring text already on the clipboard at start-up. Watching %s (Ctrl+C to stop)", args.api_url)

    last_beat = None
    try:
        while True:
            if last_beat is None or time.monotonic() - last_beat >= HEARTBEAT_SECONDS:
                beat(lambda: send_heartbeat(args.api_url, token))
                last_beat = time.monotonic()
            last_text = poll_once(pyperclip.paste, send, last_text)
            time.sleep(args.interval)
    except TokenRejected:
        return 2
    except KeyboardInterrupt:
        log.info("Stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
